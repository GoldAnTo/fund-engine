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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.models.operational import Job
from app.repositories.operational import JobRepository
from app.repositories.outbox import emit_event


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

    def request_cancel(self, job: Job) -> None:
        if job.status in {"succeeded", "failed", "cancelled"}:
            raise ConflictError(f"job {job.id} already terminal ({job.status})")
        self._repo.mark_cancellation_requested(job)
        self._append_event(job, status=job.status, message="cancel requested")

    def should_cancel(self, job: Job) -> bool:
        return bool(job.cancel_requested)

    def _append_event(
        self, job: Job, *, status: str | None, step: str | None, progress: int | None = None, message: str | None = None
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
        emit_event(
            self._session,
            type="job_progressed",
            aggregate_type="job",
            aggregate_id=job.id,
            payload={"seq": seq, "status": status, "step": step, "progress": progress},
            origin="operational",
        )
