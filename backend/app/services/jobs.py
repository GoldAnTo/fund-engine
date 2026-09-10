"""Job orchestration service (design §5.7 / §8.7).

Wraps a ``Job`` row with a small state machine and emits ``job_*`` DomainEvents
on every transition so the activity feed / task queue can be rebuilt from the
outbox instead of polled from mutable job rows.

First version is synchronous + in-process: command endpoints create a Job row
in ``queued`` then run the work inline, advancing ``progress`` and appending
``JobEvent`` rows.  A later Temporal-backed worker can replace the runner
without touching callers — they only see the Job contract.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
from app.models.operational import Job
from app.repositories.operational import JobRepository
from app.repositories.outbox import emit_event
from app.services.case_tenant_access import CaseTenantAccess


class JobService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = JobRepository(session)

    def create(
        self,
        *,
        kind: str,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        research_case_id: uuid.UUID | None = None,
        ai_run_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        actor: str | None = None,
    ) -> Job:
        job = self._repo.add_job(
            kind=kind,
            target_type=target_type,
            target_id=target_id,
            research_case_id=research_case_id,
            ai_run_id=ai_run_id,
            correlation_id=correlation_id,
        )
        emit_event(
            self._session,
            type="job_created",
            aggregate_type="job",
            aggregate_id=job.id,
            payload={"kind": kind, "target_type": target_type},
            origin="operational",
            actor=actor,
            correlation_id=correlation_id,
        )
        return job

    def start(self, job: Job, *, step: str | None = None) -> None:
        self._repo.set_status(job, status="running", step=step, started=True)
        self._append_event(job, status="running", step=step)

    def progress(
        self, job: Job, *, step: str | None = None, progress: int | None = None, message: str | None = None
    ) -> None:
        self._repo.set_status(job, status=job.status, step=step, progress=progress)
        self._append_event(job, status=job.status, step=step, progress=progress, message=message)

    def finish(
        self, job: Job, *, status: str, error: str | None = None, step: str | None = None
    ) -> None:
        self._repo.set_status(
            job, status=status, step=step, error=error, finished=True
        )
        self._append_event(job, status=status, step=step, message=error)

    def request_cancel(self, job: Job, *, actor: str | None = None) -> None:
        # Serialize cancellation with the provider's short post-return output
        # slot.  A request that waited behind a successful terminal commit
        # must re-read that status and fail instead of leaving a misleading
        # succeeded+cancel_requested Job.
        with self._session.no_autoflush:
            current = self._session.scalar(
                select(Job)
                .where(Job.id == job.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if current is None:
            raise ConflictError(f"job {job.id} not found")
        if current.status in {"succeeded", "failed", "cancelled"}:
            raise ConflictError(
                f"job {current.id} already terminal ({current.status})"
            )
        self._repo.mark_cancellation_requested(current)
        self._append_event(
            current,
            status=current.status,
            step=None,
            message="cancel requested",
            actor=actor,
        )

    def request_cancel_authorized(
        self, job_id: uuid.UUID, *, tenant_id: str, actor: str
    ) -> Job:
        job = self._authorized_job(job_id, tenant_id=tenant_id)
        self._reject_research_run(job)
        self.request_cancel(job, actor=actor)
        return job

    def get_authorized(self, job_id: uuid.UUID, *, tenant_id: str) -> Job:
        """Return a Case-owned Job without revealing foreign or unowned rows."""
        return self._authorized_job(job_id, tenant_id=tenant_id)

    def retry_authorized(
        self, job_id: uuid.UUID, *, tenant_id: str, actor: str
    ) -> Job:
        job = self._authorized_job(job_id, tenant_id=tenant_id, lock=True)
        self._reject_research_run(job)
        if job.status not in {"failed", "cancelled"}:
            raise ConflictError(
                f"job {job.id} is not retryable (status={job.status})"
            )
        job.status = "queued"
        job.attempt += 1
        job.error = None
        job.cancel_requested = False
        job.started_at = None
        job.finished_at = None
        self._append_event(
            job,
            status="queued",
            step="retry",
            message="retry requested",
            actor=actor,
            payload_extra={"retry": True, "attempt": job.attempt},
        )
        return job

    def _authorized_job(
        self, job_id: uuid.UUID, *, tenant_id: str, lock: bool = False
    ) -> Job:
        stmt = select(Job).where(Job.id == job_id)
        if lock:
            stmt = stmt.with_for_update()
        job = self._session.scalar(stmt.execution_options(populate_existing=True))
        if job is None or job.research_case_id is None:
            raise NotFoundError("job not found")
        CaseTenantAccess(self._session).require_case(job.research_case_id, tenant_id)
        return job

    @staticmethod
    def _reject_research_run(job: Job) -> None:
        if job.kind == "research_run" or job.target_type == "research_run":
            raise ConflictError(
                "research_run jobs require the automatic research service"
            )

    def should_cancel(self, job: Job) -> bool:
        return bool(job.cancel_requested)

    def _append_event(
        self,
        job: Job,
        *,
        status: str | None,
        step: str | None,
        progress: int | None = None,
        message: str | None = None,
        actor: str | None = None,
        payload_extra: dict[str, object] | None = None,
    ) -> None:
        seq = self._repo.next_event_seq(job.id)
        self._repo.append_event(
            job_id=job.id,
            seq=seq,
            status=status,
            step=step,
            progress=progress,
            message=message,
        )
        payload = {
            "seq": seq,
            "status": status,
            "step": step,
            "progress": progress,
        }
        payload.update(payload_extra or {})
        emit_event(
            self._session,
            type="job_progressed",
            aggregate_type="job",
            aggregate_id=job.id,
            payload=payload,
            origin="operational",
            actor=actor,
        )
