"""Transactional repository for lease-fenced governed acquisition jobs."""
from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import (
    DateTime,
    String,
    Text,
    Uuid,
    and_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.acquisition import (
    AcquisitionCoverageSnapshot,
    AcquisitionEvidenceView,
    AcquisitionJobRef,
    AcquisitionJobView,
    AcquisitionQueryPlanView,
    AcquisitionSearchOperationView,
    AcquisitionWorkflowLedgerCounts,
    AcquisitionWorkflowLedgerKey,
    AcquisitionWorkflowLedgerPage,
    AcquisitionWorkflowLedgerRecord,
    AdmittedEvidenceRef,
    WORKFLOW_LEDGER_STATUSES,
    WorkflowLedgerStatus,
)
from app.errors import ConflictError
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
)
from app.models.event_research import EventResearchScopeEvidenceAssignment
from app.models.research_orchestration import AcquisitionQueryPlan, AcquisitionSeries


class StaleLeaseError(Exception):
    """The caller no longer owns the lease fencing this mutation."""


class TerminalJobError(Exception):
    """A terminal acquisition job cannot transition again."""


@dataclass(frozen=True, slots=True)
class AcquisitionClaim:
    job_id: uuid.UUID
    lease_token: str
    lease_owner: str
    lease_expires_at: datetime
    attempt: int


_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})
_RUNNING_STAGES = frozenset(
    {"searching", "fetching", "freezing", "extracting", "admitting"}
)
_RUNNING_STAGE_ORDER = (
    "searching",
    "fetching",
    "freezing",
    "extracting",
    "admitting",
)
_TERMINAL_SOURCE_STAGES = {
    "succeeded": frozenset({"admitting"}),
    "partial": frozenset({"freezing", "extracting", "admitting"}),
    "failed": _RUNNING_STAGES,
    "cancelled": _RUNNING_STAGES,
}
_COUNTER_FIELDS = frozenset(
    {
        "reference_count",
        "fetched_count",
        "frozen_count",
        "admitted_count",
        "exception_count",
    }
)
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "api-key",
)
_CREDENTIAL_TEXT = re.compile(
    r"(?ix)"
    r"(?:\b(?:authorization|cookie|x-api-key)\s*[:=]\s*\S+)"
    r"|(?:\bbearer\s+\S+)"
    r"|(?:\b(?:password|token|secret|api[_-]?key)\s*[:=]\s*\S+)"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_persistable_json(value: Any, *, path: str = "$") -> None:
    """Reject credential-shaped keys and credential-bearing URLs recursively."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"sensitive persistence key rejected at {path}.{key}")
            validate_persistable_json(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            validate_persistable_json(child, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    parsed = urlsplit(value)
    if not (parsed.scheme and parsed.netloc):
        return
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"URL userinfo rejected at {path}")
    for query_key, _query_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = query_key.casefold()
        if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
            raise ValueError(f"sensitive URL query parameter rejected at {path}")


def validate_persistable_text(value: str | None, *, path: str) -> None:
    """Reject narrowly defined credential syntax and unsafe URLs in diagnostics."""
    if value is None:
        return
    validate_persistable_json(value, path=path)
    if _CREDENTIAL_TEXT.search(value):
        raise ValueError(f"credential-shaped persistence text rejected at {path}")


class AcquisitionRepository:
    """Own lease-fenced state transitions and append-only event ordering.

    The caller owns commit and rollback. Each mutating method writes its state
    transition and event in the caller's current transaction; callers must
    commit that transaction before starting external work.
    """

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session = session
        self._clock = clock

    def workflow_ledger(
        self, scope_version_id: uuid.UUID
    ) -> tuple[AcquisitionWorkflowLedgerRecord, ...]:
        """Batch project every acquisition fact for one frozen scope."""
        job_ids = select(AcquisitionQueryPlan.acquisition_job_id).join(
            AcquisitionSeries,
            AcquisitionSeries.id == AcquisitionQueryPlan.series_id,
        ).where(AcquisitionSeries.scope_version_id == scope_version_id)

        attempt_rows = list(
            self._session.scalars(
                select(AcquisitionAttempt)
                .where(AcquisitionAttempt.job_id.in_(job_ids))
                .order_by(AcquisitionAttempt.started_at, AcquisitionAttempt.id)
            )
        )
        source_rows = list(
            self._session.execute(
                select(
                    SourceReference,
                    RetrievalArtifact,
                    RetrievalArtifactDocument,
                )
                .outerjoin(
                    RetrievalArtifact,
                    RetrievalArtifact.source_reference_id == SourceReference.id,
                )
                .outerjoin(
                    RetrievalArtifactDocument,
                    RetrievalArtifactDocument.retrieval_artifact_id
                    == RetrievalArtifact.id,
                )
                .where(SourceReference.job_id.in_(job_ids))
                .order_by(SourceReference.created_at, SourceReference.id)
            )
        )
        decision_rows = list(
            self._session.execute(
                select(
                    AutomaticAdmissionDecision,
                    EventResearchScopeEvidenceAssignment,
                    EvidenceLink,
                )
                .outerjoin(
                    EventResearchScopeEvidenceAssignment,
                    (
                        EventResearchScopeEvidenceAssignment.automatic_admission_decision_id
                        == AutomaticAdmissionDecision.id
                    )
                    & (
                        EventResearchScopeEvidenceAssignment.scope_version_id
                        == scope_version_id
                    ),
                )
                .outerjoin(
                    EvidenceLink,
                    EvidenceLink.id
                    == EventResearchScopeEvidenceAssignment.evidence_link_id,
                )
                .where(AutomaticAdmissionDecision.job_id.in_(job_ids))
                .order_by(
                    AutomaticAdmissionDecision.created_at,
                    AutomaticAdmissionDecision.id,
                )
            )
        )
        exception_rows = list(
            self._session.scalars(
                select(AcquisitionException)
                .where(AcquisitionException.job_id.in_(job_ids))
                .order_by(AcquisitionException.created_at, AcquisitionException.id)
            )
        )
        manual_mapping_rows = list(
            self._session.execute(
                select(EventResearchScopeEvidenceAssignment, EvidenceLink)
                .join(EvidenceLink, EvidenceLink.id == EventResearchScopeEvidenceAssignment.evidence_link_id)
                .where(
                    EventResearchScopeEvidenceAssignment.scope_version_id == scope_version_id,
                    EventResearchScopeEvidenceAssignment.automatic_admission_decision_id.is_(None),
                )
                .order_by(EventResearchScopeEvidenceAssignment.created_at, EventResearchScopeEvidenceAssignment.id)
            )
        )
        source_chain_rows = {
            (reference.id, artifact.id if artifact else None, binding.id if binding else None): (
                reference,
                artifact,
                binding,
            )
            for reference, artifact, binding in source_rows
        }.values()
        references = {
            reference.id: reference
            for reference, _artifact, _binding in source_chain_rows
        }
        artifacts = {
            artifact.id: artifact
            for _reference, artifact, _binding in source_chain_rows
            if artifact is not None
        }
        binding_by_artifact = {
            binding.retrieval_artifact_id: binding
            for _reference, _artifact, binding in source_chain_rows
            if binding is not None
        }
        records: dict[tuple[str, uuid.UUID], AcquisitionWorkflowLedgerRecord] = {}

        def add(record: AcquisitionWorkflowLedgerRecord) -> None:
            records[(record.record_type, record.record_id)] = record

        for attempt in attempt_rows:
            if attempt.operation != "fetch" or attempt.finished_at is not None:
                continue
            reference_id = (attempt.safe_metadata or {}).get("source_reference_id")
            try:
                reference = references.get(uuid.UUID(str(reference_id)))
            except (TypeError, ValueError, AttributeError):
                reference = None
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=attempt.id,
                    record_type="acquisition_attempt",
                    status="fetching",
                    reason="资料获取请求已经开始，尚未形成冻结原件。",
                    reason_code="fetch_in_progress",
                    job_id=attempt.job_id,
                    recorded_at=attempt.started_at,
                    adapter_key=attempt.adapter_key,
                    attempt_id=attempt.id,
                    source_url=reference.canonical_url if reference else None,
                    source_role=reference.source_role if reference else None,
                )
            )

        for reference, artifact, binding in source_chain_rows:
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=reference.id,
                    record_type="source_reference",
                    status="search_candidate",
                    reason="检索返回了可获取的来源候选。",
                    reason_code="source_reference_discovered",
                    job_id=reference.job_id,
                    recorded_at=reference.created_at,
                    adapter_key=reference.adapter_key,
                    source_url=reference.canonical_url,
                    source_role=reference.source_role,
                )
            )
            if artifact is None:
                continue
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=artifact.id,
                    record_type="retrieval_artifact",
                    status="frozen",
                    reason="外部资料已经按内容哈希冻结。",
                    reason_code="retrieval_artifact_frozen",
                    job_id=reference.job_id,
                    recorded_at=artifact.retrieved_at,
                    adapter_key=reference.adapter_key,
                    attempt_id=artifact.attempt_id,
                    source_url=reference.canonical_url,
                    final_url=artifact.final_url,
                    retrieved_at=artifact.retrieved_at,
                    content_sha256=artifact.content_sha256,
                    source_role=reference.source_role,
                )
            )
            if binding is not None and binding.relation in {
                "content_duplicate",
                "publication_duplicate",
            }:
                add(
                    AcquisitionWorkflowLedgerRecord(
                        record_id=binding.id,
                        record_type="retrieval_artifact_document",
                        status="deduplicated",
                        reason="冻结资料与已有文档重复，已合并到同一发布记录。",
                        reason_code=binding.relation,
                        job_id=reference.job_id,
                        recorded_at=binding.created_at,
                        adapter_key=reference.adapter_key,
                        attempt_id=artifact.attempt_id,
                        source_url=reference.canonical_url,
                        final_url=artifact.final_url,
                        retrieved_at=artifact.retrieved_at,
                        content_sha256=artifact.content_sha256,
                        publication_key=binding.publication_key,
                        dedup_relation=binding.relation,
                        source_role=reference.source_role,
                    )
                )

        for decision, assignment, link in decision_rows:
            artifact = artifacts.get(decision.retrieval_artifact_id)
            reference = references.get(artifact.source_reference_id) if artifact else None
            binding = binding_by_artifact.get(artifact.id) if artifact else None
            status = "admitted" if decision.outcome == "admitted" else "quarantined"
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=decision.id,
                    record_type="automatic_admission_decision",
                    status=status,
                    reason=(
                        "资料通过冻结策略和自动准入门禁。"
                        if status == "admitted"
                        else "资料未通过自动准入门禁，已进入隔离区。"
                    ),
                    reason_code=("automatic_admission_passed" if status == "admitted" else "automatic_admission_quarantined"),
                    job_id=decision.job_id,
                    recorded_at=decision.created_at,
                    adapter_key=reference.adapter_key if reference else None,
                    attempt_id=artifact.attempt_id if artifact else None,
                    source_url=reference.canonical_url if reference else None,
                    final_url=artifact.final_url if artifact else None,
                    retrieved_at=artifact.retrieved_at if artifact else None,
                    content_sha256=artifact.content_sha256 if artifact else None,
                    publication_key=binding.publication_key if binding else None,
                    dedup_relation=binding.relation if binding else None,
                    admission_outcome=decision.outcome,
                    review_state=link.review_state if link else None,
                    evidence_link_id=link.id if link else None,
                    role=link.role if link else None,
                    source_role=reference.source_role if reference else None,
                    mapping_disposition=(
                        assignment.disposition if assignment else None
                    ),
                    mapping_kind=(
                        assignment.assignment_kind if assignment else None
                    ),
                )
            )

        for assignment, link in manual_mapping_rows:
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=link.id,
                    record_type="evidence_link",
                    status="admitted",
                    reason="人工审核证据已映射到当前研究范围。",
                    reason_code="reviewed_evidence_mapped",
                    job_id=None,
                    recorded_at=assignment.created_at,
                    admission_outcome="reviewed",
                    review_state=link.review_state,
                    evidence_link_id=link.id,
                    role=link.role,
                    mapping_disposition=assignment.disposition,
                    mapping_kind=assignment.assignment_kind,
                )
            )

        for exception in exception_rows:
            if exception.reason_code == "automatic_admission_quarantined":
                status = "quarantined"
            elif "conflict" in exception.reason_code:
                status = "conflicted"
            else:
                status = "skipped"
            artifact = artifacts.get(exception.retrieval_artifact_id)
            reference = references.get(exception.source_reference_id)
            if reference is None and artifact is not None:
                reference = references.get(artifact.source_reference_id)
            detail = exception.detail_json if isinstance(exception.detail_json, dict) else {}
            reason = detail.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                reason = f"资料处理记录：{exception.reason_code}。"
            add(
                AcquisitionWorkflowLedgerRecord(
                    record_id=exception.id,
                    record_type="acquisition_exception",
                    status=status,
                    reason=reason,
                    reason_code=exception.reason_code,
                    job_id=exception.job_id,
                    recorded_at=exception.created_at,
                    adapter_key=reference.adapter_key if reference else None,
                    attempt_id=artifact.attempt_id if artifact else None,
                    source_url=reference.canonical_url if reference else None,
                    final_url=artifact.final_url if artifact else None,
                    retrieved_at=artifact.retrieved_at if artifact else None,
                    content_sha256=artifact.content_sha256 if artifact else None,
                    source_role=reference.source_role if reference else None,
                )
            )
        return tuple(
            sorted(
                records.values(),
                key=lambda item: (
                    item.recorded_at,
                    item.record_type,
                    str(item.record_id),
                ),
            )
        )

    def workflow_ledger_page(
        self,
        scope_version_id: uuid.UUID,
        *,
        status: WorkflowLedgerStatus | None,
        after: AcquisitionWorkflowLedgerKey | None,
        high_watermark: AcquisitionWorkflowLedgerKey | None,
        limit: int,
    ) -> AcquisitionWorkflowLedgerPage:
        """Read one bounded keyset page without materializing ledger history."""
        if limit < 1:
            raise ValueError("workflow ledger limit must be positive")
        ledger = self._workflow_ledger_projection(scope_version_id).subquery()
        visible = select(ledger)
        if status is not None:
            visible = visible.where(ledger.c.status == status)
        visible = visible.subquery()

        snapshot_high = high_watermark
        if snapshot_high is None:
            high_row = self._session.execute(
                select(
                    visible.c.recorded_at,
                    visible.c.record_type,
                    visible.c.record_id,
                )
                .order_by(
                    visible.c.recorded_at.desc(),
                    visible.c.record_type.desc(),
                    visible.c.record_id.desc(),
                )
                .limit(1)
            ).one_or_none()
            if high_row is not None:
                snapshot_high = (
                    _aware_utc(high_row.recorded_at),
                    high_row.record_type,
                    str(high_row.record_id),
                )

        if snapshot_high is None:
            return AcquisitionWorkflowLedgerPage(
                records=(),
                counts=self._empty_workflow_ledger_counts(),
                high_watermark=None,
                has_more=False,
            )

        snapshot = select(visible).where(
            self._ledger_key_at_or_before(visible, snapshot_high)
        ).subquery()
        count_row = self._session.execute(
            select(
                func.count().label("total"),
                func.count(
                    func.distinct(
                        case(
                            (
                                snapshot.c.review_state == "reviewed",
                                snapshot.c.evidence_link_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("reviewed"),
                func.count(
                    func.distinct(
                        case(
                            (
                                snapshot.c.review_state
                                == "automatically_admitted",
                                snapshot.c.evidence_link_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("automatically_admitted"),
                *[
                    func.sum(
                        case((snapshot.c.status == item, 1), else_=0)
                    ).label(item)
                    for item in WORKFLOW_LEDGER_STATUSES
                ],
            )
        ).one()
        statement = select(snapshot)
        if after is not None:
            statement = statement.where(self._ledger_key_after(snapshot, after))
        rows = list(
            self._session.execute(
                statement.order_by(
                    snapshot.c.recorded_at,
                    snapshot.c.record_type,
                    snapshot.c.record_id,
                ).limit(limit + 1)
            ).mappings()
        )
        records = self._workflow_ledger_records_from_rows(rows[:limit])
        return AcquisitionWorkflowLedgerPage(
            records=records,
            counts=AcquisitionWorkflowLedgerCounts(
                total=int(count_row.total or 0),
                reviewed=int(count_row.reviewed or 0),
                automatically_admitted=int(
                    count_row.automatically_admitted or 0
                ),
                by_status={
                    item: int(getattr(count_row, item) or 0)
                    for item in WORKFLOW_LEDGER_STATUSES
                },
            ),
            high_watermark=snapshot_high,
            has_more=len(rows) > limit,
        )

    def workflow_ledger_for_evidence_links(
        self,
        scope_version_id: uuid.UUID,
        evidence_link_ids: tuple[uuid.UUID, ...],
    ) -> tuple[AcquisitionWorkflowLedgerRecord, ...]:
        """Resolve only explicitly cited ledger records for report citations."""
        ordered_ids = tuple(dict.fromkeys(evidence_link_ids))
        if not ordered_ids:
            return ()
        ledger = self._workflow_ledger_projection(scope_version_id).subquery()
        rows = list(
            self._session.execute(
                select(ledger)
                .where(ledger.c.evidence_link_id.in_(ordered_ids))
                .order_by(
                    ledger.c.recorded_at,
                    ledger.c.record_type,
                    ledger.c.record_id,
                )
                .limit(len(ordered_ids))
            ).mappings()
        )
        return self._workflow_ledger_records_from_rows(rows)

    @staticmethod
    def _empty_workflow_ledger_counts() -> AcquisitionWorkflowLedgerCounts:
        return AcquisitionWorkflowLedgerCounts(
            total=0,
            reviewed=0,
            automatically_admitted=0,
            by_status={item: 0 for item in WORKFLOW_LEDGER_STATUSES},
        )

    @staticmethod
    def _ledger_key_after(table, key: AcquisitionWorkflowLedgerKey):
        recorded_at, record_type, record_id = key
        parsed_id = uuid.UUID(record_id)
        return or_(
            table.c.recorded_at > recorded_at,
            and_(
                table.c.recorded_at == recorded_at,
                table.c.record_type > record_type,
            ),
            and_(
                table.c.recorded_at == recorded_at,
                table.c.record_type == record_type,
                table.c.record_id > parsed_id,
            ),
        )

    @staticmethod
    def _ledger_key_at_or_before(table, key: AcquisitionWorkflowLedgerKey):
        recorded_at, record_type, record_id = key
        parsed_id = uuid.UUID(record_id)
        return or_(
            table.c.recorded_at < recorded_at,
            and_(
                table.c.recorded_at == recorded_at,
                table.c.record_type < record_type,
            ),
            and_(
                table.c.recorded_at == recorded_at,
                table.c.record_type == record_type,
                table.c.record_id <= parsed_id,
            ),
        )

    def _workflow_ledger_projection(self, scope_version_id: uuid.UUID):
        job_ids = select(AcquisitionQueryPlan.acquisition_job_id).join(
            AcquisitionSeries,
            AcquisitionSeries.id == AcquisitionQueryPlan.series_id,
        ).where(AcquisitionSeries.scope_version_id == scope_version_id)

        nullable_types = {
            "job_id": Uuid(),
            "adapter_key": String(128),
            "attempt_id": Uuid(),
            "source_url": Text(),
            "final_url": Text(),
            "retrieved_at": DateTime(timezone=True),
            "content_sha256": String(64),
            "publication_key": String(64),
            "dedup_relation": String(32),
            "admission_outcome": String(32),
            "review_state": String(32),
            "evidence_link_id": Uuid(),
            "role": String(32),
            "source_role": String(64),
            "mapping_disposition": String(16),
            "mapping_kind": String(16),
            "attempt_source_reference_id": String(36),
        }

        def absent(name: str):
            return cast(literal(None), nullable_types[name]).label(name)

        attempts = select(
            AcquisitionAttempt.id.label("record_id"),
            literal("acquisition_attempt").label("record_type"),
            literal("fetching").label("status"),
            literal("资料获取请求已经开始，尚未形成冻结原件。").label("reason"),
            literal("fetch_in_progress").label("reason_code"),
            AcquisitionAttempt.job_id.label("job_id"),
            AcquisitionAttempt.started_at.label("recorded_at"),
            AcquisitionAttempt.adapter_key.label("adapter_key"),
            AcquisitionAttempt.id.label("attempt_id"),
            absent("source_url"),
            absent("final_url"),
            absent("retrieved_at"),
            absent("content_sha256"),
            absent("publication_key"),
            absent("dedup_relation"),
            absent("admission_outcome"),
            absent("review_state"),
            absent("evidence_link_id"),
            absent("role"),
            absent("source_role"),
            absent("mapping_disposition"),
            absent("mapping_kind"),
            AcquisitionAttempt.safe_metadata["source_reference_id"]
            .as_string()
            .label("attempt_source_reference_id"),
        ).where(
            AcquisitionAttempt.job_id.in_(job_ids),
            AcquisitionAttempt.operation == "fetch",
            AcquisitionAttempt.finished_at.is_(None),
        )
        references = select(
            SourceReference.id.label("record_id"),
            literal("source_reference").label("record_type"),
            literal("search_candidate").label("status"),
            literal("检索返回了可获取的来源候选。").label("reason"),
            literal("source_reference_discovered").label("reason_code"),
            SourceReference.job_id.label("job_id"),
            SourceReference.created_at.label("recorded_at"),
            SourceReference.adapter_key.label("adapter_key"),
            absent("attempt_id"),
            SourceReference.canonical_url.label("source_url"),
            absent("final_url"),
            absent("retrieved_at"),
            absent("content_sha256"),
            absent("publication_key"),
            absent("dedup_relation"),
            absent("admission_outcome"),
            absent("review_state"),
            absent("evidence_link_id"),
            absent("role"),
            SourceReference.source_role.label("source_role"),
            absent("mapping_disposition"),
            absent("mapping_kind"),
            absent("attempt_source_reference_id"),
        ).where(SourceReference.job_id.in_(job_ids))
        artifacts = (
            select(
                RetrievalArtifact.id.label("record_id"),
                literal("retrieval_artifact").label("record_type"),
                literal("frozen").label("status"),
                literal("外部资料已经按内容哈希冻结。").label("reason"),
                literal("retrieval_artifact_frozen").label("reason_code"),
                SourceReference.job_id.label("job_id"),
                RetrievalArtifact.retrieved_at.label("recorded_at"),
                SourceReference.adapter_key.label("adapter_key"),
                RetrievalArtifact.attempt_id.label("attempt_id"),
                SourceReference.canonical_url.label("source_url"),
                RetrievalArtifact.final_url.label("final_url"),
                RetrievalArtifact.retrieved_at.label("retrieved_at"),
                RetrievalArtifact.content_sha256.label("content_sha256"),
                absent("publication_key"),
                absent("dedup_relation"),
                absent("admission_outcome"),
                absent("review_state"),
                absent("evidence_link_id"),
                absent("role"),
                SourceReference.source_role.label("source_role"),
                absent("mapping_disposition"),
                absent("mapping_kind"),
                absent("attempt_source_reference_id"),
            )
            .join(
                SourceReference,
                SourceReference.id == RetrievalArtifact.source_reference_id,
            )
            .where(SourceReference.job_id.in_(job_ids))
        )
        bindings = (
            select(
                RetrievalArtifactDocument.id.label("record_id"),
                literal("retrieval_artifact_document").label("record_type"),
                literal("deduplicated").label("status"),
                literal(
                    "冻结资料与已有文档重复，已合并到同一发布记录。"
                ).label("reason"),
                RetrievalArtifactDocument.relation.label("reason_code"),
                SourceReference.job_id.label("job_id"),
                RetrievalArtifactDocument.created_at.label("recorded_at"),
                SourceReference.adapter_key.label("adapter_key"),
                RetrievalArtifact.attempt_id.label("attempt_id"),
                SourceReference.canonical_url.label("source_url"),
                RetrievalArtifact.final_url.label("final_url"),
                RetrievalArtifact.retrieved_at.label("retrieved_at"),
                RetrievalArtifact.content_sha256.label("content_sha256"),
                RetrievalArtifactDocument.publication_key.label("publication_key"),
                RetrievalArtifactDocument.relation.label("dedup_relation"),
                absent("admission_outcome"),
                absent("review_state"),
                absent("evidence_link_id"),
                absent("role"),
                SourceReference.source_role.label("source_role"),
                absent("mapping_disposition"),
                absent("mapping_kind"),
                absent("attempt_source_reference_id"),
            )
            .join(
                RetrievalArtifact,
                RetrievalArtifact.id
                == RetrievalArtifactDocument.retrieval_artifact_id,
            )
            .join(
                SourceReference,
                SourceReference.id == RetrievalArtifact.source_reference_id,
            )
            .where(
                SourceReference.job_id.in_(job_ids),
                RetrievalArtifactDocument.relation.in_(
                    ("content_duplicate", "publication_duplicate")
                ),
            )
        )
        decisions = (
            select(
                AutomaticAdmissionDecision.id.label("record_id"),
                literal("automatic_admission_decision").label("record_type"),
                case(
                    (
                        AutomaticAdmissionDecision.outcome == "admitted",
                        "admitted",
                    ),
                    else_="quarantined",
                ).label("status"),
                case(
                    (
                        AutomaticAdmissionDecision.outcome == "admitted",
                        "资料通过冻结策略和自动准入门禁。",
                    ),
                    else_="资料未通过自动准入门禁，已进入隔离区。",
                ).label("reason"),
                case(
                    (
                        AutomaticAdmissionDecision.outcome == "admitted",
                        "automatic_admission_passed",
                    ),
                    else_="automatic_admission_quarantined",
                ).label("reason_code"),
                AutomaticAdmissionDecision.job_id.label("job_id"),
                AutomaticAdmissionDecision.created_at.label("recorded_at"),
                SourceReference.adapter_key.label("adapter_key"),
                RetrievalArtifact.attempt_id.label("attempt_id"),
                SourceReference.canonical_url.label("source_url"),
                RetrievalArtifact.final_url.label("final_url"),
                RetrievalArtifact.retrieved_at.label("retrieved_at"),
                RetrievalArtifact.content_sha256.label("content_sha256"),
                RetrievalArtifactDocument.publication_key.label("publication_key"),
                RetrievalArtifactDocument.relation.label("dedup_relation"),
                AutomaticAdmissionDecision.outcome.label("admission_outcome"),
                EvidenceLink.review_state.label("review_state"),
                EvidenceLink.id.label("evidence_link_id"),
                EvidenceLink.role.label("role"),
                SourceReference.source_role.label("source_role"),
                EventResearchScopeEvidenceAssignment.disposition.label(
                    "mapping_disposition"
                ),
                EventResearchScopeEvidenceAssignment.assignment_kind.label(
                    "mapping_kind"
                ),
                absent("attempt_source_reference_id"),
            )
            .join(
                RetrievalArtifact,
                RetrievalArtifact.id
                == AutomaticAdmissionDecision.retrieval_artifact_id,
            )
            .join(
                SourceReference,
                SourceReference.id == RetrievalArtifact.source_reference_id,
            )
            .outerjoin(
                RetrievalArtifactDocument,
                RetrievalArtifactDocument.retrieval_artifact_id
                == RetrievalArtifact.id,
            )
            .outerjoin(
                EventResearchScopeEvidenceAssignment,
                and_(
                    EventResearchScopeEvidenceAssignment.automatic_admission_decision_id
                    == AutomaticAdmissionDecision.id,
                    EventResearchScopeEvidenceAssignment.scope_version_id
                    == scope_version_id,
                ),
            )
            .outerjoin(
                EvidenceLink,
                EvidenceLink.id
                == EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
            .where(AutomaticAdmissionDecision.job_id.in_(job_ids))
        )
        manual_mappings = (
            select(
                EvidenceLink.id.label("record_id"),
                literal("evidence_link").label("record_type"),
                literal("admitted").label("status"),
                literal("人工审核证据已映射到当前研究范围。").label("reason"),
                literal("reviewed_evidence_mapped").label("reason_code"),
                absent("job_id"),
                EventResearchScopeEvidenceAssignment.created_at.label("recorded_at"),
                absent("adapter_key"),
                absent("attempt_id"),
                absent("source_url"),
                absent("final_url"),
                absent("retrieved_at"),
                absent("content_sha256"),
                absent("publication_key"),
                absent("dedup_relation"),
                literal("reviewed").label("admission_outcome"),
                EvidenceLink.review_state.label("review_state"),
                EvidenceLink.id.label("evidence_link_id"),
                EvidenceLink.role.label("role"),
                absent("source_role"),
                EventResearchScopeEvidenceAssignment.disposition.label(
                    "mapping_disposition"
                ),
                EventResearchScopeEvidenceAssignment.assignment_kind.label(
                    "mapping_kind"
                ),
                absent("attempt_source_reference_id"),
            )
            .join(
                EvidenceLink,
                EvidenceLink.id
                == EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
            .where(
                EventResearchScopeEvidenceAssignment.scope_version_id
                == scope_version_id,
                EventResearchScopeEvidenceAssignment.automatic_admission_decision_id.is_(
                    None
                ),
            )
        )
        exception_reference = SourceReference.__table__.alias("exception_reference")
        artifact_reference = SourceReference.__table__.alias("artifact_reference")
        exceptions = (
            select(
                AcquisitionException.id.label("record_id"),
                literal("acquisition_exception").label("record_type"),
                case(
                    (
                        AcquisitionException.reason_code
                        == "automatic_admission_quarantined",
                        "quarantined",
                    ),
                    (AcquisitionException.reason_code.contains("conflict"), "conflicted"),
                    else_="skipped",
                ).label("status"),
                AcquisitionException.detail_json["reason"].as_string().label("reason"),
                AcquisitionException.reason_code.label("reason_code"),
                AcquisitionException.job_id.label("job_id"),
                AcquisitionException.created_at.label("recorded_at"),
                func.coalesce(
                    exception_reference.c.adapter_key,
                    artifact_reference.c.adapter_key,
                ).label("adapter_key"),
                RetrievalArtifact.attempt_id.label("attempt_id"),
                func.coalesce(
                    exception_reference.c.canonical_url,
                    artifact_reference.c.canonical_url,
                ).label("source_url"),
                RetrievalArtifact.final_url.label("final_url"),
                RetrievalArtifact.retrieved_at.label("retrieved_at"),
                RetrievalArtifact.content_sha256.label("content_sha256"),
                absent("publication_key"),
                absent("dedup_relation"),
                absent("admission_outcome"),
                absent("review_state"),
                absent("evidence_link_id"),
                absent("role"),
                func.coalesce(
                    exception_reference.c.source_role,
                    artifact_reference.c.source_role,
                ).label("source_role"),
                absent("mapping_disposition"),
                absent("mapping_kind"),
                absent("attempt_source_reference_id"),
            )
            .outerjoin(
                RetrievalArtifact,
                RetrievalArtifact.id == AcquisitionException.retrieval_artifact_id,
            )
            .outerjoin(
                exception_reference,
                exception_reference.c.id
                == AcquisitionException.source_reference_id,
            )
            .outerjoin(
                artifact_reference,
                artifact_reference.c.id
                == RetrievalArtifact.source_reference_id,
            )
            .where(AcquisitionException.job_id.in_(job_ids))
        )
        return union_all(
            attempts,
            references,
            artifacts,
            bindings,
            decisions,
            manual_mappings,
            exceptions,
        )

    def _workflow_ledger_records_from_rows(
        self, rows
    ) -> tuple[AcquisitionWorkflowLedgerRecord, ...]:
        reference_ids: set[uuid.UUID] = set()
        for row in rows:
            raw_id = row.get("attempt_source_reference_id")
            try:
                reference_ids.add(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError, AttributeError):
                continue
        references = {}
        if reference_ids:
            references = {
                item.id: item
                for item in self._session.scalars(
                    select(SourceReference)
                    .where(SourceReference.id.in_(reference_ids))
                    .limit(len(reference_ids))
                )
            }
        records = []
        for row in rows:
            reference = None
            raw_reference_id = row.get("attempt_source_reference_id")
            try:
                reference = references.get(uuid.UUID(str(raw_reference_id)))
            except (TypeError, ValueError, AttributeError):
                pass
            reason = row["reason"]
            if not isinstance(reason, str) or not reason.strip():
                reason = f"资料处理记录：{row['reason_code']}。"
            records.append(
                AcquisitionWorkflowLedgerRecord(
                    record_id=row["record_id"],
                    record_type=row["record_type"],
                    status=row["status"],
                    reason=reason,
                    reason_code=row["reason_code"],
                    job_id=row["job_id"],
                    recorded_at=_aware_utc(row["recorded_at"]),
                    adapter_key=(
                        reference.adapter_key
                        if reference is not None
                        else row["adapter_key"]
                    ),
                    attempt_id=row["attempt_id"],
                    source_url=(
                        reference.canonical_url
                        if reference is not None
                        else row["source_url"]
                    ),
                    final_url=row["final_url"],
                    retrieved_at=(
                        _aware_utc(row["retrieved_at"])
                        if row["retrieved_at"] is not None
                        else None
                    ),
                    content_sha256=row["content_sha256"],
                    publication_key=row["publication_key"],
                    dedup_relation=row["dedup_relation"],
                    admission_outcome=row["admission_outcome"],
                    review_state=row["review_state"],
                    evidence_link_id=row["evidence_link_id"],
                    role=row["role"],
                    source_role=(
                        reference.source_role
                        if reference is not None
                        else row["source_role"]
                    ),
                    mapping_disposition=row["mapping_disposition"],
                    mapping_kind=row["mapping_kind"],
                )
            )
        return tuple(records)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acquisition clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _ref(job: AcquisitionJob) -> AcquisitionJobRef:
        return AcquisitionJobRef(id=job.id, status=job.status)

    @staticmethod
    def _view(job: AcquisitionJob) -> AcquisitionJobView:
        snapshot = job.request_snapshot if isinstance(job.request_snapshot, dict) else {}
        try:
            scope_version_id = uuid.UUID(str(snapshot.get("scope_version_id")))
        except (TypeError, ValueError):
            scope_version_id = None
        goal_value = snapshot.get("goal_id")
        goal_id = goal_value.strip() if isinstance(goal_value, str) else None
        round_value = snapshot.get("round")
        acquisition_round = (
            round_value
            if isinstance(round_value, int)
            and not isinstance(round_value, bool)
            and round_value >= 1
            else None
        )
        return AcquisitionJobView(
            id=job.id,
            status=job.status,
            stage=job.stage,
            attempt=job.attempt,
            tenant_id=job.tenant_id,
            research_case_id=job.research_case_id,
            thesis_id=job.thesis_id,
            research_run_id=job.research_run_id,
            scope_version_id=scope_version_id,
            goal_id=goal_id,
            acquisition_round=acquisition_round,
        )

    def create_or_get(
        self,
        *,
        tenant_id: str,
        research_case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        research_run_id: uuid.UUID | None,
        idempotency_key: str,
        request_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
        creation_payload: dict[str, Any] | None = None,
    ) -> AcquisitionJobRef:
        payload = creation_payload or {}
        validate_persistable_json(request_snapshot)
        validate_persistable_json(policy_snapshot)
        validate_persistable_json(payload)
        existing = self._by_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            self._require_same_frozen_request(
                existing,
                research_case_id=research_case_id,
                thesis_id=thesis_id,
                research_run_id=research_run_id,
                request_snapshot=request_snapshot,
                policy_snapshot=policy_snapshot,
            )
            return self._ref(existing)

        now = self._now()
        job = AcquisitionJob(
            tenant_id=tenant_id,
            research_case_id=research_case_id,
            thesis_id=thesis_id,
            research_run_id=research_run_id,
            idempotency_key=idempotency_key,
            request_snapshot=request_snapshot,
            policy_snapshot=policy_snapshot,
            status="queued",
            stage="queued",
            attempt=0,
            reference_count=0,
            fetched_count=0,
            frozen_count=0,
            admitted_count=0,
            exception_count=0,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(job)
                self._session.flush()
                self._append_event(
                    job,
                    message="acquisition queued",
                    payload=payload,
                    now=now,
                )
                self._session.flush()
        except IntegrityError:
            existing = self._by_idempotency(tenant_id, idempotency_key)
            if existing is None:
                raise
            self._require_same_frozen_request(
                existing,
                research_case_id=research_case_id,
                thesis_id=thesis_id,
                research_run_id=research_run_id,
                request_snapshot=request_snapshot,
                policy_snapshot=policy_snapshot,
            )
            return self._ref(existing)
        return self._ref(job)

    def query_plan_for_job(
        self, job_id: uuid.UUID
    ) -> AcquisitionQueryPlan | None:
        return self._session.scalar(
            select(AcquisitionQueryPlan).where(
                AcquisitionQueryPlan.acquisition_job_id == job_id
            )
        )

    def query_plan(self, plan_id: uuid.UUID) -> AcquisitionQueryPlan | None:
        return self._session.get(AcquisitionQueryPlan, plan_id)

    def series(self, series_id: uuid.UUID) -> AcquisitionSeries | None:
        return self._session.get(AcquisitionSeries, series_id)

    def create_or_get_with_plan(
        self,
        *,
        tenant_id: str,
        research_case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        research_run_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        goal_id: str,
        acquisition_round: int,
        idempotency_key: str,
        request_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
        plan_id: uuid.UUID,
        planner_version: str,
        ordered_queries: list[dict[str, Any]],
        previous_query_plan_id: uuid.UUID | None,
        expansion_trigger: str | None,
        diff_json: dict[str, Any] | None,
        creation_payload: dict[str, Any] | None = None,
    ) -> AcquisitionJobRef:
        """Atomically freeze a goal series, one round job, and its query plan."""
        payload = creation_payload or {}
        for value in (request_snapshot, policy_snapshot, ordered_queries, payload):
            validate_persistable_json(value)
        if diff_json is not None:
            validate_persistable_json(diff_json)

        existing_job = self._by_idempotency(tenant_id, idempotency_key)
        if existing_job is not None:
            self._require_same_planned_request(
                existing_job,
                research_case_id=research_case_id,
                thesis_id=thesis_id,
                research_run_id=research_run_id,
                scope_version_id=scope_version_id,
                goal_id=goal_id,
                acquisition_round=acquisition_round,
                request_snapshot=request_snapshot,
                policy_snapshot=policy_snapshot,
                planner_version=planner_version,
                ordered_queries=ordered_queries,
                previous_query_plan_id=previous_query_plan_id,
                expansion_trigger=expansion_trigger,
                diff_json=diff_json,
            )
            return self._ref(existing_job)

        series = self._session.scalar(
            select(AcquisitionSeries).where(
                AcquisitionSeries.tenant_id == tenant_id,
                AcquisitionSeries.research_case_id == research_case_id,
                AcquisitionSeries.research_run_id == research_run_id,
                AcquisitionSeries.scope_version_id == scope_version_id,
                AcquisitionSeries.thesis_id == thesis_id,
                AcquisitionSeries.goal_id == goal_id,
            )
        )
        now = self._now()
        if series is None:
            if acquisition_round != 1:
                raise ConflictError("later acquisition round has no goal series")
            series = AcquisitionSeries(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                research_case_id=research_case_id,
                research_run_id=research_run_id,
                scope_version_id=scope_version_id,
                thesis_id=thesis_id,
                goal_id=goal_id,
                created_at=now,
            )
        elif acquisition_round == 1:
            raise ConflictError("goal series already has an initial acquisition round")

        predecessor = None
        if acquisition_round > 1:
            predecessor = self.query_plan(previous_query_plan_id) if previous_query_plan_id else None
            if (
                predecessor is None
                or predecessor.series_id != series.id
                or predecessor.acquisition_round != acquisition_round - 1
            ):
                raise ConflictError("query plan predecessor is outside the goal series")

        job = AcquisitionJob(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            research_case_id=research_case_id,
            thesis_id=thesis_id,
            research_run_id=research_run_id,
            idempotency_key=idempotency_key,
            request_snapshot=request_snapshot,
            policy_snapshot=policy_snapshot,
            status="queued",
            stage="queued",
            attempt=0,
            reference_count=0,
            fetched_count=0,
            frozen_count=0,
            admitted_count=0,
            exception_count=0,
            created_at=now,
            updated_at=now,
        )
        plan = AcquisitionQueryPlan(
            id=plan_id,
            series_id=series.id,
            acquisition_job_id=job.id,
            acquisition_round=acquisition_round,
            goal_id=goal_id,
            planner_version=planner_version,
            policy_version=str(policy_snapshot.get("version") or ""),
            frozen_inputs_json={
                key: value for key, value in request_snapshot.items()
                if key != "query_plan_id"
            },
            ordered_queries_json=ordered_queries,
            previous_query_plan_id=previous_query_plan_id,
            previous_acquisition_round=(acquisition_round - 1 if predecessor else None),
            expansion_trigger=expansion_trigger,
            diff_json=diff_json,
            created_at=now,
        )
        try:
            with self._session.begin_nested():
                # Flush the aggregate roots first. SQLAlchemy has no ORM
                # relationships between these append-only records from which
                # to infer unit-of-work ordering, while PostgreSQL enforces the
                # job/series foreign keys immediately. The savepoint keeps the
                # later plan/event flush atomic with these inserts.
                self._session.add_all((series, job))
                self._session.flush()
                self._session.add(plan)
                self._append_event(
                    job,
                    message="acquisition queued",
                    payload=payload,
                    now=now,
                )
                self._append_event(
                    job,
                    message=(
                        "query_plan_frozen"
                        if acquisition_round == 1
                        else "query_plan_expanded"
                    ),
                    payload={
                        "goal_id": goal_id,
                        "policy_version": plan.policy_version,
                        "planner_version": planner_version,
                        **(
                            {"reason": diff_json["reason"], "trigger": expansion_trigger}
                            if diff_json is not None
                            else {}
                        ),
                    },
                    now=now,
                )
                self._session.flush()
        except IntegrityError:
            existing_job = self._by_idempotency(tenant_id, idempotency_key)
            if existing_job is None:
                raise
            existing_plan = self.query_plan_for_job(existing_job.id)
            if existing_plan is None:
                raise ConflictError(
                    "concurrent acquisition job has no frozen query plan"
                )
            replay_snapshot = dict(request_snapshot)
            replay_snapshot["query_plan_id"] = str(existing_plan.id)
            self._require_same_planned_request(
                existing_job,
                research_case_id=research_case_id,
                thesis_id=thesis_id,
                research_run_id=research_run_id,
                scope_version_id=scope_version_id,
                goal_id=goal_id,
                acquisition_round=acquisition_round,
                request_snapshot=replay_snapshot,
                policy_snapshot=policy_snapshot,
                planner_version=planner_version,
                ordered_queries=ordered_queries,
                previous_query_plan_id=previous_query_plan_id,
                expansion_trigger=expansion_trigger,
                diff_json=diff_json,
            )
            return self._ref(existing_job)
        return self._ref(job)

    def _require_same_planned_request(
        self,
        existing: AcquisitionJob,
        *,
        research_case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        research_run_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        goal_id: str,
        acquisition_round: int,
        request_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
        planner_version: str,
        ordered_queries: list[dict[str, Any]],
        previous_query_plan_id: uuid.UUID | None,
        expansion_trigger: str | None,
        diff_json: dict[str, Any] | None,
    ) -> None:
        self._require_same_frozen_request(
            existing,
            research_case_id=research_case_id,
            thesis_id=thesis_id,
            research_run_id=research_run_id,
            request_snapshot=request_snapshot,
            policy_snapshot=policy_snapshot,
        )
        plan = self.query_plan_for_job(existing.id)
        series = self.series(plan.series_id) if plan is not None else None
        if (
            plan is None
            or series is None
            or series.scope_version_id != scope_version_id
            or series.goal_id != goal_id
            or series.tenant_id != existing.tenant_id
            or series.research_case_id != research_case_id
            or series.research_run_id != research_run_id
            or series.thesis_id != thesis_id
            or plan.acquisition_round != acquisition_round
            or plan.goal_id != goal_id
            or plan.planner_version != planner_version
            or plan.policy_version != policy_snapshot.get("version")
            or plan.ordered_queries_json != ordered_queries
            or plan.previous_query_plan_id != previous_query_plan_id
            or plan.expansion_trigger != expansion_trigger
            or plan.diff_json != diff_json
            or existing.request_snapshot.get("query_plan_id") != str(plan.id)
        ):
            raise ConflictError(
                "idempotency key already identifies a different frozen query plan"
            )

    def _by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> AcquisitionJob | None:
        return self._session.scalar(
            select(AcquisitionJob).where(
                AcquisitionJob.tenant_id == tenant_id,
                AcquisitionJob.idempotency_key == idempotency_key,
            )
        )

    def by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> AcquisitionJob | None:
        """Return the frozen job before callers resolve any mutable scope."""
        return self._by_idempotency(tenant_id, idempotency_key)

    @staticmethod
    def _require_same_frozen_request(
        existing: AcquisitionJob,
        *,
        research_case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        research_run_id: uuid.UUID | None,
        request_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
    ) -> None:
        if (
            existing.research_case_id != research_case_id
            or existing.thesis_id != thesis_id
            or existing.research_run_id != research_run_id
            or existing.request_snapshot != request_snapshot
            or existing.policy_snapshot != policy_snapshot
        ):
            raise ConflictError(
                "idempotency key already identifies a different frozen request"
            )

    def get_record(self, job_id: uuid.UUID) -> AcquisitionJob | None:
        return self._session.get(AcquisitionJob, job_id)

    def get(self, job_id: uuid.UUID) -> AcquisitionJobView | None:
        job = self.get_record(job_id)
        return None if job is None else self._view(job)

    def events(self, job_id: uuid.UUID) -> tuple[AcquisitionJobEvent, ...]:
        return tuple(
            self._session.scalars(
                select(AcquisitionJobEvent)
                .where(AcquisitionJobEvent.job_id == job_id)
                .order_by(AcquisitionJobEvent.seq)
            )
        )

    @staticmethod
    def _claim_eligibility(now: datetime):
        return or_(
            AcquisitionJob.status == "queued",
            (
                (AcquisitionJob.status == "retry_wait")
                & (AcquisitionJob.retry_at.is_not(None))
                & (AcquisitionJob.retry_at <= now)
            ),
            (
                (AcquisitionJob.status == "running")
                & (AcquisitionJob.lease_expires_at.is_not(None))
                & (AcquisitionJob.lease_expires_at <= now)
            ),
        )

    @classmethod
    def _eligible_claim_statement(cls, now: datetime):
        return (
            select(AcquisitionJob)
            .where(cls._claim_eligibility(now))
            .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
            .limit(1)
        )

    def _claim_statement(self, now: datetime):
        """PostgreSQL claim query, exposed for dialect-level contract testing."""
        return self._eligible_claim_statement(now).with_for_update(skip_locked=True)

    @classmethod
    def _sqlite_claim_statement(
        cls,
        *,
        now: datetime,
        worker_id: str,
        token: str,
        expiry: datetime,
    ):
        """Atomically select and claim one eligible SQLite job."""
        candidate_id = (
            select(AcquisitionJob.id)
            .where(cls._claim_eligibility(now))
            .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
            .limit(1)
            .scalar_subquery()
        )
        return (
            update(AcquisitionJob)
            .where(
                AcquisitionJob.id == candidate_id,
                cls._claim_eligibility(now),
            )
            .values(
                status="running",
                stage=case(
                    (AcquisitionJob.status == "queued", "searching"),
                    else_=AcquisitionJob.stage,
                ),
                attempt=AcquisitionJob.attempt + 1,
                lease_owner=worker_id,
                lease_token=token,
                lease_expires_at=expiry,
                retry_at=None,
                started_at=func.coalesce(AcquisitionJob.started_at, now),
                updated_at=now,
            )
            .returning(AcquisitionJob)
            .execution_options(synchronize_session=False, populate_existing=True)
        )

    def claim_next(
        self, *, worker_id: str, lease_for: timedelta
    ) -> AcquisitionClaim | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id must not be blank")
        validate_persistable_text(normalized_worker, path="$.worker_id")
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        now = self._now()
        dialect = self._session.get_bind().dialect.name
        token = secrets.token_urlsafe(32)
        expiry = now + lease_for
        if dialect == "sqlite":
            job = self._session.scalar(
                self._sqlite_claim_statement(
                    now=now,
                    worker_id=normalized_worker,
                    token=token,
                    expiry=expiry,
                )
            )
            if job is None:
                return None
        else:
            job = self._session.scalar(self._claim_statement(now))
            if job is None:
                return None
            prior_token = job.lease_token
            token_predicate = (
                AcquisitionJob.lease_token.is_(None)
                if prior_token is None
                else AcquisitionJob.lease_token == prior_token
            )
            result = self._session.execute(
                update(AcquisitionJob)
                .where(
                    AcquisitionJob.id == job.id,
                    token_predicate,
                    self._claim_eligibility(now),
                )
                .values(
                    status="running",
                    stage=case(
                        (AcquisitionJob.status == "queued", "searching"),
                        else_=AcquisitionJob.stage,
                    ),
                    attempt=AcquisitionJob.attempt + 1,
                    lease_owner=normalized_worker,
                    lease_token=token,
                    lease_expires_at=expiry,
                    retry_at=None,
                    started_at=func.coalesce(AcquisitionJob.started_at, now),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise StaleLeaseError("acquisition lease changed before claim")
            self._session.flush()
            self._session.expire(job)
            self._session.refresh(job)
        self._append_event(
            job,
            message="worker claimed acquisition",
            payload={"lease_owner": normalized_worker, "attempt": job.attempt},
            now=now,
        )
        self._session.flush()
        return AcquisitionClaim(
            job_id=job.id,
            lease_token=token,
            lease_owner=normalized_worker,
            lease_expires_at=_aware_utc(job.lease_expires_at),
            attempt=job.attempt,
        )

    def fence(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        allowed_stages: frozenset[str] | None = None,
    ) -> AcquisitionJob:
        """CAS-check a fresh lease in the caller's short transaction."""
        now = self._now()
        predicates = [
            AcquisitionJob.id == job_id,
            AcquisitionJob.status == "running",
            AcquisitionJob.lease_token == lease_token,
            AcquisitionJob.lease_expires_at.is_not(None),
            AcquisitionJob.lease_expires_at > now,
        ]
        if allowed_stages is not None:
            predicates.append(AcquisitionJob.stage.in_(allowed_stages))
        result = self._session.execute(
            update(AcquisitionJob)
            .where(*predicates)
            .values(updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleLeaseError("acquisition lease is stale")
        job = self.get_record(job_id)
        assert job is not None
        self._session.refresh(job)
        return job

    def renew_lease(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        lease_for: timedelta,
    ) -> datetime:
        """Extend the caller's current lease without reviving a stale claim."""
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        now = self._now()
        expiry = now + lease_for
        result = self._session.execute(
            update(AcquisitionJob)
            .where(
                AcquisitionJob.id == job_id,
                AcquisitionJob.status == "running",
                AcquisitionJob.lease_token == lease_token,
                AcquisitionJob.lease_expires_at.is_not(None),
                AcquisitionJob.lease_expires_at > now,
            )
            .values(lease_expires_at=expiry, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleLeaseError("acquisition lease is stale")
        return expiry

    def record_attempt(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        adapter_key: str,
        operation: str,
        started_at: datetime,
        finished_at: datetime,
        outcome: str,
        retryable: bool,
        safe_metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> AcquisitionAttempt:
        """Append one completed provider call behind the active lease CAS."""
        metadata = safe_metadata or {}
        validate_persistable_json(metadata, path="$.attempt.safe_metadata")
        validate_persistable_text(error_code, path="$.attempt.error_code")
        for value, name in (
            (adapter_key, "adapter_key"),
            (operation, "operation"),
            (outcome, "outcome"),
        ):
            validate_persistable_text(value, path=f"$.attempt.{name}")
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        start = _aware_utc(started_at)
        finish = _aware_utc(finished_at)
        if finish < start:
            raise ValueError("attempt finished_at must not precede started_at")
        self.fence(job_id, lease_token=lease_token)
        attempt_no = (
            self._session.scalar(
                select(func.max(AcquisitionAttempt.attempt_no)).where(
                    AcquisitionAttempt.job_id == job_id,
                    AcquisitionAttempt.adapter_key == adapter_key,
                    AcquisitionAttempt.operation == operation,
                )
            )
            or 0
        ) + 1
        attempt = AcquisitionAttempt(
            job_id=job_id,
            adapter_key=adapter_key,
            operation=operation,
            attempt_no=attempt_no,
            started_at=start,
            finished_at=finish,
            outcome=outcome,
            error_code=error_code,
            retryable=retryable,
            safe_metadata=metadata,
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    def record_event(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        message: str,
        payload: dict[str, Any],
    ) -> AcquisitionJobEvent:
        """Append one safe observable event behind the active lease fence."""
        validate_persistable_text(message, path="$.message")
        validate_persistable_json(payload, path="$.payload")
        if not message.strip():
            raise ValueError("message must not be blank")
        job = self.fence(job_id, lease_token=lease_token)
        event = self._append_event(job, message=message, payload=payload, now=self._now())
        self._session.flush()
        return event

    def create_or_get_reference(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        adapter_key: str,
        external_record_id: str,
        external_version: str,
        canonical_url: str,
        title: str,
        published_at: datetime | None,
        source_role: str,
        metadata_json: dict[str, Any],
    ) -> SourceReference:
        """Idempotently append one provider identity behind a lease fence."""
        validate_persistable_json(metadata_json, path="$.source_reference.metadata")
        self.fence(job_id, lease_token=lease_token)
        existing = self._session.scalar(
            select(SourceReference).where(
                SourceReference.job_id == job_id,
                SourceReference.adapter_key == adapter_key,
                SourceReference.external_record_id == external_record_id,
                SourceReference.external_version == external_version,
            )
        )
        normalized_published_at = (
            _aware_utc(published_at) if published_at is not None else None
        )
        expected = {
            "canonical_url": canonical_url,
            "title": title,
            "source_role": source_role,
            "metadata_json": metadata_json,
        }
        if existing is not None:
            existing_published_at = (
                _aware_utc(existing.published_at)
                if existing.published_at is not None
                else None
            )
            if (
                existing_published_at != normalized_published_at
                or any(
                    getattr(existing, key) != value
                    for key, value in expected.items()
                )
            ):
                raise ConflictError("source reference identity changed")
            return existing
        reference = SourceReference(
            job_id=job_id,
            adapter_key=adapter_key,
            external_record_id=external_record_id,
            external_version=external_version,
            canonical_url=canonical_url,
            title=title,
            published_at=normalized_published_at,
            source_role=source_role,
            metadata_json=metadata_json,
            created_at=self._now(),
        )
        self._session.add(reference)
        self._session.flush()
        return reference

    def record_exception(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        reason_code: str,
        detail_json: dict[str, Any],
        source_reference_id: uuid.UUID | None = None,
        retrieval_artifact_id: uuid.UUID | None = None,
        candidate_id: uuid.UUID | None = None,
    ) -> AcquisitionException:
        """Append or reuse one identical sanitized orchestration exception."""
        validate_persistable_text(reason_code, path="$.exception.reason_code")
        validate_persistable_json(detail_json, path="$.exception.detail")
        self.fence(job_id, lease_token=lease_token)
        existing = self._session.scalar(
            select(AcquisitionException)
            .where(
                AcquisitionException.job_id == job_id,
                AcquisitionException.source_reference_id == source_reference_id,
                AcquisitionException.retrieval_artifact_id
                == retrieval_artifact_id,
                AcquisitionException.candidate_id == candidate_id,
                AcquisitionException.reason_code == reason_code,
            )
            .order_by(AcquisitionException.created_at, AcquisitionException.id)
            .limit(1)
        )
        if existing is not None and existing.detail_json == detail_json:
            return existing
        exception = AcquisitionException(
            job_id=job_id,
            source_reference_id=source_reference_id,
            retrieval_artifact_id=retrieval_artifact_id,
            candidate_id=candidate_id,
            reason_code=reason_code,
            detail_json=detail_json,
            created_at=self._now(),
        )
        self._session.add(exception)
        self._session.flush()
        return exception

    def advance(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str,
        stage: str,
        status: str = "running",
        counters: Mapping[str, int] | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
        retry_at: datetime | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> AcquisitionJobView:
        event_payload = payload or {}
        validate_persistable_json(event_payload)
        validate_persistable_text(message, path="$.message")
        validate_persistable_text(error_code, path="$.error_code")
        validate_persistable_text(error_detail, path="$.error_detail")
        job = self.get_record(job_id)
        if job is not None and job.status in _TERMINAL_STATUSES:
            raise TerminalJobError(f"acquisition job is already {job.status}")
        now = self._now()
        if (
            job is None
            or job.status != "running"
            or job.lease_token != lease_token
            or job.lease_expires_at is None
            or _aware_utc(job.lease_expires_at) <= now
        ):
            raise StaleLeaseError("acquisition lease is stale")
        self._validate_transition(
            status=status,
            stage=stage,
            retry_at=retry_at,
            current_stage=job.stage if job is not None else None,
        )
        counter_values = dict(counters or {})
        unknown = set(counter_values) - _COUNTER_FIELDS
        if unknown or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counter_values.values()
        ):
            raise ValueError("counter values must be non-negative known counters")

        values: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "updated_at": now,
            "error_code": error_code,
            "error_detail": error_detail,
            **counter_values,
        }
        if status == "retry_wait":
            values.update(
                retry_at=retry_at.astimezone(UTC) if retry_at is not None else None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
        elif status in _TERMINAL_STATUSES:
            values.update(
                retry_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                finished_at=now,
            )
        result = self._session.execute(
            update(AcquisitionJob)
            .where(
                AcquisitionJob.id == job_id,
                AcquisitionJob.lease_token == lease_token,
                AcquisitionJob.lease_expires_at.is_not(None),
                AcquisitionJob.lease_expires_at > now,
                AcquisitionJob.status.not_in(_TERMINAL_STATUSES),
                self._advance_stage_predicate(status=status, stage=stage),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleLeaseError("acquisition lease is stale")
        self._session.flush()
        updated = self.get_record(job_id)
        assert updated is not None
        self._session.refresh(updated)
        self._append_event(
            updated,
            message=message or f"acquisition advanced to {stage}",
            payload=event_payload,
            now=now,
        )
        self._session.flush()
        return self._view(updated)

    @staticmethod
    def _advance_stage_predicate(*, status: str, stage: str):
        if status in {"running", "retry_wait"} and stage in _RUNNING_STAGE_ORDER:
            target_index = _RUNNING_STAGE_ORDER.index(stage)
            return AcquisitionJob.stage.in_(
                _RUNNING_STAGE_ORDER[: target_index + 1]
            )
        allowed = _TERMINAL_SOURCE_STAGES.get(status)
        if allowed is not None:
            return AcquisitionJob.stage.in_(allowed)
        return AcquisitionJob.stage.in_(())

    @staticmethod
    def _validate_transition(
        *,
        status: str,
        stage: str,
        retry_at: datetime | None,
        current_stage: str | None = None,
    ) -> None:
        if status == "running":
            if stage not in _RUNNING_STAGES:
                raise ValueError("stage is not a valid running stage")
            if retry_at is not None:
                raise ValueError("retry_at is only valid for retry_wait")
            AcquisitionRepository._validate_stage_progress(current_stage, stage)
            return
        if status == "retry_wait":
            if stage not in _RUNNING_STAGES:
                raise ValueError("stage is not a valid retry stage")
            if retry_at is None:
                raise ValueError("retry_wait requires retry_at")
            if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                raise ValueError("retry_at must be timezone-aware")
            AcquisitionRepository._validate_stage_progress(current_stage, stage)
            return
        if status in _TERMINAL_STATUSES and stage == status:
            if retry_at is not None:
                raise ValueError("terminal transitions cannot set retry_at")
            if (
                current_stage is not None
                and current_stage not in _TERMINAL_SOURCE_STAGES[status]
            ):
                raise ValueError("terminal transition is not valid from current stage")
            return
        raise ValueError("status and stage are incoherent")

    @staticmethod
    def _validate_stage_progress(
        current_stage: str | None, target_stage: str
    ) -> None:
        if current_stage is None or current_stage not in _RUNNING_STAGE_ORDER:
            return
        if _RUNNING_STAGE_ORDER.index(target_stage) < _RUNNING_STAGE_ORDER.index(
            current_stage
        ):
            raise ValueError("running stage cannot regress")

    def cancel(
        self,
        job_id: uuid.UUID,
        *,
        lease_token: str | None,
        payload: dict[str, Any] | None = None,
    ) -> AcquisitionJobView:
        event_payload = payload or {}
        validate_persistable_json(event_payload)
        job = self.get_record(job_id)
        if job is not None and job.status in _TERMINAL_STATUSES:
            raise TerminalJobError(f"acquisition job is already {job.status}")
        now = self._now()
        if lease_token is None:
            lease_predicates = (
                AcquisitionJob.lease_token.is_(None),
                AcquisitionJob.status.in_(("queued", "retry_wait")),
            )
        else:
            lease_predicates = (
                AcquisitionJob.lease_token == lease_token,
                AcquisitionJob.lease_expires_at.is_not(None),
                AcquisitionJob.lease_expires_at > now,
                AcquisitionJob.status == "running",
            )
        result = self._session.execute(
            update(AcquisitionJob)
            .where(
                AcquisitionJob.id == job_id,
                *lease_predicates,
            )
            .values(
                status="cancelled",
                stage="cancelled",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                retry_at=None,
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise StaleLeaseError("acquisition lease is stale")
        self._session.flush()
        updated = self.get_record(job_id)
        assert updated is not None
        self._session.refresh(updated)
        self._append_event(
            updated,
            message="acquisition cancelled",
            payload=event_payload,
            now=now,
        )
        self._session.flush()
        return self._view(updated)

    def _append_event(
        self,
        job: AcquisitionJob,
        *,
        message: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> AcquisitionJobEvent:
        validate_persistable_json(payload)
        validate_persistable_text(message, path="$.message")
        last_seq = self._session.scalar(
            select(func.max(AcquisitionJobEvent.seq)).where(
                AcquisitionJobEvent.job_id == job.id
            )
        )
        pending_seq = max(
            (
                candidate.seq
                for candidate in self._session.new
                if isinstance(candidate, AcquisitionJobEvent)
                and candidate.job_id == job.id
            ),
            default=0,
        )
        event = AcquisitionJobEvent(
            job_id=job.id,
            seq=max(last_seq or 0, pending_seq) + 1,
            status=job.status,
            stage=job.stage,
            message=message,
            payload_json=payload,
            created_at=now,
        )
        self._session.add(event)
        return event

    def admitted_evidence(
        self, job_id: uuid.UUID
    ) -> tuple[AdmittedEvidenceRef, ...]:
        rows = self._session.execute(
            select(EvidenceLink.id, SourceStatement.id, DocumentVersion.id)
            .select_from(AutomaticAdmissionDecision)
            .join(
                AcquisitionJob,
                AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
            )
            .join(
                EvidenceLink,
                (
                    EvidenceLink.automatic_admission_decision_id
                    == AutomaticAdmissionDecision.id
                )
                & (EvidenceLink.thesis_id == AcquisitionJob.thesis_id),
            )
            .join(
                SourceStatement,
                (SourceStatement.id == EvidenceLink.source_statement_id)
                & (
                    SourceStatement.automatic_admission_decision_id
                    == AutomaticAdmissionDecision.id
                )
                & (
                    SourceStatement.atomic_claim_candidate_id
                    == AutomaticAdmissionDecision.candidate_id
                ),
            )
            .join(
                AtomicClaimCandidate,
                (
                    AtomicClaimCandidate.id
                    == AutomaticAdmissionDecision.candidate_id
                )
                & (
                    AtomicClaimCandidate.source_span_id
                    == SourceStatement.source_span_id
                ),
            )
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(
                RetrievalArtifact,
                RetrievalArtifact.id
                == AutomaticAdmissionDecision.retrieval_artifact_id,
            )
            .join(
                SourceReference,
                (SourceReference.id == RetrievalArtifact.source_reference_id)
                & (
                    SourceReference.job_id
                    == AutomaticAdmissionDecision.job_id
                ),
            )
            .join(
                AcquisitionAttempt,
                (AcquisitionAttempt.id == RetrievalArtifact.attempt_id)
                & (
                    AcquisitionAttempt.job_id
                    == AutomaticAdmissionDecision.job_id
                ),
            )
            .join(
                RetrievalArtifactDocument,
                RetrievalArtifactDocument.retrieval_artifact_id
                == AutomaticAdmissionDecision.retrieval_artifact_id,
            )
            .join(
                DocumentVersion,
                (DocumentVersion.id == SourceSpan.document_version_id)
                & (
                    DocumentVersion.id
                    == RetrievalArtifactDocument.document_version_id
                ),
            )
            .where(
                AutomaticAdmissionDecision.job_id == job_id,
                AutomaticAdmissionDecision.outcome == "admitted",
            )
            .order_by(EvidenceLink.created_at, EvidenceLink.id)
        )
        return tuple(
            AdmittedEvidenceRef(
                evidence_link_id=evidence_link_id,
                source_statement_id=source_statement_id,
                document_version_id=document_version_id,
            )
            for evidence_link_id, source_statement_id, document_version_id in rows
        )

    def _coverage_evidence_batch(
        self, job_ids: tuple[uuid.UUID, ...]
    ) -> dict[uuid.UUID, tuple[AcquisitionEvidenceView, ...]]:
        """Return admitted evidence for a bounded job set in one query."""
        if not job_ids:
            return {}
        rows = self._session.execute(
            select(
                EvidenceLink,
                SourceStatement,
                DocumentVersion,
                AutomaticAdmissionDecision,
                AtomicClaimCandidate,
                SourceReference,
                RetrievalArtifactDocument,
                AcquisitionJob,
            )
            .select_from(AutomaticAdmissionDecision)
            .join(AcquisitionJob, AcquisitionJob.id == AutomaticAdmissionDecision.job_id)
            .join(
                EvidenceLink,
                (EvidenceLink.automatic_admission_decision_id == AutomaticAdmissionDecision.id)
                & (EvidenceLink.thesis_id == AcquisitionJob.thesis_id),
            )
            .join(
                SourceStatement,
                (SourceStatement.id == EvidenceLink.source_statement_id)
                & (SourceStatement.automatic_admission_decision_id == AutomaticAdmissionDecision.id)
                & (SourceStatement.atomic_claim_candidate_id == AutomaticAdmissionDecision.candidate_id),
            )
            .join(
                AtomicClaimCandidate,
                (AtomicClaimCandidate.id == AutomaticAdmissionDecision.candidate_id)
                & (AtomicClaimCandidate.source_span_id == SourceStatement.source_span_id),
            )
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(RetrievalArtifact, RetrievalArtifact.id == AutomaticAdmissionDecision.retrieval_artifact_id)
            .join(
                SourceReference,
                (SourceReference.id == RetrievalArtifact.source_reference_id)
                & (SourceReference.job_id == AutomaticAdmissionDecision.job_id),
            )
            .join(
                AcquisitionAttempt,
                (AcquisitionAttempt.id == RetrievalArtifact.attempt_id)
                & (AcquisitionAttempt.job_id == AutomaticAdmissionDecision.job_id),
            )
            .join(
                RetrievalArtifactDocument,
                RetrievalArtifactDocument.retrieval_artifact_id
                == AutomaticAdmissionDecision.retrieval_artifact_id,
            )
            .join(
                DocumentVersion,
                (DocumentVersion.id == SourceSpan.document_version_id)
                & (DocumentVersion.id == RetrievalArtifactDocument.document_version_id),
            )
            .where(
                AutomaticAdmissionDecision.job_id.in_(job_ids),
                AutomaticAdmissionDecision.outcome == "admitted",
                EvidenceLink.review_state == "automatically_admitted",
            )
            .order_by(EvidenceLink.created_at, EvidenceLink.id)
        )
        result: dict[uuid.UUID, list[AcquisitionEvidenceView]] = {
            job_id: [] for job_id in job_ids
        }
        for link, statement, document, decision, candidate, reference, binding, job in rows:
            request = job.request_snapshot if isinstance(job.request_snapshot, dict) else {}
            metadata = reference.metadata_json if isinstance(reference.metadata_json, dict) else {}
            structured = candidate.structured_fields if isinstance(candidate.structured_fields, dict) else {}
            goal_id = request.get("goal_id")
            if not isinstance(goal_id, str) or not goal_id.strip():
                continue
            provider = metadata.get("provider_identity") or metadata.get("publisher")
            source_identity = (
                f"{reference.adapter_key}:{provider.strip()}"
                if isinstance(provider, str) and provider.strip()
                else ""
            )
            observed = structured.get("observed_period")
            if observed is None and statement.observed_period is not None:
                observed = statement.observed_period.isoformat()
            result[job.id].append(
                AcquisitionEvidenceView(
                    evidence_link_id=link.id,
                    automatic_admission_decision_id=decision.id,
                    source_statement_id=statement.id,
                    document_version_id=document.id,
                    job_id=job.id,
                    goal_id=goal_id.strip(),
                    review_state=link.review_state,
                    role=link.role,
                    authority=document.source_authority,
                    source_identity=source_identity,
                    publication_key=binding.publication_key,
                    canonical_url=reference.canonical_url,
                    content_sha256=document.content_sha256,
                    available_at=link.available_at,
                    subject=structured.get("subject"),
                    security_code=(
                        structured.get("security_code")
                        or metadata.get("security_code")
                    ),
                    metric=structured.get("predicate"),
                    observed_period=(str(observed) if observed is not None else None),
                    unit=structured.get("unit"),
                    policy_version=decision.policy_version,
                )
            )
        return {job_id: tuple(values) for job_id, values in result.items()}

    def coverage_evidence(
        self, job_id: uuid.UUID
    ) -> tuple[AcquisitionEvidenceView, ...]:
        """Return admitted evidence with coherent frozen coverage provenance."""
        return self._coverage_evidence_batch((job_id,)).get(job_id, ())

    @staticmethod
    def _query_plan_view(plan: AcquisitionQueryPlan) -> AcquisitionQueryPlanView:
        ordered = tuple(
            {
                "adapter_key": str(item.get("adapter_key", "")),
                "objective": str(item.get("objective", "")),
                "query": str(item.get("query", "")),
            }
            for item in plan.ordered_queries_json
            if isinstance(item, dict)
        )
        return AcquisitionQueryPlanView(
            id=plan.id,
            job_id=plan.acquisition_job_id,
            series_id=plan.series_id,
            goal_id=plan.goal_id,
            acquisition_round=plan.acquisition_round,
            frozen_inputs=dict(plan.frozen_inputs_json),
            ordered_queries=ordered,
        )

    def query_plan_view_for_job(
        self, job_id: uuid.UUID
    ) -> AcquisitionQueryPlanView | None:
        plan = self.query_plan_for_job(job_id)
        if plan is None:
            return None
        return self._query_plan_view(plan)

    @staticmethod
    def _search_operations_for(
        plan: AcquisitionQueryPlan,
        attempts: tuple[AcquisitionAttempt, ...],
    ) -> tuple[AcquisitionSearchOperationView, ...]:
        result: list[AcquisitionSearchOperationView] = []
        for query_index, item in enumerate(plan.ordered_queries_json):
            if not isinstance(item, dict):
                continue
            adapter_key = str(item.get("adapter_key", ""))
            query = str(item.get("query", ""))
            operation_attempts = tuple(
                attempt
                for attempt in attempts
                if attempt.adapter_key == adapter_key
                and isinstance(attempt.safe_metadata, dict)
                and attempt.safe_metadata.get("query_index") == query_index
            )
            successful = any(
                attempt.outcome == "succeeded" and attempt.finished_at is not None
                for attempt in operation_attempts
            )
            outcome = (
                "succeeded"
                if successful
                else "failed"
                if operation_attempts
                else "missing"
            )
            result.append(
                AcquisitionSearchOperationView(
                    job_id=plan.acquisition_job_id,
                    query_index=query_index,
                    adapter_key=adapter_key,
                    query=query,
                    outcome=outcome,
                    attempt_count=len(operation_attempts),
                    error_codes=tuple(
                        sorted(
                            {
                                attempt.error_code
                                for attempt in operation_attempts
                                if isinstance(attempt.error_code, str)
                                and attempt.error_code.strip()
                            }
                        )
                    ),
                )
            )
        return tuple(result)

    def coverage_search_operations(
        self, job_id: uuid.UUID
    ) -> tuple[AcquisitionSearchOperationView, ...]:
        plan = self.query_plan_for_job(job_id)
        if plan is None:
            return ()
        attempts = tuple(
            self._session.scalars(
                select(AcquisitionAttempt)
                .where(
                    AcquisitionAttempt.job_id == job_id,
                    AcquisitionAttempt.operation == "search",
                )
                .order_by(
                    AcquisitionAttempt.adapter_key,
                    AcquisitionAttempt.attempt_no,
                    AcquisitionAttempt.id,
                )
            )
        )
        return self._search_operations_for(plan, attempts)

    def coverage_snapshots(
        self, job_ids: tuple[uuid.UUID, ...]
    ) -> dict[uuid.UUID, AcquisitionCoverageSnapshot]:
        """Load jobs, plans, search attempts, and evidence with bounded queries."""
        ordered_ids = tuple(dict.fromkeys(job_ids))
        if not ordered_ids:
            return {}
        rows = tuple(
            self._session.execute(
                select(AcquisitionJob, AcquisitionQueryPlan)
                .join(
                    AcquisitionQueryPlan,
                    AcquisitionQueryPlan.acquisition_job_id == AcquisitionJob.id,
                )
                .where(AcquisitionJob.id.in_(ordered_ids))
            )
        )
        attempts_by_job: dict[uuid.UUID, list[AcquisitionAttempt]] = {
            job_id: [] for job_id in ordered_ids
        }
        for attempt in self._session.scalars(
            select(AcquisitionAttempt)
            .where(
                AcquisitionAttempt.job_id.in_(ordered_ids),
                AcquisitionAttempt.operation == "search",
            )
            .order_by(
                AcquisitionAttempt.job_id,
                AcquisitionAttempt.adapter_key,
                AcquisitionAttempt.attempt_no,
                AcquisitionAttempt.id,
            )
        ):
            attempts_by_job[attempt.job_id].append(attempt)
        evidence_by_job = self._coverage_evidence_batch(ordered_ids)
        return {
            job.id: AcquisitionCoverageSnapshot(
                job=self._view(job),
                plan=self._query_plan_view(plan),
                search_operations=self._search_operations_for(
                    plan, tuple(attempts_by_job[job.id])
                ),
                evidence=evidence_by_job.get(job.id, ()),
            )
            for job, plan in rows
        }
