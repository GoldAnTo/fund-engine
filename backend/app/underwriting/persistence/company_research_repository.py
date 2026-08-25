"""Persistence primitives for append-only company-research workbench data."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.persistence.company_research_models import (
    COMPANY_RESEARCH_ARTIFACT_KINDS,
    COMPANY_RESEARCH_PREPARATION_STATUSES,
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PREPARE_JOB_KIND = "prepare_company_research"
_PREPARE_JOB_TARGET_TYPE = "company_research_preparation"


class CompanyResearchIntegrityError(ValidationError):
    """A persisted immutable company-research record cannot be trusted."""


class CompanyResearchRepository:
    """Flush-only operations which retain ownership of the caller transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

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
                input_hash=input_hash,
                payload=payload,
                source_refs=source_refs,
            )
        )

    @staticmethod
    def _event_payload(
        *, preparation_id: UUID, event_type: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        return {
            "schema_version": "company-research-event.v1",
            "preparation_id": str(preparation_id),
            "event_type": event_type,
            "payload": payload,
        }

    @classmethod
    def event_content_hash(
        cls, *, preparation_id: UUID, event_type: str, payload: Mapping[str, object]
    ) -> str:
        return canonical_hash(
            cls._event_payload(
                preparation_id=preparation_id, event_type=event_type, payload=payload
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
        if last_error_code is not None:
            last_error_code = self._require_nonempty_text(
                last_error_code, "last_error_code", 96
            )
        row = CompanyResearchPreparation(
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

    @staticmethod
    def _validate_artifact_row(row: CompanyResearchArtifactVersion) -> None:
        expected_hash = CompanyResearchRepository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
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
            input_hash=input_hash,
            payload=copied_payload,
            source_refs=copied_refs,
            content_hash=self.artifact_content_hash(
                project_id=project_id,
                kind=kind,
                version=version,
                supersedes_id=actual_parent_id,
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
        successor = aliased(CompanyResearchArtifactVersion)
        statement = (
            select(CompanyResearchArtifactVersion)
            .where(
                CompanyResearchArtifactVersion.project_id == project_id,
                CompanyResearchArtifactVersion.kind == kind,
                ~exists(
                    select(successor.id).where(
                        successor.supersedes_id == CompanyResearchArtifactVersion.id
                    )
                ),
            )
            .order_by(
                CompanyResearchArtifactVersion.version.desc(),
                CompanyResearchArtifactVersion.id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        heads = tuple(self._session.scalars(statement))
        if len(heads) > 1:
            raise ConflictError("multiple current artifact heads")
        if not heads:
            return None
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
        if self.preparation(preparation_id) is None:
            raise ValidationError("company research preparation not found")
        if not isinstance(payload, Mapping):
            raise ValidationError("company research event payload must be an object")
        event_type = self._require_nonempty_text(event_type, "event_type", 64)
        copied_payload = deepcopy(dict(payload))
        return self._flush_in_savepoint(
            CompanyResearchEvent(
                preparation_id=preparation_id,
                event_type=event_type,
                payload=copied_payload,
                content_hash=self.event_content_hash(
                    preparation_id=preparation_id,
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
                .order_by(CompanyResearchEvent.created_at, CompanyResearchEvent.id)
            )
        )
        for row in rows:
            self._validate_event_row(row)
        return rows

    def prepare_job(self, preparation_id: UUID) -> Job | None:
        """Find only the generic job owned by this exact preparation contract."""
        return self._session.scalar(
            select(Job)
            .where(
                Job.kind == _PREPARE_JOB_KIND,
                Job.target_type == _PREPARE_JOB_TARGET_TYPE,
                Job.target_id == preparation_id,
                Job.research_case_id.is_(None),
            )
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )

    def attach_prepare_job(
        self, preparation_id: UUID, job_id: UUID
    ) -> CompanyResearchPreparation:
        """Attach only a generic Job that is scoped to this preparation exactly."""
        preparation = self.preparation(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        job = self._session.get(Job, job_id)
        if (
            job is None
            or job.kind != _PREPARE_JOB_KIND
            or job.target_type != _PREPARE_JOB_TARGET_TYPE
            or job.target_id != preparation.id
            or job.research_case_id is not None
        ):
            raise ValidationError(
                "company research preparation job ownership is invalid"
            )
        preparation.job_id = job.id
        self._session.flush([preparation])
        return preparation
