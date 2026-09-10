"""Checkpointed orchestration for governed acquisition claims.

Every helper opens one short database unit and closes it before source or model
I/O. PostgreSQL remains the sole recovery state: references, attempts,
artifacts, bindings, extraction runs, decisions, and publications are all
re-read when a lease is reclaimed.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acquisition.policy import (
    B_SCOPE_POLICY,
    AcquisitionQueryPlanner,
    SourcePolicy,
)
from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    RetrievedSearchResult,
    SourceAdapter,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.ai.client import LLMClient
from app.ai.extraction import StatementExtractor
from app.documents.locators import (
    SourceLocatorV1,
    TextPosition,
    TextQuote,
    compute_text_sha256,
)
from app.domain.acquisition import AcquisitionRequest, EvidenceObjective
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.event_research import EventResearchBrief
from app.models.ledger import (
    AIRun,
    AtomicClaimCandidate,
    CaseDocumentVersion,
    DocumentVersion,
    DocumentUploadArtifact,
    EvidenceLink,
    SourceSpan,
)
from app.models.source_governance import SourceContract
from app.repositories.acquisition import (
    AcquisitionClaim,
    AcquisitionRepository,
    StaleLeaseError,
)
from app.repositories.documents import DocumentRepository
from app.services.atomic_claims import AtomicClaimService
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
    lease_write_fence,
)
from app.services.ingest import DocumentService
from app.services.retrieved_documents import (
    FetchCheckpointContext,
    FrozenRequestContext,
    RetrievedDocumentFreezer,
)
from app.services.source_admission import source_contract_is_active


SessionFactory = Callable[[], Session]
JitterSource = Callable[[], float]
_SYSTEM_RANDOM = secrets.SystemRandom()
_RUNNING_STAGES = frozenset(
    {"searching", "fetching", "freezing", "extracting", "admitting"}
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(child) for child in value]
    return value


def _diagnostics_for_storage(value: Any) -> Any:
    """Preserve provider diagnostics as JSON, treating non-finite numbers as absent."""
    if isinstance(value, Mapping):
        return {str(key): _diagnostics_for_storage(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_diagnostics_for_storage(child) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:128] or "ProviderError"


class AcquisitionRunner:
    """Resume one lease-fenced acquisition claim from durable checkpoints."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        adapters: Mapping[str, SourceAdapter],
        llm_client: LLMClient,
        freezer: RetrievedDocumentFreezer | None = None,
        planner: AcquisitionQueryPlanner | None = None,
        clock: Callable[[], datetime] = _utcnow,
        retry_delay: timedelta = timedelta(minutes=1),
        max_retry_delay: timedelta = timedelta(minutes=15),
        retry_jitter_ratio: float = 0.2,
        jitter_source: JitterSource = _SYSTEM_RANDOM.random,
        max_attempts: int = 3,
        lease_for: timedelta = timedelta(seconds=1800),
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        if retry_delay <= timedelta(0):
            raise ValueError("retry_delay must be positive")
        if max_retry_delay < retry_delay:
            raise ValueError("max_retry_delay must not be less than retry_delay")
        if (
            isinstance(retry_jitter_ratio, bool)
            or not isinstance(retry_jitter_ratio, (int, float))
            or not math.isfinite(retry_jitter_ratio)
            or not 0 <= retry_jitter_ratio <= 1
        ):
            raise ValueError("retry_jitter_ratio must be between zero and one")
        if not callable(jitter_source):
            raise TypeError("jitter_source must be callable")
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        normalized: dict[str, SourceAdapter] = {}
        for key, adapter in adapters.items():
            if key in normalized:
                raise ValueError("adapter keys must be unique")
            normalized[key] = adapter
        self._session_factory = session_factory
        self._adapters = normalized
        self._extractor = StatementExtractor(llm_client)
        self._freezer = freezer or RetrievedDocumentFreezer(
            session_factory, clock=clock
        )
        self._planner = planner or AcquisitionQueryPlanner()
        self._clock = clock
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._retry_jitter_ratio = float(retry_jitter_ratio)
        self._jitter_source = jitter_source
        self._max_attempts = max_attempts
        self._lease_for = lease_for
        self._searched_adapters: set[str] = set()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acquisition runner clock must be timezone-aware")
        return value.astimezone(UTC)

    def run_claim(self, claim: AcquisitionClaim) -> None:
        contract = self._load_contract(claim)
        if contract is None:
            return
        request, policy, stage = contract
        if not self._configured_adapters_are_safe(claim, policy):
            return
        if not self._fetch_checkpoints_are_valid(claim):
            return
        queries = {
            planned.adapter_key: planned
            for planned in self._planner.plan(request, policy)
            if planned.adapter_key in self._adapters
        }
        if request.acquisition_kind == "intake_material":
            if stage == "searching":
                if not self._prepare_intake_material(claim, request):
                    return
                self._advance(claim, "extracting")
                stage = "extracting"
            if stage == "extracting":
                self._extract(claim)
                self._advance(claim, "admitting")
                stage = "admitting"
            if stage == "admitting":
                self._admit_and_publish(claim, request)
                self._finish_terminal(claim)
            return
        if stage == "searching":
            self._search(claim, request, queries)
            if not self._has_references(claim.job_id):
                self._finish_without_artifacts(claim, stage="searching")
                return
            self._advance(claim, "fetching")
            stage = "fetching"
        if stage == "fetching":
            self._fetch(claim, request)
            if not self._has_artifacts(claim.job_id):
                self._finish_without_artifacts(claim, stage="fetching")
                return
            self._advance(claim, "freezing")
            stage = "freezing"
        if stage == "freezing":
            self._freeze(claim, request, policy)
            self._advance(claim, "extracting")
            stage = "extracting"
        if stage == "extracting":
            self._extract(claim)
            self._advance(claim, "admitting")
            stage = "admitting"
        if stage == "admitting":
            self._admit_and_publish(claim, request)
            self._finish_terminal(claim)

    def _configured_adapters_are_safe(
        self, claim: AcquisitionClaim, policy: SourcePolicy
    ) -> bool:
        failures: list[tuple[str, dict[str, str]]] = []
        for adapter_key in sorted(policy.enabled_adapter_keys):
            adapter = self._adapters.get(adapter_key)
            if adapter is None:
                failures.append(
                    (
                        "configured_adapter_unavailable",
                        {"adapter_key": adapter_key},
                    )
                )
                continue
            try:
                descriptor_key = adapter.descriptor.adapter_key
            except Exception:
                descriptor_key = None
            if descriptor_key != adapter_key:
                failures.append(
                    (
                        "configured_adapter_identity_mismatch",
                        {"adapter_key": adapter_key},
                    )
                )
        if not failures:
            return True

        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            for reason_code, detail in failures:
                repository.record_exception(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    reason_code=reason_code,
                    detail_json=detail,
                )
            repository.advance(
                claim.job_id,
                lease_token=claim.lease_token,
                stage="failed",
                status="failed",
                message="acquisition adapter configuration failed closed",
                error_code="adapter_configuration_invalid",
            )
            session.commit()
        return False

    def _fetch_checkpoints_are_valid(self, claim: AcquisitionClaim) -> bool:
        with self._session_factory() as session:
            invalid_attempts = tuple(
                session.scalars(
                    select(AcquisitionAttempt)
                    .outerjoin(
                        RetrievalArtifact,
                        RetrievalArtifact.attempt_id == AcquisitionAttempt.id,
                    )
                    .where(
                        AcquisitionAttempt.job_id == claim.job_id,
                        AcquisitionAttempt.operation == "fetch",
                        AcquisitionAttempt.outcome == "succeeded",
                        RetrievalArtifact.id.is_(None),
                    )
                )
            )
            invalid = []
            for attempt in invalid_attempts:
                metadata = attempt.safe_metadata
                reference_value = (
                    metadata.get("source_reference_id")
                    if isinstance(metadata, dict)
                    else None
                )
                try:
                    reference_id = uuid.UUID(reference_value)
                except (TypeError, ValueError):
                    reference_id = None
                reference = (
                    session.get(SourceReference, reference_id)
                    if reference_id is not None
                    else None
                )
                invalid.append(
                    (
                        attempt.id,
                        reference.id
                        if reference is not None
                        and reference.job_id == claim.job_id
                        else None,
                    )
                )
        if not invalid:
            return True

        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            for attempt_id, reference_id in invalid:
                repository.record_exception(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    reason_code="invalid_fetch_checkpoint",
                    detail_json={"attempt_id": str(attempt_id)},
                    source_reference_id=reference_id,
                )
            repository.advance(
                claim.job_id,
                lease_token=claim.lease_token,
                stage="failed",
                status="failed",
                message="acquisition fetch checkpoint failed closed",
                error_code="invalid_fetch_checkpoint",
            )
            session.commit()
        return False

    def _load_contract(
        self, claim: AcquisitionClaim
    ) -> tuple[AcquisitionRequest, SourcePolicy, str] | None:
        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            job = repository.fence(
                claim.job_id,
                lease_token=claim.lease_token,
                allowed_stages=_RUNNING_STAGES,
            )
            request_snapshot = dict(job.request_snapshot or {})
            policy_snapshot = dict(job.policy_snapshot or {})
            stage = job.stage
            request_version = request_snapshot.get("source_policy_version")
            policy_version = policy_snapshot.get("version")
            if (
                request_version != policy_version
                or request_version != B_SCOPE_POLICY.version
                or policy_version != B_SCOPE_POLICY.version
            ):
                repository.record_exception(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    reason_code="unsupported_source_policy_version",
                    detail_json={
                        "active_policy_version": B_SCOPE_POLICY.version,
                    },
                )
                exception_count = session.scalar(
                    select(func.count()).select_from(AcquisitionException).where(
                        AcquisitionException.job_id == claim.job_id
                    )
                ) or 0
                repository.advance(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    stage="failed",
                    status="failed",
                    counters={"exception_count": exception_count},
                    message="acquisition source policy version is unsupported",
                    error_code="unsupported_source_policy_version",
                )
                session.commit()
                return None
            entities = request_snapshot.get("entity_names", ())
            if entities is None:
                entities = ()
            if (isinstance(entities, (list, tuple))
                    and all(isinstance(entity, str) for entity in entities)
                    and not any(entity.strip() for entity in entities)):
                # Every automatic semantic admission requires a subject from
                # the frozen entity names, including material intake. A ticker
                # or metric can form a query but cannot satisfy that gate.
                # Check before DTO construction to contain legacy blank names,
                # while leaving the original immutable request untouched.
                repository.record_exception(
                    claim.job_id, lease_token=claim.lease_token,
                    reason_code="research_subject_missing", detail_json={},
                )
                exception_count = session.scalar(select(func.count()).select_from(AcquisitionException).where(
                    AcquisitionException.job_id == claim.job_id)) or 0
                repository.advance(
                    claim.job_id, lease_token=claim.lease_token, stage="failed", status="failed",
                    counters={"exception_count": exception_count},
                    message="acquisition requires a frozen research subject",
                    error_code="research_subject_missing",
                )
                session.commit()
                return None
            session.commit()
        request = AcquisitionRequest(
            tenant_id=request_snapshot["tenant_id"],
            case_id=uuid.UUID(request_snapshot["case_id"]),
            thesis_id=uuid.UUID(request_snapshot["thesis_id"]),
            research_run_id=(
                uuid.UUID(request_snapshot["research_run_id"])
                if request_snapshot.get("research_run_id")
                else None
            ),
            round=int(request_snapshot["round"]),
            objective=EvidenceObjective(request_snapshot["objective"]),
            target_link_role=request_snapshot["target_link_role"],
            thesis_statement=request_snapshot["thesis_statement"],
            entity_names=tuple(request_snapshot.get("entity_names") or ()),
            security_codes=tuple(request_snapshot.get("security_codes") or ()),
            metric_terms=tuple(request_snapshot.get("metric_terms") or ()),
            period_start=request_snapshot["period_start"],
            period_end=request_snapshot["period_end"],
            cutoff=_parse_datetime(request_snapshot["cutoff"], "cutoff"),
            allowed_source_roles=frozenset(
                request_snapshot.get("allowed_source_roles") or ()
            ),
            source_policy_version=request_snapshot["source_policy_version"],
            idempotency_key=request_snapshot["idempotency_key"],
            acquisition_kind=request_snapshot.get(
                "acquisition_kind", "external_gap"
            ),
            document_version_id=(
                uuid.UUID(request_snapshot["document_version_id"])
                if request_snapshot.get("document_version_id")
                else None
            ),
        )
        policy = SourcePolicy(
            version=policy_snapshot["version"],
            enabled_adapter_keys=frozenset(
                policy_snapshot.get("enabled_adapter_keys") or ()
            ),
            allowed_source_roles=frozenset(
                policy_snapshot.get("allowed_source_roles") or ()
            ),
            exact_hosts=frozenset(policy_snapshot.get("exact_hosts") or ()),
            suffix_hosts=frozenset(policy_snapshot.get("suffix_hosts") or ()),
            max_response_bytes=int(policy_snapshot["max_response_bytes"]),
            per_adapter_page_limit=int(policy_snapshot["per_adapter_page_limit"]),
            permission_declarations=tuple(
                tuple(item)
                for item in policy_snapshot.get("permission_declarations") or ()
            ),
        )
        return request, policy, stage

    def _prepare_intake_material(
        self, claim: AcquisitionClaim, request: AcquisitionRequest
    ) -> bool:
        """Bind the frozen intake original to governed acquisition lineage."""
        document_id = request.document_version_id
        if document_id is None:
            return False
        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            repository.fence(
                claim.job_id,
                lease_token=claim.lease_token,
                allowed_stages=frozenset({"searching"}),
            )
            document = session.get(DocumentVersion, document_id)
            attachment = session.scalar(
                select(CaseDocumentVersion.id).where(
                    CaseDocumentVersion.research_case_id == request.case_id,
                    CaseDocumentVersion.document_version_id == document_id,
                )
            )
            brief = session.scalar(
                select(EventResearchBrief)
                .where(EventResearchBrief.research_case_id == request.case_id)
                .order_by(EventResearchBrief.created_at.desc())
                .limit(1)
            )
            contract = session.scalar(
                select(SourceContract).where(
                    SourceContract.document_version_id == document_id
                )
            )
            upload = session.scalar(
                select(DocumentUploadArtifact).where(
                    DocumentUploadArtifact.document_version_id == document_id
                )
            )
            reason: str | None = None
            raw = (
                upload.raw_bytes
                if upload is not None
                else brief.raw_input.encode("utf-8")
                if brief is not None
                else b""
            )
            mime_type = (
                upload.mime_type
                if upload is not None
                else "text/plain; charset=utf-8"
            )
            if (
                document is None
                or attachment is None
                or brief is None
                or brief.source_metadata.get("intake_role") != "provided_material"
            ):
                reason = "intake_material_scope_mismatch"
            elif hashlib.sha256(raw).hexdigest() != document.content_sha256:
                reason = "intake_material_content_mismatch"
            elif (
                contract is None
                or contract.source_type not in {"pasted_snapshot", "uploaded_file"}
                or contract.research_source_type != contract.source_type
                or not contract.allow_ai_processing
                or not contract.allow_display
                or not source_contract_is_active(contract, at=self._now())
            ):
                reason = "intake_material_contract_rejected"
            if reason is not None:
                repository.record_exception(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    reason_code=reason,
                    detail_json={"document_version_id": str(document_id)},
                )
                repository.advance(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    stage="failed",
                    status="failed",
                    counters={"exception_count": 1},
                    message="intake material failed governed source checks",
                    error_code=reason,
                )
                session.commit()
                return False

            assert document is not None and contract is not None
            reference = repository.create_or_get_reference(
                claim.job_id,
                lease_token=claim.lease_token,
                adapter_key="intake_material",
                external_record_id=str(document.id),
                external_version=document.content_sha256,
                canonical_url=document.source_url,
                title=document.title or brief.event_title,
                published_at=document.published_at,
                source_role="user_provided_material",
                metadata_json={
                    "provider_identity": contract.provider_or_tenant,
                    "document_version_id": str(document.id),
                },
            )
            checkpoint = session.execute(
                select(AcquisitionAttempt, RetrievalArtifact)
                .join(
                    RetrievalArtifact,
                    RetrievalArtifact.attempt_id == AcquisitionAttempt.id,
                )
                .join(
                    RetrievalArtifactDocument,
                    RetrievalArtifactDocument.retrieval_artifact_id
                    == RetrievalArtifact.id,
                )
                .where(
                    AcquisitionAttempt.job_id == claim.job_id,
                    AcquisitionAttempt.adapter_key == "intake_material",
                    AcquisitionAttempt.operation == "fetch",
                    AcquisitionAttempt.outcome == "succeeded",
                    AcquisitionAttempt.finished_at.is_not(None),
                    RetrievalArtifact.source_reference_id == reference.id,
                    RetrievalArtifact.content_sha256 == document.content_sha256,
                    RetrievalArtifact.final_url == document.source_url,
                    RetrievalArtifactDocument.document_version_id == document.id,
                )
                .order_by(AcquisitionAttempt.started_at, AcquisitionAttempt.id)
                .limit(1)
            ).first()
            if checkpoint is not None:
                attempt, artifact = checkpoint
                metadata = (
                    attempt.safe_metadata
                    if isinstance(attempt.safe_metadata, dict)
                    else {}
                )
                if (
                    metadata.get("source_reference_id") != str(reference.id)
                    or artifact.raw_bytes != raw
                    or artifact.mime_type != mime_type
                ):
                    raise ValueError("intake material checkpoint is inconsistent")
                session.commit()
                return True
            now = self._now()
            attempt = repository.record_attempt(
                claim.job_id,
                lease_token=claim.lease_token,
                adapter_key="intake_material",
                operation="fetch",
                started_at=now,
                finished_at=now,
                outcome="succeeded",
                retryable=False,
                safe_metadata={
                    "source_reference_id": str(reference.id),
                    "claim_attempt": claim.attempt,
                    "retrieval_mode": "frozen_intake_original",
                },
            )
            artifact = session.scalar(
                select(RetrievalArtifact).where(
                    RetrievalArtifact.source_reference_id == reference.id,
                    RetrievalArtifact.attempt_id == attempt.id,
                )
            )
            if artifact is None:
                artifact = RetrievalArtifact(
                    source_reference_id=reference.id,
                    attempt_id=attempt.id,
                    content_sha256=document.content_sha256,
                    raw_bytes=raw,
                    mime_type=mime_type,
                    byte_size=len(raw),
                    final_url=document.source_url,
                    etag=None,
                    last_modified=None,
                    provider_request_id=f"intake:{document.id}",
                    retrieved_at=now,
                )
                session.add(artifact)
                session.flush()
            binding = session.scalar(
                select(RetrievalArtifactDocument).where(
                    RetrievalArtifactDocument.retrieval_artifact_id == artifact.id
                )
            )
            if binding is None:
                session.add(
                    RetrievalArtifactDocument(
                        retrieval_artifact_id=artifact.id,
                        document_version_id=document.id,
                        relation="intake_original",
                        publication_key=f"intake:{document.content_sha256}"[:64],
                        created_at=now,
                    )
                )
            if upload is None:
                locator = SourceLocatorV1(
                    document_sha256=document.content_sha256,
                    page=1,
                    parser_version=document.parser_version,
                    text_position=TextPosition(start=0, end=len(brief.raw_input)),
                    text_quote=TextQuote(exact=brief.raw_input),
                    extra={"kind": "intake_material"},
                ).to_storage_dict()
                # PostgreSQL's JSON type has no equality operator. Compare
                # decoded locators structurally after scoping to this document;
                # this also ignores object-key serialization order on SQLite.
                replay_span_exists = any(
                    stored_locator == locator
                    for stored_locator in session.scalars(
                        select(SourceSpan.locator_v1).where(
                            SourceSpan.document_version_id == document.id
                        )
                    )
                )
                if not replay_span_exists:
                    DocumentService(DocumentRepository(session)).add_span(
                        document_version_id=document.id,
                        locator=locator,
                        verbatim_text=brief.raw_input,
                        text_sha256=compute_text_sha256(brief.raw_input),
                        context_hash=compute_text_sha256(brief.raw_input),
                        locator_v1=locator,
                    )
            session.commit()
        return True

    def _fence(self, claim: AcquisitionClaim) -> None:
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).fence(
                claim.job_id, lease_token=claim.lease_token
            )
            session.commit()

    def _renew(self, claim: AcquisitionClaim) -> None:
        """Give the current claim a fresh lease budget.

        Called before each long unit of work (per-document LLM extraction) so
        a multi-document job — dozens of documents at minutes per extraction
        — does not outlive the single lease granted at claim time.  Losing the
        lease raises ``StaleLeaseError`` for the caller to abandon the claim.
        """
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).renew(
                claim.job_id,
                lease_token=claim.lease_token,
                lease_for=self._lease_for,
            )
            session.commit()

    def _record_attempt(
        self,
        claim: AcquisitionClaim,
        *,
        adapter_key: str,
        operation: str,
        started_at: datetime,
        outcome: str,
        retryable: bool,
        metadata: dict[str, Any],
        error_code: str | None = None,
    ) -> uuid.UUID:
        with self._session_factory() as session:
            attempt = AcquisitionRepository(
                session, clock=self._clock
            ).record_attempt(
                claim.job_id,
                lease_token=claim.lease_token,
                adapter_key=adapter_key,
                operation=operation,
                started_at=started_at,
                finished_at=self._now(),
                outcome=outcome,
                retryable=retryable,
                safe_metadata={**metadata, "claim_attempt": claim.attempt},
                error_code=error_code,
            )
            attempt_id = attempt.id
            session.commit()
        return attempt_id

    def _record_exception(
        self,
        claim: AcquisitionClaim,
        *,
        reason_code: str,
        detail: dict[str, Any],
        reference_id: uuid.UUID | None = None,
        artifact_id: uuid.UUID | None = None,
        candidate_id: uuid.UUID | None = None,
    ) -> None:
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).record_exception(
                claim.job_id,
                lease_token=claim.lease_token,
                reason_code=reason_code,
                detail_json=detail,
                source_reference_id=reference_id,
                retrieval_artifact_id=artifact_id,
                candidate_id=candidate_id,
            )
            session.commit()

    def _search(self, claim, request, queries) -> None:
        for adapter_key, planned in queries.items():
            adapter = self._adapters[adapter_key]
            try:
                # Renew BEFORE each adapter: a search call can run long when
                # the upstream is slow, and the queue may carry several
                # adapters.  The single lease from claim time cannot cover all
                # of them, and a stale lease here must not crash the worker.
                self._renew(claim)
            except StaleLeaseError:
                return
            started_at = self._now()
            try:
                items = adapter.search(planned.query, request.cutoff)
            except SourceUnavailable as exc:
                self._record_attempt(
                    claim,
                    adapter_key=adapter_key,
                    operation="search",
                    started_at=started_at,
                    outcome="failed",
                    retryable=exc.retryable,
                    metadata={"diagnostics": _diagnostics_for_storage(exc.diagnostics)},
                    error_code="SourceUnavailable",
                )
                self._record_exception(
                    claim,
                    reason_code="source_unavailable",
                    detail={
                        "adapter_key": adapter_key,
                        "operation": "search",
                        "retryable": exc.retryable,
                        "claim_attempt": claim.attempt,
                        "diagnostics": _diagnostics_for_storage(exc.diagnostics),
                    },
                )
                continue
            except Exception as exc:
                code = _safe_error_code(exc)
                self._record_attempt(
                    claim,
                    adapter_key=adapter_key,
                    operation="search",
                    started_at=started_at,
                    outcome="failed",
                    retryable=False,
                    metadata={},
                    error_code=code,
                )
                self._record_exception(
                    claim,
                    reason_code="search_failed",
                    detail={"adapter_key": adapter_key, "error_code": code},
                )
                continue
            self._record_attempt(
                claim,
                adapter_key=adapter_key,
                operation="search",
                started_at=started_at,
                outcome="succeeded",
                retryable=False,
                metadata={"item_count": len(items)},
            )
            self._searched_adapters.add(adapter_key)
            for item in items:
                if isinstance(item, RejectedSearchItem):
                    self._record_exception(
                        claim,
                        reason_code="search_item_rejected",
                        detail={
                            "adapter_key": adapter_key,
                            "external_record_id": item.external_record_id,
                            "reason": item.reason,
                            "metadata": _thaw(item.metadata),
                        },
                    )
                    continue
                reference_value = (
                    item.reference
                    if isinstance(item, RetrievedSearchResult)
                    else item
                )
                adapter.descriptor.validate_reference(reference_value)
                if isinstance(item, RetrievedSearchResult):
                    self._freezer.checkpoint_search_result(
                        reference_value,
                        item.envelope,
                        FetchCheckpointContext(
                            job_id=claim.job_id,
                            research_case_id=request.case_id,
                            tenant_id=request.tenant_id,
                            declared_actor=claim.lease_owner,
                            lease_token=claim.lease_token,
                            claim_attempt=claim.attempt,
                            retrieved_at=self._now(),
                        ),
                        started_at=started_at,
                    )
                    continue
                metadata = _thaw(reference_value.metadata)
                metadata["retrieval_locator"] = _thaw(
                    reference_value.fetch_locator
                )
                with self._session_factory() as session:
                    AcquisitionRepository(
                        session, clock=self._clock
                    ).create_or_get_reference(
                        claim.job_id,
                        lease_token=claim.lease_token,
                        adapter_key=reference_value.adapter_key,
                        external_record_id=reference_value.external_record_id,
                        external_version=reference_value.external_version,
                        canonical_url=reference_value.canonical_url,
                        title=reference_value.title,
                        published_at=reference_value.published_at,
                        source_role=reference_value.source_role,
                        metadata_json=metadata,
                    )
                    session.commit()

    def _references_without_artifacts(
        self, job_id: uuid.UUID
    ) -> tuple[SourceReference, ...]:
        with self._session_factory() as session:
            values = tuple(
                session.scalars(
                    select(SourceReference)
                    .outerjoin(
                        RetrievalArtifact,
                        RetrievalArtifact.source_reference_id == SourceReference.id,
                    )
                    .where(
                        SourceReference.job_id == job_id,
                        RetrievalArtifact.id.is_(None),
                    )
                    .order_by(SourceReference.created_at, SourceReference.id)
                )
            )
            for value in values:
                value.metadata_json
                session.expunge(value)
            return values

    @staticmethod
    def _reference_value(reference: SourceReference) -> SourceReferenceValue:
        metadata = dict(reference.metadata_json or {})
        locator = metadata.pop("retrieval_locator", {})
        if reference.published_at is None:
            raise ValueError("persisted source reference lacks published_at")
        return SourceReferenceValue(
            adapter_key=reference.adapter_key,
            external_record_id=reference.external_record_id,
            external_version=reference.external_version,
            canonical_url=reference.canonical_url,
            title=reference.title,
            published_at=_as_utc(reference.published_at),
            source_role=reference.source_role,
            fetch_locator=locator,
            metadata=metadata,
        )

    def _fetch(self, claim, request) -> None:
        for reference in self._references_without_artifacts(claim.job_id):
            adapter = self._adapters.get(reference.adapter_key)
            if adapter is None:
                self._record_exception(
                    claim,
                    reason_code="adapter_not_injected",
                    detail={"adapter_key": reference.adapter_key},
                    reference_id=reference.id,
                )
                continue
            try:
                # Renew BEFORE each reference fetch: a single retrieval can
                # take minutes (large PDFs, slow mirrors), and a job can carry
                # dozens of references.  Keep the claim budget fresh and let
                # stale leases abort the run quietly.
                self._renew(claim)
            except StaleLeaseError:
                return
            started_at = self._now()
            try:
                value = self._reference_value(reference)
                adapter.descriptor.validate_reference(value)
                if reference.adapter_key not in self._searched_adapters:
                    adapter.restore_reference(value)
            except Exception as exc:
                code = _safe_error_code(exc)
                self._record_attempt(
                    claim,
                    adapter_key=reference.adapter_key,
                    operation="fetch",
                    started_at=started_at,
                    outcome="failed",
                    retryable=False,
                    metadata={"source_reference_id": str(reference.id)},
                    error_code=code,
                )
                self._record_exception(
                    claim,
                    reason_code="reference_restore_failed",
                    detail={
                        "adapter_key": reference.adapter_key,
                        "error_code": code,
                    },
                    reference_id=reference.id,
                )
                continue
            try:
                envelope = adapter.fetch(value)
            except SourceUnavailable as exc:
                self._record_attempt(
                    claim,
                    adapter_key=reference.adapter_key,
                    operation="fetch",
                    started_at=started_at,
                    outcome="failed",
                    retryable=exc.retryable,
                    metadata={
                        "source_reference_id": str(reference.id),
                        "diagnostics": _diagnostics_for_storage(exc.diagnostics),
                    },
                    error_code="SourceUnavailable",
                )
                self._record_exception(
                    claim,
                    reason_code="source_unavailable",
                    detail={
                        "adapter_key": reference.adapter_key,
                        "operation": "fetch",
                        "retryable": exc.retryable,
                        "claim_attempt": claim.attempt,
                        "diagnostics": _diagnostics_for_storage(exc.diagnostics),
                    },
                    reference_id=reference.id,
                )
                continue
            except Exception as exc:
                code = _safe_error_code(exc)
                self._record_attempt(
                    claim,
                    adapter_key=reference.adapter_key,
                    operation="fetch",
                    started_at=started_at,
                    outcome="failed",
                    retryable=False,
                    metadata={"source_reference_id": str(reference.id)},
                    error_code=code,
                )
                self._record_exception(
                    claim,
                    reason_code="fetch_failed",
                    detail={"adapter_key": reference.adapter_key, "error_code": code},
                    reference_id=reference.id,
                )
                continue
            self._freezer.checkpoint_fetch(
                reference.id,
                envelope,
                FetchCheckpointContext(
                    job_id=claim.job_id,
                    research_case_id=request.case_id,
                    tenant_id=request.tenant_id,
                    declared_actor=claim.lease_owner,
                    lease_token=claim.lease_token,
                    claim_attempt=claim.attempt,
                    retrieved_at=self._now(),
                ),
                started_at=started_at,
            )

    def _unbound_artifacts(self, job_id: uuid.UUID):
        with self._session_factory() as session:
            rows = tuple(
                session.execute(
                    select(RetrievalArtifact, SourceReference)
                    .join(
                        SourceReference,
                        SourceReference.id == RetrievalArtifact.source_reference_id,
                    )
                    .outerjoin(
                        RetrievalArtifactDocument,
                        RetrievalArtifactDocument.retrieval_artifact_id
                        == RetrievalArtifact.id,
                    )
                    .where(
                        SourceReference.job_id == job_id,
                        RetrievalArtifactDocument.id.is_(None),
                    )
                    .order_by(RetrievalArtifact.retrieved_at, RetrievalArtifact.id)
                )
            )
            result = []
            for artifact, reference in rows:
                artifact.raw_bytes
                reference.metadata_json
                session.expunge(artifact)
                session.expunge(reference)
                result.append((artifact, reference))
            return tuple(result)

    def _freeze(self, claim, request, policy) -> None:
        for artifact, reference in self._unbound_artifacts(claim.job_id):
            try:
                # Renew BEFORE each artifact freeze: pdf parsing + admission
                # can take minutes per artifact and a job can carry dozens.
                # Already-frozen artifacts are skipped on resume via the
                # unbound query above, so losing the lease here just stops
                # this run quietly.
                self._renew(claim)
            except StaleLeaseError:
                return
            envelope = RetrievedEnvelope(
                content=artifact.raw_bytes,
                mime_type=artifact.mime_type,
                final_url=artifact.final_url,
                etag=artifact.etag,
                last_modified=artifact.last_modified,
                provider_request_id=artifact.provider_request_id,
                metadata={},
            )
            self._freezer.freeze(
                reference.id,
                envelope,
                self._freeze_context(
                    claim, request, policy, reference, artifact.attempt_id
                ),
            )

    def _freeze_context(self, claim, request, policy, reference, attempt_id):
        declarations = dict(policy.permission_declarations)
        return FrozenRequestContext(
            job_id=claim.job_id,
            attempt_id=attempt_id,
            research_case_id=request.case_id,
            tenant_id=request.tenant_id,
            declared_actor=claim.lease_owner,
            lease_token=claim.lease_token,
            source_metadata={
                "permissions": {"ai_processing": True, "display": True},
                "region": "CN",
                "retention_policy": "case_retained",
                "deletion_policy": "not_recorded",
                "contract_version": declarations[reference.adapter_key],
            },
            retrieved_at=self._now(),
        )

    def _documents_for_job(self, job_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        with self._session_factory() as session:
            return tuple(
                session.scalars(
                    select(RetrievalArtifactDocument.document_version_id)
                    .join(
                        RetrievalArtifact,
                        RetrievalArtifact.id
                        == RetrievalArtifactDocument.retrieval_artifact_id,
                    )
                    .join(
                        SourceReference,
                        SourceReference.id == RetrievalArtifact.source_reference_id,
                    )
                    .where(SourceReference.job_id == job_id)
                    .order_by(RetrievalArtifactDocument.created_at)
                )
            )

    def _extract(self, claim: AcquisitionClaim) -> None:
        for document_id in self._documents_for_job(claim.job_id):
            if self._has_successful_extraction(document_id):
                continue
            # Renew BEFORE each document: one LLM extraction can take minutes,
            # and a job may carry dozens of documents, so the single lease
            # granted at claim time can never cover them all.
            self._renew(claim)
            session = self._session_factory()
            try:
                try:
                    self._extractor.extract(
                        document_id,
                        session,
                        pre_commit_guard=lambda guarded_session: lease_write_fence(
                            guarded_session,
                            job_id=claim.job_id,
                            lease_token=claim.lease_token,
                            now=self._now(),
                            allowed_stages=frozenset({"extracting"}),
                        ),
                    )
                except Exception as exc:
                    # A rotated lease surfaces here as StaleLeaseError; let
                    # it propagate so run_once / run_claim can decide whether
                    # to abandon the claim (the worker loop must keep polling
                    # — another worker now owns the job).
                    if isinstance(exc, StaleLeaseError):
                        raise
                    try:
                        AcquisitionRepository(session, clock=self._clock).fence(
                            claim.job_id, lease_token=claim.lease_token
                        )
                    except StaleLeaseError:
                        raise
                    session.commit()
                    self._record_exception(
                        claim,
                        reason_code="extraction_failed",
                        detail={"document_version_id": str(document_id)},
                    )
                    continue
                try:
                    AcquisitionRepository(session, clock=self._clock).fence(
                        claim.job_id, lease_token=claim.lease_token
                    )
                except StaleLeaseError:
                    # Extraction already committed its own durable work under
                    # its pre-commit guard; losing the lease afterwards only
                    # means another worker owns the job — propagate so the
                    # caller can stop quietly without crashing the worker.
                    raise
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()

    def _has_successful_extraction(self, document_id: uuid.UUID) -> bool:
        with self._session_factory() as session:
            runs = session.scalars(
                select(AIRun).where(AIRun.kind == "extract", AIRun.status == "success")
            )
            return any(
                isinstance(run.input_ref, dict)
                and run.input_ref.get("document_version_id") == str(document_id)
                for run in runs
            )

    def _candidate_lineages(self, job_id: uuid.UUID):
        with self._session_factory() as session:
            return tuple(
                session.execute(
                    select(AtomicClaimCandidate.id, RetrievalArtifact.id)
                    .join(
                        SourceSpan,
                        SourceSpan.id == AtomicClaimCandidate.source_span_id,
                    )
                    .join(
                        RetrievalArtifactDocument,
                        RetrievalArtifactDocument.document_version_id
                        == SourceSpan.document_version_id,
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
                    .where(SourceReference.job_id == job_id)
                    .order_by(AtomicClaimCandidate.created_at, AtomicClaimCandidate.id)
                )
            )

    def _admit_and_publish(self, claim, request) -> None:
        for candidate_id, artifact_id in self._candidate_lineages(claim.job_id):
            with self._session_factory() as session:
                decision = session.scalar(
                    select(AutomaticAdmissionDecision).where(
                        AutomaticAdmissionDecision.job_id == claim.job_id,
                        AutomaticAdmissionDecision.candidate_id == candidate_id,
                        AutomaticAdmissionDecision.gate_version
                        == B_SCOPE_GATE_VERSION,
                        AutomaticAdmissionDecision.policy_version
                        == request.source_policy_version,
                    )
                )
                if decision is None:
                    decision = AutomaticAdmissionGate(
                        session, clock=self._clock
                    ).evaluate(
                        candidate_id,
                        AdmissionContext(
                            job_id=claim.job_id,
                            retrieval_artifact_id=artifact_id,
                            thesis_id=request.thesis_id,
                            cutoff=request.cutoff,
                            objective=request.objective.value,
                            target_link_role=request.target_link_role,
                            gate_version=B_SCOPE_GATE_VERSION,
                            policy_version=request.source_policy_version,
                            allowed_source_roles=request.allowed_source_roles,
                            metric_terms=request.metric_terms,
                            expected_subject=(
                                request.entity_names[0]
                                if request.entity_names
                                else None
                            ),
                            lease_token=claim.lease_token,
                        ),
                    )
                decision_id = decision.id
                outcome = decision.outcome
                session.commit()
            if outcome != "admitted":
                continue
            with self._session_factory() as session:
                existing = session.scalar(
                    select(EvidenceLink.id).where(
                        EvidenceLink.automatic_admission_decision_id == decision_id
                    )
                )
                if existing is None:
                    AtomicClaimService(
                        session, clock=self._clock
                    ).publish_automatically(
                        candidate_id,
                        decision_id,
                        lease_token=claim.lease_token,
                    )
                session.commit()

    def _counts(self, job_id: uuid.UUID) -> dict[str, int]:
        with self._session_factory() as session:
            reference_count = session.scalar(
                select(func.count()).select_from(SourceReference).where(
                    SourceReference.job_id == job_id
                )
            ) or 0
            fetched_count = session.scalar(
                select(func.count()).select_from(RetrievalArtifact).join(
                    SourceReference,
                    SourceReference.id == RetrievalArtifact.source_reference_id,
                ).where(SourceReference.job_id == job_id)
            ) or 0
            frozen_count = session.scalar(
                select(func.count()).select_from(RetrievalArtifactDocument).join(
                    RetrievalArtifact,
                    RetrievalArtifact.id
                    == RetrievalArtifactDocument.retrieval_artifact_id,
                ).join(
                    SourceReference,
                    SourceReference.id == RetrievalArtifact.source_reference_id,
                ).where(SourceReference.job_id == job_id)
            ) or 0
            admitted_count = session.scalar(
                select(func.count()).select_from(AutomaticAdmissionDecision).where(
                    AutomaticAdmissionDecision.job_id == job_id,
                    AutomaticAdmissionDecision.outcome == "admitted",
                )
            ) or 0
            exception_count = session.scalar(
                select(func.count()).select_from(AcquisitionException).where(
                    AcquisitionException.job_id == job_id
                )
            ) or 0
        return {
            "reference_count": reference_count,
            "fetched_count": fetched_count,
            "frozen_count": frozen_count,
            "admitted_count": admitted_count,
            "exception_count": exception_count,
        }

    def _advance(self, claim: AcquisitionClaim, stage: str) -> None:
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).advance(
                claim.job_id,
                lease_token=claim.lease_token,
                stage=stage,
                counters=self._counts(claim.job_id),
            )
            session.commit()

    def _has_references(self, job_id: uuid.UUID) -> bool:
        return self._counts(job_id)["reference_count"] > 0

    def _has_artifacts(self, job_id: uuid.UUID) -> bool:
        return self._counts(job_id)["fetched_count"] > 0

    @staticmethod
    def _retry_after_seconds(value: object, *, policy_cap: float) -> float | None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            return None
        return min(float(value), policy_cap)

    @classmethod
    def _retry_after_from_metadata(
        cls, metadata: object, *, policy_cap: float
    ) -> float | None:
        if not isinstance(metadata, Mapping):
            return None
        candidates = [metadata.get("retry_after_seconds")]
        diagnostics = metadata.get("diagnostics")
        if isinstance(diagnostics, Mapping):
            candidates.append(diagnostics.get("retry_after_seconds"))
        values: list[float] = []
        for candidate in candidates:
            parsed = cls._retry_after_seconds(candidate, policy_cap=policy_cap)
            if parsed is not None:
                values.append(parsed)
        return max(values, default=None)

    def _retryable_failure_policy(
        self, job_id: uuid.UUID, *, claim_attempt: int
    ) -> tuple[bool, float]:
        policy_cap = self._max_retry_delay.total_seconds()
        with self._session_factory() as session:
            attempts = tuple(
                session.scalars(
                    select(AcquisitionAttempt).where(
                        AcquisitionAttempt.job_id == job_id,
                        AcquisitionAttempt.outcome == "failed",
                        AcquisitionAttempt.retryable.is_(True),
                    )
                )
            )
            exceptions = tuple(
                session.scalars(
                    select(AcquisitionException).where(
                        AcquisitionException.job_id == job_id,
                        AcquisitionException.reason_code == "source_unavailable",
                    )
                )
            )
        current_attempts = tuple(
            attempt
            for attempt in attempts
            if isinstance(attempt.safe_metadata, dict)
            and attempt.safe_metadata.get("claim_attempt") == claim_attempt
        )
        retry_after_values: list[float] = []
        for attempt in current_attempts:
            value = self._retry_after_from_metadata(
                attempt.safe_metadata, policy_cap=policy_cap
            )
            if value is not None:
                retry_after_values.append(value)
        for exception in exceptions:
            detail = exception.detail_json
            if (
                not isinstance(detail, dict)
                or detail.get("claim_attempt") != claim_attempt
                or detail.get("retryable") is not True
            ):
                continue
            value = self._retry_after_from_metadata(detail, policy_cap=policy_cap)
            if value is not None:
                retry_after_values.append(value)
        return bool(current_attempts), max(retry_after_values, default=0.0)

    def _retry_backoff(self, claim_attempt: int, provider_minimum: float) -> timedelta:
        exponential = min(
            self._retry_delay * (2 ** (claim_attempt - 1)),
            self._max_retry_delay,
        ).total_seconds()
        policy_cap = self._max_retry_delay.total_seconds()
        sample = self._jitter_source()
        if (
            isinstance(sample, bool)
            or not isinstance(sample, (int, float))
            or not math.isfinite(sample)
            or not 0 <= sample <= 1
        ):
            raise ValueError(
                "jitter_source must return a finite value from zero to one"
            )
        jitter_room = min(
            exponential * self._retry_jitter_ratio,
            policy_cap - exponential,
        )
        jittered = exponential + jitter_room * float(sample)
        return timedelta(seconds=max(jittered, provider_minimum))

    def _finish_without_artifacts(self, claim: AcquisitionClaim, *, stage: str) -> None:
        retryable, provider_minimum = self._retryable_failure_policy(
            claim.job_id, claim_attempt=claim.attempt
        )
        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            if retryable and claim.attempt < self._max_attempts:
                backoff = self._retry_backoff(claim.attempt, provider_minimum)
                repository.advance(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    stage=stage,
                    status="retry_wait",
                    retry_at=self._now() + backoff,
                    counters=self._counts(claim.job_id),
                    message="acquisition waiting for retryable provider",
                )
            else:
                repository.advance(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    stage="failed",
                    status="failed",
                    counters=self._counts(claim.job_id),
                    message="acquisition produced no retrievable result",
                )
            session.commit()

    def _finish_terminal(self, claim: AcquisitionClaim) -> None:
        counts = self._counts(claim.job_id)
        if counts["admitted_count"] and not counts["exception_count"]:
            terminal = "succeeded"
        elif counts["frozen_count"] or counts["admitted_count"]:
            terminal = "partial"
        else:
            terminal = "failed"
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).advance(
                claim.job_id,
                lease_token=claim.lease_token,
                stage=terminal,
                status=terminal,
                counters=counts,
                message=f"acquisition {terminal}",
            )
            session.commit()
