"""Persistence operations for the mutable research-preparation projection."""
from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.research_preparation import ArtifactKind, PreparationStep
from app.errors import ConflictError, NotFoundError
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseTenantAdmission,
    DocumentVersion,
    SourceSpan,
)
from app.models.operational import Job
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
    ResearchPreparationEvent,
)
from app.repositories.operational import JobRepository
from app.services.event_research_scope_evidence import lock_event_scope_case


SQLITE_PREPARATION_CLAIM_BATCH_SIZE = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchPreparationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = JobRepository(session)

    def lock_for_case(
        self, case_id: uuid.UUID, *, case_locked: bool = False
    ) -> ResearchPreparation | None:
        """Lock the stable Case row before reading the preparation projection."""
        if not case_locked and lock_event_scope_case(self._session, case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")
        return self._session.scalar(
            select(ResearchPreparation)
            .where(ResearchPreparation.research_case_id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def preparation_for_case(self, case_id: uuid.UUID) -> ResearchPreparation | None:
        """Read a preparation before a caller obtains its Case lock."""
        return self._session.scalar(
            select(ResearchPreparation).where(
                ResearchPreparation.research_case_id == case_id
            )
        )

    def lock_candidate_rows(
        self, candidate_ids: set[uuid.UUID]
    ) -> list[AtomicClaimCandidate]:
        """Acquire candidate row locks in the global UUID order.

        This is the first serialization primitive for every atomic-claim
        review path.  It prevents a command from holding one preparation's
        Case lock while attempting to traverse another shared candidate's
        preparation mappings.
        """
        locked: list[AtomicClaimCandidate] = []
        for candidate_id in sorted(candidate_ids, key=str):
            candidate = self._session.scalar(
                select(AtomicClaimCandidate)
                .where(AtomicClaimCandidate.id == candidate_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if candidate is not None:
                locked.append(candidate)
        return locked

    def lock_preparation_for_candidate_review(
        self, candidate_id: uuid.UUID
    ) -> list[ResearchPreparation]:
        """Lock every current preparation affected by a claim review.

        Candidate lookup is deliberately read-only until it has established a
        current parse artifact for the candidate.  When such an artifact
        exists, each Case → preparation pair is locked in canonical order.
        This matches protocol confirmation's lock order, so a review cannot
        interleave after that command has read its context.  A shared frozen
        document may legitimately map one candidate into multiple current
        preparations.
        """
        document_version_id = self._session.scalar(
            select(SourceSpan.document_version_id)
            .join(
                AtomicClaimCandidate,
                AtomicClaimCandidate.source_span_id == SourceSpan.id,
            )
            .where(AtomicClaimCandidate.id == candidate_id)
        )
        if document_version_id is None:
            return []
        if self._session.get(DocumentVersion, document_version_id) is None:
            return []
        candidate_case_ids = list(
            self._session.scalars(
                select(CaseTenantAdmission.research_case_id).where(
                    CaseTenantAdmission.initial_document_version_id
                    == document_version_id
                )
            )
        )
        if not candidate_case_ids:
            return []
        current_artifacts = list(
            self._session.scalars(
                select(ResearchPreparationArtifact)
                .join(
                    ResearchPreparation,
                    ResearchPreparation.id
                    == ResearchPreparationArtifact.research_preparation_id,
                )
                .where(
                    ResearchPreparation.research_case_id.in_(candidate_case_ids),
                    ResearchPreparationArtifact.kind == "atomic_claim_candidates",
                    ResearchPreparationArtifact.state == "current",
                )
            )
        )
        candidate_preparation_ids = {
            artifact.research_preparation_id
            for artifact in current_artifacts
            if self._artifact_includes_candidate(artifact, candidate_id)
        }
        if not candidate_preparation_ids:
            return []
        mappings = sorted(
            set(
                self._session.execute(
                    select(
                        ResearchPreparation.research_case_id,
                        ResearchPreparation.id,
                    ).where(ResearchPreparation.id.in_(candidate_preparation_ids))
                ).all()
            ),
            key=lambda mapping: (str(mapping[0]), str(mapping[1])),
        )
        if not mappings:
            return []

        locked_preparations: list[ResearchPreparation] = []
        for case_id, preparation_id in mappings:
            if lock_event_scope_case(self._session, case_id) is None:
                continue
            try:
                preparation = self._lock_preparation_after_case_lock(
                    case_id, preparation_id
                )
            except ConflictError:
                # The preliminary mapping became stale before its pair was
                # locked. Revalidation below remains authoritative.
                continue
            admission = self._session.scalar(
                select(CaseTenantAdmission).where(
                    CaseTenantAdmission.research_case_id == case_id,
                    CaseTenantAdmission.initial_document_version_id
                    == document_version_id,
                )
            )
            artifact = self._session.scalar(
                select(ResearchPreparationArtifact)
                .where(
                    ResearchPreparationArtifact.research_preparation_id
                    == preparation.id,
                    ResearchPreparationArtifact.kind == "atomic_claim_candidates",
                    ResearchPreparationArtifact.state == "current",
                )
                .with_for_update()
            )
            if (
                admission is not None
                and artifact is not None
                and self._artifact_includes_candidate(artifact, candidate_id)
            ):
                locked_preparations.append(preparation)
        if not locked_preparations:
            return []
        return locked_preparations

    @staticmethod
    def _artifact_includes_candidate(
        artifact: ResearchPreparationArtifact, candidate_id: uuid.UUID
    ) -> bool:
        candidates = artifact.payload.get("candidates")
        return isinstance(candidates, list) and any(
            isinstance(item, dict) and item.get("candidate_id") == str(candidate_id)
            for item in candidates
        )

    def _lock_case_then_preparation(
        self, research_case_id: uuid.UUID, preparation_id: uuid.UUID
    ) -> ResearchPreparation:
        """Take the stable Case lock before mutating its preparation row."""
        with self._session.no_autoflush:
            case = lock_event_scope_case(self._session, research_case_id)
        if case is None:
            raise ConflictError(f"research case {research_case_id} not found")
        # These write helpers can follow a service mutation of the already
        # locked projection. Persist it before populate_existing refreshes it,
        # including when the production Session disables autoflush. Keep the
        # Case lock first and leave commit/rollback to the caller.
        self._session.flush()
        return self._lock_preparation_after_case_lock(
            research_case_id, preparation_id
        )

    def _lock_preparation_after_case_lock(
        self, research_case_id: uuid.UUID, preparation_id: uuid.UUID
    ) -> ResearchPreparation:
        """Lock one preparation after the caller has locked its Case."""
        preparation = self._session.scalar(
            select(ResearchPreparation)
            .where(
                ResearchPreparation.id == preparation_id,
                ResearchPreparation.research_case_id == research_case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if preparation is None:
            raise ConflictError("preparation does not belong to research case")
        return preparation

    def append_event(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        type: str,
        step: str | None,
        message: str | None,
        detail: dict[str, object],
    ) -> ResearchPreparationEvent:
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        last_seq = self._session.scalar(
            select(func.max(ResearchPreparationEvent.seq)).where(
                ResearchPreparationEvent.research_preparation_id == preparation.id
            )
        )
        event = ResearchPreparationEvent(
            research_preparation_id=preparation.id,
            seq=(last_seq or 0) + 1,
            type=type,
            step=step,
            message=message,
            detail=detail,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def current_artifact(
        self, preparation_id: uuid.UUID, kind: ArtifactKind
    ) -> ResearchPreparationArtifact | None:
        return self._session.scalar(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation_id,
                ResearchPreparationArtifact.kind == kind,
                ResearchPreparationArtifact.state == "current",
            )
        )

    def append_artifact(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        kind: ArtifactKind,
        input_fingerprint: str,
        payload: dict[str, object],
        context_fingerprint: str | None = None,
    ) -> ResearchPreparationArtifact:
        if context_fingerprint is not None and re.fullmatch(
            r"[0-9a-f]{64}", context_fingerprint
        ) is None:
            raise ConflictError("preparation context fingerprint is invalid")
        if kind == "atomic_claim_candidates" and context_fingerprint is not None:
            raise ConflictError("claim candidate artifacts cannot carry a context fingerprint")
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        current = self._session.scalar(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind == kind,
                ResearchPreparationArtifact.state == "current",
            )
            .with_for_update()
        )
        if current is not None:
            current.state = "superseded"
            self._session.flush()
        last_sequence = self._session.scalar(
            select(func.max(ResearchPreparationArtifact.sequence)).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
        artifact = ResearchPreparationArtifact(
            research_preparation_id=preparation.id,
            kind=kind,
            sequence=(last_sequence or 0) + 1,
            preparation_version=preparation.version,
            input_fingerprint=input_fingerprint,
            context_fingerprint=context_fingerprint,
            payload=payload,
            state="current",
            invalidated_reason=None,
            created_at=_utcnow(),
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def queue_step_job(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        step: PreparationStep,
    ) -> Job:
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        correlation_id = f"{preparation.id}:{preparation.version}:{step}"
        active = self._session.scalar(
            select(Job)
            .where(
                Job.kind == "prepare_research",
                Job.target_type == "research_preparation",
                Job.target_id == preparation.id,
                Job.correlation_id == correlation_id,
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.created_at, Job.id)
        )
        if active is not None:
            return active
        return self._jobs.add_job(
            kind="prepare_research",
            target_type="research_preparation",
            target_id=preparation.id,
            research_case_id=preparation.research_case_id,
            correlation_id=correlation_id,
        )

    def claim_next_preparation_job(self, *, now: datetime | None = None) -> Job | None:
        """Atomically claim one preparation job, never a normal run job.

        PostgreSQL honours ``SKIP LOCKED``; SQLite accepts the same ORM shape
        but serializes writers, which is sufficient for the local worker and
        test fallback.
        """
        now = now or _utcnow()
        eligible = self._eligible_preparation_jobs(now)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            job = self._session.scalar(
                eligible.with_for_update(of=Job, skip_locked=True)
                .execution_options(populate_existing=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "running"
            job.started_at = now
            job.claim_token = str(uuid.uuid4())
            self._append_job_event(job, status="running", step=job.step, message="preparation job claimed")
            self._session.flush()
            return job

        # SQLite has no row locks.  The read only nominates a candidate; the
        # conditional state transition is the ownership primitive.  A racing
        # worker sees rowcount=0 and continues to the next candidate.
        candidate_ids = list(self._session.scalars(
            eligible.with_only_columns(Job.id).limit(SQLITE_PREPARATION_CLAIM_BATCH_SIZE)
        ))
        for candidate_id in candidate_ids:
            claim_token = str(uuid.uuid4())
            claimed = self._session.execute(
                update(Job)
                .where(
                    Job.id == candidate_id,
                    Job.status == "queued",
                    Job.cancel_requested.is_(False),
                )
                .values(status="running", started_at=now, claim_token=claim_token)
            )
            if claimed.rowcount != 1:
                continue
            job = self._session.get(Job, candidate_id, populate_existing=True)
            assert job is not None
            self._append_job_event(job, status="running", step=job.step, message="preparation job claimed")
            self._session.flush()
            return job
        return None

    @staticmethod
    def _eligible_preparation_jobs(now: datetime):
        preparation_exists = exists(
            select(ResearchPreparation.id).where(
                ResearchPreparation.id == Job.target_id,
                ResearchPreparation.research_case_id == Job.research_case_id,
                or_(
                    ResearchPreparation.next_attempt_at.is_(None),
                    ResearchPreparation.next_attempt_at <= now,
                ),
            )
        )
        return (
            select(Job)
            .where(
                Job.kind == "prepare_research",
                Job.status == "queued",
                Job.cancel_requested.is_(False),
                Job.target_type == "research_preparation",
                Job.target_id.is_not(None),
                Job.research_case_id.is_not(None),
                preparation_exists,
            )
            .order_by(Job.created_at, Job.id)
        )

    def recover_stale_preparation_jobs(self, *, before: datetime) -> int:
        """Return abandoned preparation jobs to their queue without cloning."""
        jobs = list(self._session.scalars(
            select(Job)
            .where(
                Job.kind == "prepare_research",
                Job.status == "running",
                Job.started_at.is_not(None),
                Job.started_at < before,
            )
            .with_for_update(skip_locked=True)
        ))
        for job in jobs:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = _utcnow()
                self._append_job_event(job, status="cancelled", step=job.step, message="preparation job cancelled")
            else:
                job.status = "queued"
                job.started_at = None
                job.finished_at = None
                job.claim_token = None
                self._append_job_event(job, status="queued", step=job.step, message="stale preparation job reclaimed")
        self._session.flush()
        return len(jobs)

    def cancel_queued_preparation_jobs(self) -> list[Job]:
        """Terminalize queued cancellation requests before any provider call."""
        jobs = list(self._session.scalars(
            select(Job)
            .where(
                Job.kind == "prepare_research",
                Job.status == "queued",
                Job.cancel_requested.is_(True),
            )
            .order_by(Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ))
        for job in jobs:
            self.set_preparation_job_terminal(
                job, status="cancelled", step=job.step, error="stale preparation output discarded"
            )
        return jobs

    def set_preparation_job_terminal(
        self, job: Job, *, status: str, step: str | None, error: str | None = None
    ) -> None:
        job.status = status
        job.step = step
        job.error = error
        job.finished_at = _utcnow()
        self._append_job_event(job, status=status, step=step, message=error)
        self._session.flush()

    def requeue_preparation_job(
        self, job: Job, *, step: str, error: str
    ) -> None:
        job.status = "queued"
        job.step = step
        job.error = error
        job.attempt += 1
        job.claim_token = None
        self._append_job_event(job, status="queued", step=step, message=error)
        self._session.flush()

    def _append_job_event(
        self, job: Job, *, status: str, step: str | None, message: str | None
    ) -> None:
        self._jobs.append_event(
            job_id=job.id,
            seq=self._jobs.next_event_seq(job.id),
            status=status,
            step=step,
            message=message,
        )
