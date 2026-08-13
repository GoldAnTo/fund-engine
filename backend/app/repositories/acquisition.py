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

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.acquisition import (
    AcquisitionJobRef,
    AcquisitionJobView,
    AdmittedEvidenceRef,
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
        return AcquisitionJobView(
            id=job.id,
            status=job.status,
            stage=job.stage,
            attempt=job.attempt,
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

    def _by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> AcquisitionJob | None:
        return self._session.scalar(
            select(AcquisitionJob).where(
                AcquisitionJob.tenant_id == tenant_id,
                AcquisitionJob.idempotency_key == idempotency_key,
            )
        )

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
        event = AcquisitionJobEvent(
            job_id=job.id,
            seq=(last_seq or 0) + 1,
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
