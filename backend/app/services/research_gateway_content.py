"""Read authorized native content without exposing operational payloads."""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor
from app.errors import ConflictError, NotFoundError
from app.models.acquisition import (
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    SourceReference,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_gateway import ArtifactReference, ResearchRunSpec
from app.queries.automatic_research import AutomaticResearchQueries
from app.schemas.v1.research_gateway_content import (
    EvidenceDetailDTO,
    EvidenceLocatorDTO,
    EvidenceSummaryDTO,
    ResearchContentDTO,
)
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.research_gateway import ResearchGateway
from app.services.research_gateway_artifacts import (
    native_artifacts,
    validated_gateway_source_bindings,
)
from app.services.research_gateway_assessment_review import read_assessment_review

_WARNINGS = (
    "系统生成的研究草案，未经人工审核。",
    "搜反证方向不等于已证实反证；当前支持、反证等关系来自检索任务方向，不代表已经验证的支持或反驳关系。",
    "用户材料不是独立核验，仍需合规外部来源交叉验证。",
)
_NOT_FOUND = "research conversation not found"
_EVIDENCE_LIMIT = 200
# Unknown query parameters can carry opaque credentials. Only known public
# document navigation parameters with plain values survive this display check.
_PUBLIC_QUERY_KEYS = frozenset({"id", "page", "p", "year", "date", "lang", "language"})
_READ_ERRORS = (
    ConflictError,
    NotFoundError,
    ValueError,
    TypeError,
    KeyError,
    ValidationError,
)


def _public_source_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    decoded = unquote(value)
    if (
        any(ord(char) < 33 or ord(char) == 127 for char in decoded)
        or "\\" in decoded
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            normalized = host.rstrip(".").lower()
            if (
                any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in normalized.split(".")
                )
                or "." not in normalized
                or normalized.endswith(
                    (".localhost", ".local", ".internal", ".lan", ".home", ".test")
                )
                or not re.search(r"\.[a-z]{2,}$", normalized)
            ):
                return None
        else:
            if not address.is_global:
                return None
        for key, item in parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True
        ):
            if key.lower() not in _PUBLIC_QUERY_KEYS or not re.fullmatch(
                r"[a-zA-Z0-9_.-]{1,128}", item
            ):
                return None
        if parsed.fragment and not re.fullmatch(r"page=\d{1,6}", parsed.fragment):
            return None
    except (ValueError, UnicodeError):
        return None
    return value


def _coordinate(value: object, *, minimum: int) -> int | None:
    return value if type(value) is int and minimum <= value <= 1_000_000 else None


def _locator(span: SourceSpan) -> EvidenceLocatorDTO:
    legacy = span.locator if isinstance(span.locator, dict) else {}
    v1 = span.locator_v1 if isinstance(span.locator_v1, dict) else {}
    if v1.get("schema") != "source-locator/v1":
        v1 = {}
    extra = v1.get("extra") if isinstance(v1.get("extra"), dict) else {}
    page = v1.get("page", legacy.get("page", legacy.get("page_no")))
    paragraph = extra.get("paragraph", legacy.get("paragraph"))
    return EvidenceLocatorDTO(
        page=_coordinate(page, minimum=1), paragraph=_coordinate(paragraph, minimum=0)
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ResearchGatewayContent:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _authorize(
        self, actor: ResearchActor, conversation_id: UUID, run_spec_id: UUID
    ) -> tuple[ResearchRunSpec, ResearchRun, list[ArtifactReference]]:
        # Reads may share a Session across polls. Never use its prior identity
        # map as evidence of current ownership, source rights or admission.
        self._session.expire_all()
        spec = ResearchGateway(self._session).read_run_spec(actor, run_spec_id)
        if spec.conversation_id != conversation_id:
            raise NotFoundError(_NOT_FOUND)
        run = self._session.get(ResearchRun, spec.native_run_id)
        if run is None or run.research_case_id != spec.native_case_id:
            raise NotFoundError(_NOT_FOUND)
        try:
            refs = native_artifacts(self._session, spec)
        except _READ_ERRORS:
            # A stale case lifecycle or malformed frozen lineage cannot turn
            # into a current-case result or a detailed provider exception.
            refs = []
        return spec, run, refs

    def _evidence_rows(self, spec: ResearchRunSpec, ids: set[UUID], *, limit: int):
        if not ids:
            return []
        return list(
            self._session.execute(
                select(
                    EvidenceLink,
                    SourceStatement,
                    SourceSpan,
                    DocumentVersion,
                    AtomicClaimCandidate,
                    AutomaticAdmissionDecision,
                    Thesis,
                    SourceReference,
                )
                .join(
                    SourceStatement,
                    EvidenceLink.source_statement_id == SourceStatement.id,
                )
                .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
                .join(
                    DocumentVersion,
                    SourceSpan.document_version_id == DocumentVersion.id,
                )
                .join(
                    AutomaticAdmissionDecision,
                    EvidenceLink.automatic_admission_decision_id
                    == AutomaticAdmissionDecision.id,
                )
                .join(
                    AtomicClaimCandidate,
                    AutomaticAdmissionDecision.candidate_id == AtomicClaimCandidate.id,
                )
                .join(Thesis, EvidenceLink.thesis_id == Thesis.id)
                .join(
                    RetrievalArtifact,
                    AutomaticAdmissionDecision.retrieval_artifact_id
                    == RetrievalArtifact.id,
                )
                .join(
                    SourceReference,
                    RetrievalArtifact.source_reference_id == SourceReference.id,
                )
                .where(
                    EvidenceLink.id.in_(ids),
                    Thesis.research_case_id == spec.native_case_id,
                )
                .order_by(EvidenceLink.created_at, EvidenceLink.id)
                .limit(limit)
            )
        )

    def _tasks_by_job(self, spec: ResearchRunSpec) -> dict[str, ResearchTask]:
        run = self._session.get(ResearchRun, spec.native_run_id)
        if run is None or run.research_case_id != spec.native_case_id:
            raise NotFoundError(_NOT_FOUND)
        scope = load_automatic_research_scope(self._session, run)
        bindings = validated_gateway_source_bindings(self._session, spec, run, scope)
        # The validated matrix owns this relation. Result tasks may contain
        # arbitrary additional JSON fields and cannot become source tasks.
        return {
            str(job.id): bindings.tasks_by_id[task_id]
            for task_id, job in bindings.jobs_by_task_id.items()
        }

    @staticmethod
    def _summary(row, tasks: dict[str, ResearchTask]) -> EvidenceSummaryDTO:
        link, statement, _span, document, _candidate, decision, thesis, reference = row
        return EvidenceSummaryDTO(
            evidence_link_id=str(link.id),
            task_id=str(tasks[str(decision.job_id)].id),
            thesis_id=str(thesis.id),
            thesis_statement=thesis.statement,
            statement=statement.normalized_text,
            document_version_id=str(document.id),
            title=document.title or reference.title or "未命名来源",
            source_authority=document.source_authority,
            source_url=_public_source_url(document.source_url),
            published_at=_utc(document.published_at),
            observed_period=statement.observed_period,
            relationship=link.role,
        )

    def read_research(
        self, actor: ResearchActor, conversation_id: UUID, run_spec_id: UUID
    ) -> ResearchContentDTO:
        spec, run, refs = self._authorize(actor, conversation_id, run_spec_id)
        evidence_ids = {ref.id for ref in refs if ref.kind == "evidence_link"}
        total_evidence = len(evidence_ids)
        projection_authorized = True
        try:
            rows = self._evidence_rows(spec, evidence_ids, limit=_EVIDENCE_LIMIT)
            if len(rows) != min(total_evidence, _EVIDENCE_LIMIT):
                raise NotFoundError(_NOT_FOUND)
            tasks = self._tasks_by_job(spec) if rows else {}
            evidence = [self._summary(row, tasks) for row in rows]
        except _READ_ERRORS:
            # A worker may advance bindings between the native authorization
            # and this projection. Never combine a missing summary with the
            # previously authorized report or expose a partial stale read.
            evidence, refs = [], []
            total_evidence = 0
            projection_authorized = False
        state, reason = {
            "failed": ("failed", "execution_failed"),
            "cancelled": ("cancelled", "execution_cancelled"),
            "succeeded": ("withheld", "result_not_authorized"),
        }.get(run.status, ("pending", "result_pending"))
        if not projection_authorized:
            state, reason = "withheld", "result_not_authorized"
        draft_id, result = None, None
        authorized_draft = next((ref for ref in refs if ref.kind == "draft"), None)
        if authorized_draft is not None:
            try:
                view = AutomaticResearchQueries(self._session).get(
                    spec.native_case_id, actor.tenant_id
                )
            except _READ_ERRORS:
                view = None
            if (
                view is not None
                and view.run_id == str(spec.native_run_id)
                and view.status == "completed"
                and view.result is not None
            ):
                result = view.result.model_copy(
                    update={
                        "sources": [
                            source.model_copy(
                                update={"url": _public_source_url(source.url)}
                            )
                            for source in view.result.sources
                        ]
                    }
                )
                draft_id = str(authorized_draft.id)
                state, reason = "available", None
            else:
                state, reason = "withheld", "result_not_authorized"
        return ResearchContentDTO(
            conversation_id=str(conversation_id),
            run_spec_id=str(run_spec_id),
            state=state,
            reason_code=reason,
            draft_id=draft_id,
            result=result,
            assessment_review=(
                read_assessment_review(
                    self._session, spec, run, evidence,
                    truncated=total_evidence > _EVIDENCE_LIMIT,
                )
                if result is not None else None
            ),
            evidence=evidence,
            total_evidence=total_evidence,
            truncated=total_evidence > _EVIDENCE_LIMIT,
            warnings=list(_WARNINGS),
        )

    def read_evidence(
        self,
        actor: ResearchActor,
        conversation_id: UUID,
        run_spec_id: UUID,
        evidence_link_id: UUID,
    ) -> EvidenceDetailDTO:
        spec, _run, refs = self._authorize(actor, conversation_id, run_spec_id)
        if evidence_link_id not in {
            ref.id for ref in refs if ref.kind == "evidence_link"
        }:
            raise NotFoundError(_NOT_FOUND)
        try:
            rows = self._evidence_rows(spec, {evidence_link_id}, limit=1)
            if len(rows) != 1:
                raise NotFoundError(_NOT_FOUND)
            row = rows[0]
            summary = self._summary(row, self._tasks_by_job(spec))
            (
                _link,
                _statement,
                span,
                document,
                candidate,
                _decision,
                _thesis,
                _reference,
            ) = row
            return EvidenceDetailDTO(
                **summary.model_dump(),
                conversation_id=str(conversation_id),
                run_spec_id=str(run_spec_id),
                quote=candidate.quote,
                source_span_id=str(span.id),
                locator=_locator(span),
                content_sha256=document.content_sha256,
                acquired_at=_utc(document.acquired_at),
            )
        except _READ_ERRORS as exc:
            raise NotFoundError(_NOT_FOUND) from exc
