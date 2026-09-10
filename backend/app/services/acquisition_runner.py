"""Checkpointed orchestration for governed acquisition claims.

Every helper opens one short database unit and closes it before source or model
I/O. PostgreSQL remains the sole recovery state: references, attempts,
artifacts, bindings, extraction runs, decisions, and publications are all
re-read when a lease is reclaimed.
"""
from __future__ import annotations

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
from app.ai.prompts import EXTRACT_PROMPT_VERSION
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionRequest,
    EvidenceObjective,
    PlannedQuery,
    QueryPlanExpansion,
)
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import AIRun, AtomicClaimCandidate, EvidenceLink, SourceSpan
from app.repositories.acquisition import (
    AcquisitionClaim,
    AcquisitionRepository,
)
from app.services.atomic_claims import AtomicClaimService
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
    lease_write_fence,
)
from app.services.retrieved_documents import (
    FetchCheckpointContext,
    FrozenRequestContext,
    RetrievedDocumentFreezer,
)


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
    if isinstance(value, float) and not math.isfinite(value):
        # SQLite's JSON adapter accepts NaN/Infinity while PostgreSQL JSON
        # correctly rejects them. Provider diagnostics are advisory only, so
        # preserve the key but normalize a non-JSON number to null before any
        # durable attempt/exception write.
        return None
    return value


def _safe_error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:128] or "ProviderError"


class _InvalidQueryPlan(ValueError):
    def __init__(self, validation_error: str) -> None:
        super().__init__(validation_error)
        self.validation_error = validation_error


class AcquisitionRunner:
    """Resume one lease-fenced acquisition claim from durable checkpoints."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        adapters: Mapping[str, SourceAdapter],
        llm_client: LLMClient,
        freezer: RetrievedDocumentFreezer | None = None,
        clock: Callable[[], datetime] = _utcnow,
        retry_delay: timedelta = timedelta(minutes=1),
        max_retry_delay: timedelta = timedelta(minutes=15),
        retry_jitter_ratio: float = 0.2,
        jitter_source: JitterSource = _SYSTEM_RANDOM.random,
        max_attempts: int = 3,
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
        self._clock = clock
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._retry_jitter_ratio = float(retry_jitter_ratio)
        self._jitter_source = jitter_source
        self._max_attempts = max_attempts
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
        request, policy, stage, frozen_queries = contract
        if not self._configured_adapters_are_safe(claim, policy):
            return
        if not self._fetch_checkpoints_are_valid(claim):
            return
        queries = {
            planned.adapter_key: planned
            for planned in frozen_queries
            if planned.adapter_key in self._adapters
        }
        if stage == "searching":
            self._search(claim, request, queries, policy)
            if not self._has_references(claim.job_id):
                self._finish_without_artifacts(
                    claim, request=request, stage="searching"
                )
                return
            self._advance(claim, "fetching")
            stage = "fetching"
        if stage == "fetching":
            self._fetch(claim, request)
            if not self._has_artifacts(claim.job_id):
                self._finish_without_artifacts(
                    claim, request=request, stage="fetching"
                )
                return
            self._advance(claim, "freezing")
            stage = "freezing"
        if stage == "freezing":
            self._freeze(claim, request, policy)
            self._advance(claim, "extracting")
            stage = "extracting"
        if stage == "extracting":
            self._extract(claim, policy, request)
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
    ) -> tuple[
        AcquisitionRequest, SourcePolicy, str, tuple[PlannedQuery, ...]
    ] | None:
        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            job = repository.fence(
                claim.job_id,
                lease_token=claim.lease_token,
                allowed_stages=_RUNNING_STAGES,
            )
            plan = repository.query_plan_for_job(job.id)
            series = repository.series(plan.series_id) if plan is not None else None
            stage = job.stage
            try:
                if not isinstance(job.request_snapshot, Mapping):
                    raise _InvalidQueryPlan("invalid_request_snapshot_root")
                if not isinstance(job.policy_snapshot, Mapping):
                    raise _InvalidQueryPlan("invalid_policy_snapshot_root")
                request_snapshot = dict(job.request_snapshot)
                policy_snapshot = dict(job.policy_snapshot)
            except _InvalidQueryPlan as exc:
                self._fail_invalid_query_plan(
                    session,
                    repository,
                    claim,
                    validation_error=exc.validation_error,
                )
                session.commit()
                return None
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
            try:
                if plan is None or series is None:
                    raise ValueError("missing persisted query plan or series")
                plan_id = uuid.UUID(str(request_snapshot["query_plan_id"]))
                run_id = uuid.UUID(str(request_snapshot["research_run_id"]))
                scope_id = uuid.UUID(str(request_snapshot["scope_version_id"]))
                thesis_id = uuid.UUID(str(request_snapshot["thesis_id"]))
                case_id = uuid.UUID(str(request_snapshot["case_id"]))
                round_value = int(request_snapshot["round"])
                goal_id = str(request_snapshot["goal_id"])
                planner_version = str(request_snapshot["planner_version"])
                previous_plan_id = (
                    uuid.UUID(str(request_snapshot["previous_query_plan_id"]))
                    if request_snapshot.get("previous_query_plan_id")
                    else None
                )
                expected_inputs = {
                    key: value
                    for key, value in request_snapshot.items()
                    if key != "query_plan_id"
                }
                if (
                    plan.id != plan_id
                    or plan.acquisition_job_id != job.id
                    or plan.series_id != series.id
                    or plan.acquisition_round != round_value
                    or plan.goal_id != goal_id
                    or plan.planner_version != planner_version
                    or planner_version != ACQUISITION_PLANNER_VERSION
                    or plan.policy_version != request_version
                    or plan.previous_query_plan_id != previous_plan_id
                    or plan.frozen_inputs_json != expected_inputs
                    or series.tenant_id != job.tenant_id
                    or series.research_case_id != case_id
                    or series.research_run_id != run_id
                    or series.scope_version_id != scope_id
                    or series.thesis_id != thesis_id
                    or series.goal_id != goal_id
                    or job.research_case_id != case_id
                    or job.research_run_id != run_id
                    or job.thesis_id != thesis_id
                ):
                    raise ValueError("persisted query plan binding mismatch")
                expansion_value = request_snapshot.get("expansion")
                expansion = (
                    QueryPlanExpansion(
                        trigger=expansion_value["trigger"],
                        reason=expansion_value["reason"],
                    )
                    if isinstance(expansion_value, dict)
                    else None
                )
                predecessor = (
                    repository.query_plan(plan.previous_query_plan_id)
                    if plan.previous_query_plan_id is not None
                    else None
                )
                self._validate_query_plan_lineage(
                    plan=plan,
                    predecessor=predecessor,
                    expansion=expansion,
                )
                request = AcquisitionRequest(
                    tenant_id=request_snapshot["tenant_id"],
                    case_id=case_id,
                    thesis_id=thesis_id,
                    research_run_id=run_id,
                    scope_version_id=scope_id,
                    goal_id=goal_id,
                    round=round_value,
                    objective=EvidenceObjective(request_snapshot["objective"]),
                    target_link_role=request_snapshot["target_link_role"],
                    thesis_statement=request_snapshot["thesis_statement"],
                    entity_names=tuple(request_snapshot.get("entity_names") or ()),
                    security_codes=tuple(request_snapshot.get("security_codes") or ()),
                    metric_terms=tuple(request_snapshot.get("metric_terms") or ()),
                    metric_periods=tuple(
                        request_snapshot.get("metric_periods") or ()
                    ),
                    metric_units=tuple(request_snapshot.get("metric_units") or ()),
                    period_start=request_snapshot["period_start"],
                    period_end=request_snapshot["period_end"],
                    cutoff=_parse_datetime(request_snapshot["cutoff"], "cutoff"),
                    allowed_source_roles=frozenset(
                        request_snapshot.get("allowed_source_roles") or ()
                    ),
                    source_policy_version=request_snapshot["source_policy_version"],
                    planner_version=planner_version,
                    previous_query_plan_id=previous_plan_id,
                    expansion=expansion,
                    idempotency_key=request_snapshot["idempotency_key"],
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
                    per_adapter_page_limit=int(
                        policy_snapshot["per_adapter_page_limit"]
                    ),
                    permission_declarations=tuple(
                        tuple(item)
                        for item in policy_snapshot.get("permission_declarations") or ()
                    ),
                )
                ordered_queries = tuple(
                    PlannedQuery(
                        adapter_key=item["adapter_key"],
                        objective=EvidenceObjective(item["objective"]),
                        query=item["query"],
                    )
                    for item in plan.ordered_queries_json
                )
                adapter_keys = tuple(query.adapter_key for query in ordered_queries)
                if (
                    adapter_keys != tuple(sorted(policy.enabled_adapter_keys))
                    or len(set(adapter_keys)) != len(adapter_keys)
                    or any(
                        query.objective is not request.objective
                        or not query.query.strip()
                        for query in ordered_queries
                    )
                ):
                    raise ValueError("persisted query plan exceeds frozen policy")
            except (KeyError, TypeError, ValueError) as exc:
                validation_error = (
                    exc.validation_error
                    if isinstance(exc, _InvalidQueryPlan)
                    else "frozen_plan_binding_mismatch"
                )
                self._fail_invalid_query_plan(
                    session,
                    repository,
                    claim,
                    validation_error=validation_error,
                )
                session.commit()
                return None
            session.commit()
        return request, policy, stage, ordered_queries

    def _fail_invalid_query_plan(
        self,
        session: Session,
        repository: AcquisitionRepository,
        claim: AcquisitionClaim,
        *,
        validation_error: str,
    ) -> None:
        repository.record_exception(
            claim.job_id,
            lease_token=claim.lease_token,
            reason_code="invalid_query_plan",
            detail_json={
                "active_policy_version": B_SCOPE_POLICY.version,
                "active_planner_version": ACQUISITION_PLANNER_VERSION,
                "validation_error": validation_error,
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
            message="acquisition query plan failed closed",
            payload={
                "reason_code": "invalid_query_plan",
                "validation_error": validation_error,
            },
            error_code="invalid_query_plan",
        )

    @staticmethod
    def _validate_query_plan_lineage(*, plan, predecessor, expansion) -> None:
        if plan.acquisition_round == 1:
            if (
                predecessor is not None
                or plan.previous_query_plan_id is not None
                or plan.previous_acquisition_round is not None
                or plan.expansion_trigger is not None
                or plan.diff_json is not None
                or expansion is not None
            ):
                raise _InvalidQueryPlan("unexpected_initial_round_lineage")
            return

        if predecessor is None:
            raise _InvalidQueryPlan("previous_plan_missing")
        if predecessor.series_id != plan.series_id:
            raise _InvalidQueryPlan("previous_plan_series_mismatch")
        if (
            predecessor.id != plan.previous_query_plan_id
            or predecessor.acquisition_round != plan.acquisition_round - 1
            or plan.previous_acquisition_round != plan.acquisition_round - 1
        ):
            raise _InvalidQueryPlan("previous_plan_round_mismatch")
        if (
            not isinstance(plan.expansion_trigger, str)
            or not plan.expansion_trigger.strip()
        ):
            raise _InvalidQueryPlan("expansion_trigger_missing")
        if expansion is None or plan.expansion_trigger != expansion.trigger:
            raise _InvalidQueryPlan("expansion_trigger_mismatch")
        if not isinstance(plan.diff_json, dict):
            raise _InvalidQueryPlan("query_plan_diff_mismatch")
        persisted_reason = plan.diff_json.get("reason")
        if not isinstance(persisted_reason, str) or not persisted_reason.strip():
            raise _InvalidQueryPlan("expansion_reason_missing")

        current_queries = list(plan.ordered_queries_json)
        previous_queries = list(predecessor.ordered_queries_json)
        expected_diff = {
            "added_queries": [
                item for item in current_queries if item not in previous_queries
            ],
            "removed_queries": [
                item for item in previous_queries if item not in current_queries
            ],
            "reason": expansion.reason,
        }
        if plan.diff_json != expected_diff:
            raise _InvalidQueryPlan("query_plan_diff_mismatch")

    def _fence(self, claim: AcquisitionClaim) -> None:
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).fence(
                claim.job_id, lease_token=claim.lease_token
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

    def _record_event(
        self,
        claim: AcquisitionClaim,
        *,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            AcquisitionRepository(session, clock=self._clock).record_event(
                claim.job_id,
                lease_token=claim.lease_token,
                message=message,
                payload=payload,
            )
            session.commit()

    @staticmethod
    def _retry_event_payload(
        request: AcquisitionRequest,
        *,
        adapter_key: str,
        operation: str,
        reason_code: str,
        retry_delay_seconds: float,
        query_index: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "adapter_key": adapter_key,
            "goal_id": request.goal_id,
            "operation": operation,
            "policy_version": request.source_policy_version,
            "reason_code": reason_code,
            "retry_delay_seconds": retry_delay_seconds,
        }
        if query_index is not None:
            payload["query_index"] = query_index
        return payload

    def _search(self, claim, request, queries, policy: SourcePolicy) -> None:
        for query_index, (adapter_key, planned) in enumerate(queries.items()):
            adapter = self._adapters[adapter_key]
            self._fence(claim)
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
                    metadata={
                        "diagnostics": _thaw(exc.diagnostics),
                        "query_index": query_index,
                    },
                    error_code="SourceUnavailable",
                )
                self._record_exception(
                    claim,
                    reason_code="source_unavailable",
                    detail={
                        "adapter_key": adapter_key,
                        "operation": "search",
                        "query_index": query_index,
                        "retryable": exc.retryable,
                        "claim_attempt": claim.attempt,
                        "diagnostics": _thaw(exc.diagnostics),
                    },
                )
                self._record_event(
                    claim,
                    message="retry_source_switch",
                    payload=self._retry_event_payload(
                        request,
                        adapter_key=adapter_key,
                        operation="search",
                        query_index=query_index,
                        reason_code="source_unavailable",
                        retry_delay_seconds=0.0,
                    ),
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
                    metadata={"query_index": query_index},
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
                metadata={"item_count": len(items), "query_index": query_index},
            )
            self._record_event(
                claim,
                message="search_result_page",
                payload={
                    "adapter_key": adapter_key,
                    "goal_id": request.goal_id,
                    "policy_version": request.source_policy_version,
                    "query_index": query_index,
                    "result_count": len(items),
                },
            )
            self._searched_adapters.add(adapter_key)
            accepted_count = 0
            omitted_count = 0
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
                if accepted_count >= policy.per_adapter_page_limit:
                    omitted_count += 1
                    continue
                accepted_count += 1
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
                metadata["acquisition_query_index"] = query_index
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
            if omitted_count:
                self._record_event(
                    claim,
                    message="search_results_bounded",
                    payload={
                        "adapter_key": adapter_key,
                        "goal_id": request.goal_id,
                        "policy_version": request.source_policy_version,
                        "query_index": query_index,
                        "accepted_count": accepted_count,
                        "omitted_count": omitted_count,
                    },
                )

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
        metadata.pop("acquisition_query_index", None)
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
            reference_metadata = dict(reference.metadata_json or {})
            stored_query_index = reference_metadata.get("acquisition_query_index")
            query_index = (
                stored_query_index
                if isinstance(stored_query_index, int)
                and not isinstance(stored_query_index, bool)
                else None
            )
            adapter = self._adapters.get(reference.adapter_key)
            if adapter is None:
                self._record_exception(
                    claim,
                    reason_code="adapter_not_injected",
                    detail={"adapter_key": reference.adapter_key},
                    reference_id=reference.id,
                )
                continue
            self._fence(claim)
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
                        "diagnostics": _thaw(exc.diagnostics),
                    },
                    error_code="SourceUnavailable",
                )
                self._record_exception(
                    claim,
                    reason_code="source_unavailable",
                    detail={
                        "adapter_key": reference.adapter_key,
                        "operation": "fetch",
                        **(
                            {"query_index": query_index}
                            if query_index is not None
                            else {}
                        ),
                        "retryable": exc.retryable,
                        "claim_attempt": claim.attempt,
                        "diagnostics": _thaw(exc.diagnostics),
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

    def _documents_for_job(
        self, job_id: uuid.UUID, *, per_adapter_limit: int
    ) -> tuple[uuid.UUID, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                    select(
                        SourceReference.adapter_key,
                        RetrievalArtifactDocument.document_version_id,
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
                    .order_by(
                        SourceReference.adapter_key,
                        RetrievalArtifactDocument.created_at,
                        RetrievalArtifactDocument.document_version_id,
                    )
            )
            counts: dict[str, int] = {}
            selected: list[uuid.UUID] = []
            for adapter_key, document_id in rows:
                count = counts.get(adapter_key, 0)
                if count >= per_adapter_limit:
                    continue
                counts[adapter_key] = count + 1
                selected.append(document_id)
            return tuple(selected)

    def _extract(
        self,
        claim: AcquisitionClaim,
        policy: SourcePolicy,
        request: AcquisitionRequest,
    ) -> None:
        grounding_context = {
            "entity_names": list(request.entity_names),
            "metric_terms": list(request.metric_terms),
            "period_start": request.period_start,
            "period_end": request.period_end,
        }
        for document_id in self._documents_for_job(
            claim.job_id,
            per_adapter_limit=policy.per_adapter_page_limit,
        ):
            if self._has_successful_extraction(document_id, grounding_context):
                continue
            self._fence(claim)
            session = self._session_factory()
            try:
                try:
                    self._extractor.extract(
                        document_id,
                        session,
                        grounding_context=grounding_context,
                        pre_commit_guard=lambda guarded_session: lease_write_fence(
                            guarded_session,
                            job_id=claim.job_id,
                            lease_token=claim.lease_token,
                            now=self._now(),
                            allowed_stages=frozenset({"extracting"}),
                        ),
                    )
                except Exception:
                    AcquisitionRepository(session, clock=self._clock).fence(
                        claim.job_id, lease_token=claim.lease_token
                    )
                    session.commit()
                    self._record_exception(
                        claim,
                        reason_code="extraction_failed",
                        detail={"document_version_id": str(document_id)},
                    )
                    continue
                AcquisitionRepository(session, clock=self._clock).fence(
                    claim.job_id, lease_token=claim.lease_token
                )
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                session.close()

    def _has_successful_extraction(
        self,
        document_id: uuid.UUID,
        grounding_context: Mapping[str, object],
    ) -> bool:
        expected_context = _thaw(grounding_context)
        with self._session_factory() as session:
            runs = session.scalars(
                select(AIRun).where(
                    AIRun.kind == "extract",
                    AIRun.status.in_(("success", "partial")),
                    AIRun.prompt_version == EXTRACT_PROMPT_VERSION,
                )
            )
            return any(
                isinstance(run.input_ref, dict)
                and run.input_ref.get("document_version_id") == str(document_id)
                and run.input_ref.get("grounding_context") == expected_context
                and (
                    run.status == "success"
                    or run.input_ref.get("rule_fallback") is True
                )
                for run in runs
            )

    def _candidate_lineages(self, job_id: uuid.UUID):
        with self._session_factory() as session:
            job = session.get(AcquisitionJob, job_id)
            snapshot = (
                job.request_snapshot
                if job is not None and isinstance(job.request_snapshot, dict)
                else {}
            )
            expected_context = {
                "entity_names": list(snapshot.get("entity_names") or ()),
                "metric_terms": list(snapshot.get("metric_terms") or ()),
                "period_start": snapshot.get("period_start"),
                "period_end": snapshot.get("period_end"),
            }
            eligible_runs: dict[str, str] = {}
            for run in session.scalars(
                select(AIRun).where(
                    AIRun.kind == "extract",
                    AIRun.status.in_(("success", "partial")),
                    AIRun.prompt_version == EXTRACT_PROMPT_VERSION,
                )
            ):
                input_ref = run.input_ref if isinstance(run.input_ref, dict) else {}
                document_id = input_ref.get("document_version_id")
                if (
                    input_ref.get("grounding_context") == expected_context
                    and isinstance(document_id, str)
                    and (
                        run.status == "success"
                        or input_ref.get("rule_fallback") is True
                    )
                ):
                    eligible_runs[f"extract:{run.id}"] = document_id

            rows = session.execute(
                    select(
                        AtomicClaimCandidate.id,
                        RetrievalArtifact.id,
                        AtomicClaimCandidate.structured_fields,
                        SourceSpan.document_version_id,
                    )
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
            return tuple(
                (candidate_id, artifact_id)
                for candidate_id, artifact_id, structured_fields, document_id in rows
                if isinstance(structured_fields, dict)
                and (
                    eligible_runs.get(structured_fields.get("run_ref"))
                    == str(document_id)
                    or (
                        str(document_id) in eligible_runs.values()
                        and isinstance(structured_fields.get("scope"), dict)
                        and structured_fields["scope"].get("extraction_method")
                        == "financial_table_v1"
                    )
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
    ) -> tuple[bool, float, dict[str, Any] | None]:
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
        current_exceptions = tuple(
            exception
            for exception in exceptions
            if isinstance(exception.detail_json, dict)
            and exception.detail_json.get("claim_attempt") == claim_attempt
            and exception.detail_json.get("retryable") is True
        )
        retry_after_values: list[float] = []
        for attempt in current_attempts:
            value = self._retry_after_from_metadata(
                attempt.safe_metadata, policy_cap=policy_cap
            )
            if value is not None:
                retry_after_values.append(value)
        for exception in current_exceptions:
            detail = exception.detail_json
            value = self._retry_after_from_metadata(detail, policy_cap=policy_cap)
            if value is not None:
                retry_after_values.append(value)
        context = None
        if current_exceptions:
            detail = current_exceptions[-1].detail_json
            query_index = detail.get("query_index")
            context = {
                "adapter_key": detail.get("adapter_key"),
                "operation": detail.get("operation"),
                "reason_code": "source_unavailable",
                "query_index": (
                    query_index
                    if isinstance(query_index, int)
                    and not isinstance(query_index, bool)
                    else None
                ),
            }
        elif current_attempts:
            attempt = current_attempts[-1]
            query_index = attempt.safe_metadata.get("query_index")
            context = {
                "adapter_key": attempt.adapter_key,
                "operation": attempt.operation,
                "reason_code": "source_unavailable",
                "query_index": (
                    query_index
                    if isinstance(query_index, int)
                    and not isinstance(query_index, bool)
                    else None
                ),
            }
        return (
            bool(current_attempts or current_exceptions),
            max(retry_after_values, default=0.0),
            context,
        )

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

    def _finish_without_artifacts(
        self,
        claim: AcquisitionClaim,
        *,
        request: AcquisitionRequest,
        stage: str,
    ) -> None:
        retryable, provider_minimum, retry_context = self._retryable_failure_policy(
            claim.job_id, claim_attempt=claim.attempt
        )
        with self._session_factory() as session:
            repository = AcquisitionRepository(session, clock=self._clock)
            if retryable and claim.attempt < self._max_attempts:
                backoff = self._retry_backoff(claim.attempt, provider_minimum)
                if (
                    retry_context is None
                    or not isinstance(retry_context.get("adapter_key"), str)
                    or not isinstance(retry_context.get("operation"), str)
                ):
                    raise ValueError("retryable provider failure lacks safe context")
                repository.advance(
                    claim.job_id,
                    lease_token=claim.lease_token,
                    stage=stage,
                    status="retry_wait",
                    retry_at=self._now() + backoff,
                    counters=self._counts(claim.job_id),
                    message="retry_source_switch",
                    payload=self._retry_event_payload(
                        request,
                        adapter_key=retry_context["adapter_key"],
                        operation=retry_context["operation"],
                        query_index=retry_context.get("query_index"),
                        reason_code=retry_context["reason_code"],
                        retry_delay_seconds=backoff.total_seconds(),
                    ),
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
