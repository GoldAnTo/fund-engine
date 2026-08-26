"""Persistence primitives for append-only company-research workbench data."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.persistence.company_research_models import (
    COMPANY_RESEARCH_ARTIFACT_KINDS,
    COMPANY_RESEARCH_PREPARATION_STATUSES,
    COMPANY_RESEARCH_PREPARATION_STEPS,
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.hashing import canonical_hash

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PREPARE_JOB_KIND = "prepare_company_research"
_PREPARE_JOB_TARGET_TYPE = "company_research_preparation"


class CompanyResearchIntegrityError(ValidationError):
    """A persisted immutable company-research record cannot be trusted."""


class CompanyResearchRepository:
    """Flush-only operations which retain ownership of the caller transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _reserve_sqlite_writer_before_ownership_read(self) -> None:
        """Serialize SQLite ownership validation without taking over caller work."""
        connection = self._session.connection()
        if connection.dialect.name != "sqlite":
            return
        dbapi_connection = getattr(
            connection.connection, "driver_connection", connection.connection
        )
        # SQLAlchemy can expose a logical transaction before sqlite3 has opened
        # a physical one.  In that state we can still reserve the sole writer
        # before the Job/preparation read.  Once the caller owns a physical
        # transaction, do not begin, commit, or replace it here.
        if not dbapi_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    def _reserve_sqlite_writer_before_artifact_head_read(self) -> None:
        """Reserve SQLite's writer before calculating an artifact successor."""
        try:
            self._reserve_sqlite_writer_before_ownership_read()
        except OperationalError as exc:
            raise StaleParentError(
                "expected parent is not the company artifact head"
            ) from exc

    def _reserve_sqlite_writer_before_event_append(self) -> None:
        """Serialize an event sequence without committing caller-owned work."""
        try:
            self._reserve_sqlite_writer_before_ownership_read()
        except OperationalError as exc:
            raise ConflictError("company research event append is concurrent") from exc

    def _job_for_update(self, job_id: UUID) -> Job | None:
        """Read a Job under the caller transaction's row lock when supported."""
        return self._session.scalar(
            select(Job).where(Job.id == job_id).with_for_update()
        )

    def _preparation_for_update(
        self, preparation_id: UUID
    ) -> CompanyResearchPreparation | None:
        """Serialize competing attachments to the mutable preparation row."""
        return self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.id == preparation_id)
            .with_for_update()
        )

    def _locked_prepare_job(
        self, preparation: CompanyResearchPreparation
    ) -> Job:
        """Lock the Job owned by an already-locked preparation exactly."""
        if preparation.job_id is None:
            raise CompanyResearchIntegrityError(
                "company research preparation job is missing"
            )
        job = self._job_for_update(preparation.job_id)
        if not self._is_exact_prepare_job_owner(job, preparation.id):
            raise CompanyResearchIntegrityError(
                "company research preparation job ownership is invalid"
            )
        return job

    @staticmethod
    def _require_preparation_step(value: str | None, field: str) -> str:
        if value not in COMPANY_RESEARCH_PREPARATION_STEPS:
            raise ValidationError(f"{field} is not a company research preparation step")
        return value

    @staticmethod
    def _validate_prepare_job_step(
        *,
        preparation_step: str | None,
        preparation_status: str,
        job: Job,
        persisted: bool,
    ) -> None:
        error_type = CompanyResearchIntegrityError if persisted else ValidationError
        if preparation_step not in COMPANY_RESEARCH_PREPARATION_STEPS:
            raise error_type("company research preparation step is invalid")
        if job.step != preparation_step:
            raise error_type("company research preparation job step is invalid")
        expected_job_statuses = {
            "queued": frozenset({"queued"}),
            # A legacy/manual retry records a failed Job, while the worker's
            # automatic retry keeps its owned Job queued behind backoff.
            "recoverable_failure": frozenset({"failed", "queued"}),
        }.get(preparation_status)
        if expected_job_statuses is not None and job.status not in expected_job_statuses:
            raise error_type("company research preparation job status is invalid")

    def _flush_in_savepoint(self, row: Any) -> Any:
        """Flush without committing or poisoning an outer SQLite transaction."""
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            dbapi_connection = getattr(
                connection.connection, "driver_connection", connection.connection
            )
            if not dbapi_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        with self._session.begin_nested():
            self._session.add(row)
            self._session.flush([row])
        return row

    @staticmethod
    def _require_hash(value: str, field: str) -> str:
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            raise ValidationError(f"{field} must be a lowercase SHA-256 hash")
        return value

    @staticmethod
    def _stored_datetime(value: datetime, field: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _require_nonempty_text(value: str, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not (cleaned := value.strip()):
            raise ValidationError(f"{field} must not be empty")
        if len(cleaned) > maximum:
            raise ValidationError(f"{field} is too long")
        return cleaned

    @staticmethod
    def _artifact_payload(
        *,
        project_id: UUID,
        kind: str,
        version: int,
        supersedes_id: UUID | None,
        parent_content_hash: str | None,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        return {
            "schema_version": "company-research-artifact.v1",
            "project_id": str(project_id),
            "kind": kind,
            "version": version,
            "supersedes_id": str(supersedes_id) if supersedes_id is not None else None,
            "parent_content_hash": parent_content_hash,
            "input_hash": input_hash,
            "payload": payload,
            "source_refs": source_refs,
        }

    @classmethod
    def artifact_content_hash(
        cls,
        *,
        project_id: UUID,
        kind: str,
        version: int,
        supersedes_id: UUID | None,
        parent_content_hash: str | None = None,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
    ) -> str:
        return canonical_hash(
            cls._artifact_payload(
                project_id=project_id,
                kind=kind,
                version=version,
                supersedes_id=supersedes_id,
                parent_content_hash=parent_content_hash,
                input_hash=input_hash,
                payload=payload,
                source_refs=source_refs,
            )
        )

    @staticmethod
    def _event_payload(
        *,
        preparation_id: UUID,
        sequence: int,
        previous_event_hash: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "company-research-event.v1",
            "preparation_id": str(preparation_id),
            "sequence": sequence,
            "previous_event_hash": previous_event_hash,
            "event_type": event_type,
            "payload": payload,
        }

    @classmethod
    def event_content_hash(
        cls,
        *,
        preparation_id: UUID,
        sequence: int,
        previous_event_hash: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> str:
        return canonical_hash(
            cls._event_payload(
                preparation_id=preparation_id,
                sequence=sequence,
                previous_event_hash=previous_event_hash,
                event_type=event_type,
                payload=payload,
            )
        )

    def add_preparation(
        self,
        *,
        project_id: UUID,
        idempotency_key: str,
        request_hash: str,
        strategy_version: str,
        status: str,
        current_step: str | None,
        progress: int,
        attempt: int,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        job_id: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> CompanyResearchPreparation:
        if status not in COMPANY_RESEARCH_PREPARATION_STATUSES:
            raise ValidationError("company research preparation status is invalid")
        if type(progress) is not int or not 0 <= progress <= 100:
            raise ValidationError("progress must be between 0 and 100")
        if type(attempt) is not int or attempt < 1:
            raise ValidationError("attempt must be at least 1")
        if current_step is not None:
            current_step = self._require_nonempty_text(current_step, "current_step", 64)
            self._require_preparation_step(current_step, "current_step")
        if last_error_code is not None:
            last_error_code = self._require_nonempty_text(
                last_error_code, "last_error_code", 96
            )
        job: Job | None = None
        preparation_id: UUID | None = None
        if job_id is not None:
            self._reserve_sqlite_writer_before_ownership_read()
            job = self._job_for_update(job_id)
            if (
                job is None
                or job.kind != _PREPARE_JOB_KIND
                or job.target_type != _PREPARE_JOB_TARGET_TYPE
                or job.target_id is None
                or job.research_case_id is not None
            ):
                raise ValidationError(
                    "company research preparation job ownership is invalid"
                )
            # The job is created first with the UUID it owns; persist the new
            # preparation at that exact UUID so no later attachment can turn a
            # legacy or unrelated job into an owner.
            preparation_id = job.target_id
            self._validate_prepare_job_step(
                preparation_step=current_step,
                preparation_status=status,
                job=job,
                persisted=False,
            )
        row = CompanyResearchPreparation(
            id=preparation_id,
            project_id=project_id,
            idempotency_key=self._require_nonempty_text(
                idempotency_key, "idempotency_key", 255
            ),
            request_hash=self._require_hash(request_hash, "request_hash"),
            strategy_version=self._require_nonempty_text(
                strategy_version, "strategy_version", 96
            ),
            status=status,
            current_step=current_step,
            progress=progress,
            attempt=attempt,
            next_attempt_at=(
                self._stored_datetime(next_attempt_at, "next_attempt_at")
                if next_attempt_at is not None
                else None
            ),
            last_error_code=last_error_code,
            job_id=job_id,
            created_at=self._stored_datetime(created_at, "created_at"),
            updated_at=self._stored_datetime(updated_at, "updated_at"),
        )
        try:
            return self._flush_in_savepoint(row)
        except IntegrityError as exc:
            raise ConflictError("company research preparation already exists") from exc

    def preparation(self, preparation_id: UUID) -> CompanyResearchPreparation | None:
        return self._session.get(CompanyResearchPreparation, preparation_id)

    def preparation_by_idempotency_key(
        self, idempotency_key: str
    ) -> CompanyResearchPreparation | None:
        return self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.idempotency_key == idempotency_key)
            .limit(1)
        )

    def preparation_for_project(
        self, project_id: UUID
    ) -> CompanyResearchPreparation | None:
        """Return the single preparation owned by a high-level project."""
        return self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.project_id == project_id)
            .limit(1)
        )

    def requeue_recoverable_preparation(
        self, preparation_id: UUID, *, updated_at: datetime
    ) -> CompanyResearchPreparation:
        """Return one recoverable preparation to its initial queued step."""
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        if preparation.status != "recoverable_failure":
            raise ValidationError(
                "company research preparation is not recoverable"
            )
        job = self.prepare_job(preparation.id)
        if job is None:
            raise CompanyResearchIntegrityError(
                "company research preparation job is missing"
            )
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        preparation.status = "queued"
        preparation.progress = 0
        preparation.attempt += 1
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = self._stored_datetime(updated_at, "updated_at")
        job.status = "queued"
        job.progress = 0
        job.attempt += 1
        job.error = None
        job.started_at = None
        job.finished_at = None
        try:
            with self._session.begin_nested():
                self._session.flush([preparation, job])
        except IntegrityError as exc:
            raise ConflictError("company research preparation retry conflicts") from exc
        return preparation

    def complete_evidence_preparation(
        self,
        preparation_id: UUID,
        *,
        input_hash: str,
        evidence_index_payload: Mapping[str, object],
        research_gaps_payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
        created_at: datetime,
        expected_claim_token: str | None = None,
        expected_request_hash: str | None = None,
        expected_strategy_version: str | None = None,
    ) -> tuple[CompanyResearchPreparation, CompanyResearchArtifactVersion, CompanyResearchArtifactVersion, Job, CompanyResearchEvent]:
        """Append the two source artifacts and atomically hand off to review."""
        self._reserve_sqlite_writer_before_ownership_read()
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        job = self._locked_prepare_job(preparation)
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        claimed = expected_claim_token is not None
        if expected_request_hash is not None and preparation.request_hash != expected_request_hash:
            raise ValidationError("company research preparation input changed")
        if (
            expected_strategy_version is not None
            and preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation strategy changed")
        if claimed:
            if (
                preparation.status != "preparing_sources"
                or preparation.current_step != "evidence_index"
                or job.status != "running"
                or job.claim_token != expected_claim_token
                or job.cancel_requested
            ):
                raise ValidationError("company research preparation claim is stale")
        elif preparation.status != "queued" or preparation.current_step != "evidence_index":
            raise ValidationError("company research preparation is not queued for evidence indexing")
        when = self._stored_datetime(created_at, "created_at")
        with self._session.begin_nested():
            evidence_index = self.append_artifact(
                project_id=preparation.project_id,
                kind="evidence_index",
                input_hash=input_hash,
                payload=evidence_index_payload,
                source_refs=source_refs,
                expected_parent_id=None,
                created_at=when,
            )
            research_gaps = self.append_artifact(
                project_id=preparation.project_id,
                kind="research_gaps",
                input_hash=input_hash,
                payload=research_gaps_payload,
                source_refs=source_refs,
                expected_parent_id=None,
                created_at=when,
            )
            preparation.status = "awaiting_evidence_review"
            preparation.current_step = "research_gaps"
            preparation.progress = 25
            preparation.next_attempt_at = None
            preparation.last_error_code = None
            preparation.updated_at = when
            job.status = "waiting_for_review"
            job.progress = 25
            job.step = "research_gaps"
            job.error = None
            job.finished_at = None
            job.claim_token = None
            event = self.append_event(
                preparation_id=preparation_id,
                event_type="evidence_index_prepared",
                payload={"evidence_index_id": str(evidence_index.id), "research_gaps_id": str(research_gaps.id)},
                created_at=when,
            )
            self._session.flush([preparation, job])
        return preparation, evidence_index, research_gaps, job, event

    def fail_evidence_preparation(
        self,
        preparation_id: UUID,
        *,
        error_code: str,
        created_at: datetime,
        expected_claim_token: str | None = None,
        expected_request_hash: str | None = None,
        expected_strategy_version: str | None = None,
    ) -> tuple[CompanyResearchPreparation, Job, CompanyResearchEvent]:
        """Record a recoverable source failure before any source artifact exists."""
        self._reserve_sqlite_writer_before_ownership_read()
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        job = self._locked_prepare_job(preparation)
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        claimed = expected_claim_token is not None
        if expected_request_hash is not None and preparation.request_hash != expected_request_hash:
            raise ValidationError("company research preparation input changed")
        if (
            expected_strategy_version is not None
            and preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation strategy changed")
        if claimed:
            if (
                preparation.status != "preparing_sources"
                or preparation.current_step != "evidence_index"
                or job.status != "running"
                or job.claim_token != expected_claim_token
                or job.cancel_requested
            ):
                raise ValidationError("company research preparation claim is stale")
        elif preparation.status != "queued" or preparation.current_step != "evidence_index":
            raise ValidationError("company research preparation is not queued for evidence indexing")
        when = self._stored_datetime(created_at, "created_at")
        error_code = self._require_nonempty_text(error_code, "error_code", 96)
        with self._session.begin_nested():
            preparation.status = "recoverable_failure"
            preparation.progress = 0
            preparation.next_attempt_at = None
            preparation.last_error_code = error_code
            preparation.updated_at = when
            job.status = "failed"
            job.progress = 0
            job.error = error_code
            job.finished_at = when
            job.claim_token = None
            event = self.append_event(
                preparation_id=preparation_id,
                event_type="source_preparation_failed",
                payload={"code": error_code, "recoverable": True},
                created_at=when,
            )
            self._session.flush([preparation, job])
        return preparation, job, event

    @staticmethod
    def _validate_artifact_row(row: CompanyResearchArtifactVersion) -> None:
        expected_hash = CompanyResearchRepository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=row.parent_content_hash,
            input_hash=row.input_hash,
            payload=row.payload,
            source_refs=row.source_refs,
        )
        if row.content_hash != expected_hash:
            raise CompanyResearchIntegrityError(
                "company research artifact content hash mismatch"
            )

    @staticmethod
    def _validate_event_row(row: CompanyResearchEvent) -> None:
        expected_hash = CompanyResearchRepository.event_content_hash(
            preparation_id=row.preparation_id,
            sequence=row.sequence,
            previous_event_hash=row.previous_event_hash,
            event_type=row.event_type,
            payload=row.payload,
        )
        if row.content_hash != expected_hash:
            raise CompanyResearchIntegrityError(
                "company research event content hash mismatch"
            )

    def artifact(self, artifact_id: UUID) -> CompanyResearchArtifactVersion | None:
        row = self._session.get(CompanyResearchArtifactVersion, artifact_id)
        if row is not None:
            self._validate_artifact_row(row)
        return row

    def append_artifact(
        self,
        *,
        project_id: UUID,
        kind: str,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> CompanyResearchArtifactVersion:
        if kind not in COMPANY_RESEARCH_ARTIFACT_KINDS:
            raise ValidationError("company research artifact kind is invalid")
        if not isinstance(payload, Mapping):
            raise ValidationError("artifact payload must be an object")
        if isinstance(source_refs, (str, bytes)) or not isinstance(
            source_refs, Sequence
        ):
            raise ValidationError("artifact source_refs must be an array")
        if not all(isinstance(value, Mapping) for value in source_refs):
            raise ValidationError("artifact source_refs must contain objects")
        self._reserve_sqlite_writer_before_artifact_head_read()
        parent = self.current_artifact(project_id, kind, lock=True)
        actual_parent_id = parent.id if parent is not None else None
        if actual_parent_id != expected_parent_id:
            raise StaleParentError("expected parent is not the company artifact head")
        version = 1 if parent is None else parent.version + 1
        copied_payload = deepcopy(dict(payload))
        copied_refs = deepcopy(list(source_refs))
        input_hash = self._require_hash(input_hash, "input_hash")
        row = CompanyResearchArtifactVersion(
            project_id=project_id,
            kind=kind,
            version=version,
            supersedes_id=actual_parent_id,
            parent_content_hash=(
                parent.content_hash if parent is not None else None
            ),
            input_hash=input_hash,
            payload=copied_payload,
            source_refs=copied_refs,
            content_hash=self.artifact_content_hash(
                project_id=project_id,
                kind=kind,
                version=version,
                supersedes_id=actual_parent_id,
                parent_content_hash=(
                    parent.content_hash if parent is not None else None
                ),
                input_hash=input_hash,
                payload=copied_payload,
                source_refs=copied_refs,
            ),
            created_at=self._stored_datetime(created_at, "created_at"),
        )
        try:
            return self._flush_in_savepoint(row)
        except IntegrityError as exc:
            raise StaleParentError(
                "expected parent is not the company artifact head"
            ) from exc

    def artifact_chain(
        self, artifact_id: UUID
    ) -> tuple[CompanyResearchArtifactVersion, ...]:
        """Return root-to-leaf chain after iteratively validating every link."""
        seen: set[UUID] = set()
        chain: list[CompanyResearchArtifactVersion] = []
        current_id: UUID | None = artifact_id
        expected_project_id: UUID | None = None
        expected_kind: str | None = None
        expected_version: int | None = None
        while current_id is not None:
            if current_id in seen:
                raise CompanyResearchIntegrityError(
                    "company research artifact chain contains a cycle"
                )
            seen.add(current_id)
            row = self.artifact(current_id)
            if row is None:
                raise CompanyResearchIntegrityError(
                    "company research artifact parent is missing"
                )
            if expected_project_id is not None and (
                row.project_id != expected_project_id
                or row.kind != expected_kind
                or row.version != expected_version
            ):
                raise CompanyResearchIntegrityError(
                    "company research artifact parent chain is invalid"
                )
            if row.supersedes_id is None:
                if row.parent_content_hash is not None:
                    raise CompanyResearchIntegrityError(
                        "company research artifact root has a parent content hash"
                    )
            else:
                parent = self.artifact(row.supersedes_id)
                if parent is None:
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent is missing"
                    )
                if row.parent_content_hash != parent.content_hash:
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent content hash mismatch"
                    )
            chain.append(row)
            expected_project_id = row.project_id
            expected_kind = row.kind
            expected_version = row.version - 1
            current_id = row.supersedes_id
        if not chain:
            raise CompanyResearchIntegrityError(
                "company research artifact chain is empty"
            )
        if chain[-1].version != 1:
            raise CompanyResearchIntegrityError(
                "company research artifact chain has no version-one root"
            )
        return tuple(reversed(chain))

    def current_artifact(
        self, project_id: UUID, kind: str, *, lock: bool = False
    ) -> CompanyResearchArtifactVersion | None:
        if kind not in COMPANY_RESEARCH_ARTIFACT_KINDS:
            raise ValidationError("company research artifact kind is invalid")
        statement = (
            select(CompanyResearchArtifactVersion)
            .where(
                CompanyResearchArtifactVersion.project_id == project_id,
                CompanyResearchArtifactVersion.kind == kind,
            )
            .order_by(
                CompanyResearchArtifactVersion.version.desc(),
                CompanyResearchArtifactVersion.id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        rows = tuple(self._session.scalars(statement))
        if not rows:
            return None
        rows_by_id = {row.id: row for row in rows}
        for row in rows:
            self._validate_artifact_row(row)

        successors = tuple(
            self._session.scalars(
                select(CompanyResearchArtifactVersion).where(
                    CompanyResearchArtifactVersion.supersedes_id.in_(rows_by_id)
                )
            )
        )
        parent_ids_with_successors: set[UUID] = set()
        for successor in successors:
            self._validate_artifact_row(successor)
            parent = rows_by_id[successor.supersedes_id]
            if (
                successor.project_id != parent.project_id
                or successor.kind != parent.kind
            ):
                raise CompanyResearchIntegrityError(
                    "company research artifact successor crosses project or kind"
                )
            parent_ids_with_successors.add(parent.id)

        for row in rows:
            seen: set[UUID] = set()
            current = row
            while current.supersedes_id is not None:
                if current.id in seen:
                    raise CompanyResearchIntegrityError(
                        "company research artifact chain contains a cycle"
                    )
                seen.add(current.id)
                parent = rows_by_id.get(current.supersedes_id)
                if parent is None:
                    parent = self.artifact(current.supersedes_id)
                    if parent is None:
                        raise CompanyResearchIntegrityError(
                            "company research artifact parent is missing"
                        )
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent crosses project or kind"
                    )
                current = parent

        heads = tuple(
            row for row in rows if row.id not in parent_ids_with_successors
        )
        if not heads:
            raise CompanyResearchIntegrityError(
                "company research artifact rows have no current leaf"
            )
        if len(heads) > 1:
            raise ConflictError("multiple current artifact heads")
        self.artifact_chain(heads[0].id)
        return heads[0]

    def append_event(
        self,
        *,
        preparation_id: UUID,
        event_type: str,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> CompanyResearchEvent:
        self._reserve_sqlite_writer_before_event_append()
        if self._preparation_for_update(preparation_id) is None:
            raise ValidationError("company research preparation not found")
        if not isinstance(payload, Mapping):
            raise ValidationError("company research event payload must be an object")
        event_type = self._require_nonempty_text(event_type, "event_type", 64)
        copied_payload = deepcopy(dict(payload))
        existing_events = self.events(preparation_id)
        predecessor = existing_events[-1] if existing_events else None
        sequence = 1 if predecessor is None else predecessor.sequence + 1
        previous_event_hash = (
            predecessor.content_hash if predecessor is not None else None
        )
        return self._flush_in_savepoint(
            CompanyResearchEvent(
                preparation_id=preparation_id,
                sequence=sequence,
                previous_event_hash=previous_event_hash,
                event_type=event_type,
                payload=copied_payload,
                content_hash=self.event_content_hash(
                    preparation_id=preparation_id,
                    sequence=sequence,
                    previous_event_hash=previous_event_hash,
                    event_type=event_type,
                    payload=copied_payload,
                ),
                created_at=self._stored_datetime(created_at, "created_at"),
            )
        )

    def events(self, preparation_id: UUID) -> tuple[CompanyResearchEvent, ...]:
        rows = tuple(
            self._session.scalars(
                select(CompanyResearchEvent)
                .where(CompanyResearchEvent.preparation_id == preparation_id)
                .order_by(CompanyResearchEvent.sequence)
            )
        )
        previous_hash: str | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            self._validate_event_row(row)
            if row.sequence != expected_sequence:
                raise CompanyResearchIntegrityError(
                    "company research event sequence is invalid"
                )
            if row.previous_event_hash != previous_hash:
                raise CompanyResearchIntegrityError(
                    "company research event predecessor hash mismatch"
                )
            previous_hash = row.content_hash
        return rows

    def prepare_job(self, preparation_id: UUID) -> Job | None:
        """Return the preparation's bound job after verifying its discriminator."""
        preparation = self.preparation(preparation_id)
        if preparation is None or preparation.job_id is None:
            return None
        job = self._session.get(Job, preparation.job_id)
        if not self._is_exact_prepare_job_owner(job, preparation.id):
            raise CompanyResearchIntegrityError(
                "company research preparation job ownership is invalid"
            )
        return job

    def attach_prepare_job(
        self, preparation_id: UUID, job_id: UUID
    ) -> CompanyResearchPreparation:
        """Attach only a generic Job that is scoped to this preparation exactly."""
        self._reserve_sqlite_writer_before_ownership_read()
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        if preparation.job_id is not None:
            if preparation.job_id != job_id:
                raise ConflictError("company research preparation job is already bound")
            job = self._job_for_update(job_id)
            if not self._is_exact_prepare_job_owner(job, preparation.id):
                raise CompanyResearchIntegrityError(
                    "company research preparation job ownership is invalid"
                )
            return preparation
        job = self._job_for_update(job_id)
        if not self._is_exact_prepare_job_owner(job, preparation.id):
            raise ValidationError(
                "company research preparation job ownership is invalid"
            )
        preparation.job_id = job.id
        try:
            with self._session.begin_nested():
                self._session.flush([preparation])
        except IntegrityError as exc:
            raise ConflictError("company research preparation job is already bound") from exc
        return preparation

    @staticmethod
    def _is_exact_prepare_job_owner(
        job: Job | None, preparation_id: UUID
    ) -> bool:
        return bool(
            job is not None
            and job.kind == _PREPARE_JOB_KIND
            and job.target_type == _PREPARE_JOB_TARGET_TYPE
            and job.target_id == preparation_id
            and job.research_case_id is None
        )
