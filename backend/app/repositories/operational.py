"""Repository for operational-store tables (jobs, task items, review
assignments, idempotency keys, projection checkpoints).

These tables are mutable (unlike the ledger) but every meaningful transition
also emits a DomainEvent via ``emit_event`` so projections reconstruct from the
append-only log.  Keep write methods small and let the service layer own the
state machine + event emission.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import (
    IdempotencyKey,
    Job,
    JobEvent,
    ProjectionCheckpoint,
    ReviewAssignment,
    TaskItem,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_job(
        self,
        *,
        kind: str,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        research_case_id: uuid.UUID | None = None,
        ai_run_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
    ) -> Job:
        job = Job(
            kind=kind,
            status="queued",
            target_type=target_type,
            target_id=target_id,
            research_case_id=research_case_id,
            ai_run_id=ai_run_id,
            correlation_id=correlation_id,
            created_at=_utcnow(),
        )
        self._session.add(job)
        self._session.flush()
        return job

    def get_job(self, job_id: uuid.UUID) -> Job | None:
        return self._session.get(Job, job_id)

    def set_status(
        self,
        job: Job,
        *,
        status: str,
        step: str | None = None,
        progress: int | None = None,
        error: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        job.status = status
        if step is not None:
            job.step = step
        if progress is not None:
            job.progress = progress
        if error is not None:
            job.error = error
        if started and job.started_at is None:
            job.started_at = _utcnow()
        if finished:
            job.finished_at = _utcnow()
        # Note: no flush — caller commits or the unit-of-work does.

    def mark_cancellation_requested(self, job: Job) -> None:
        job.cancel_requested = True

    def next_event_seq(self, job_id: uuid.UUID) -> int:
        last = self._session.scalar(
            select(JobEvent.seq)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.seq.desc())
            .limit(1)
        )
        return (last or 0) + 1

    def append_event(
        self,
        *,
        job_id: uuid.UUID,
        seq: int,
        status: str | None = None,
        step: str | None = None,
        progress: int | None = None,
        message: str | None = None,
    ) -> JobEvent:
        event = JobEvent(
            job_id=job_id,
            seq=seq,
            status=status,
            step=step,
            progress=progress,
            message=message,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def events_after(self, job_id: uuid.UUID, after_seq: int) -> list[JobEvent]:
        return list(
            self._session.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .where(JobEvent.seq > after_seq)
                .order_by(JobEvent.seq)
            )
        )

    def jobs_page(
        self, *, after_created_at: datetime | None = None, after_id: uuid.UUID | None = None, limit: int = 50
    ) -> list[Job]:
        from sqlalchemy import tuple_

        query = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
        if after_created_at is not None and after_id is not None:
            query = query.where(
                tuple_(Job.created_at, Job.id) < tuple_(after_created_at, after_id)
            )
        return list(self._session.scalars(query.limit(limit + 1)))


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_task(
        self,
        *,
        title: str,
        task_type: str,
        status: str = "open",
        priority: str = "normal",
        description: str | None = None,
        ref_type: str | None = None,
        ref_id: uuid.UUID | None = None,
        research_case_id: uuid.UUID | None = None,
        assignee: str | None = None,
    ) -> TaskItem:
        task = TaskItem(
            title=title,
            description=description,
            status=status,
            priority=priority,
            task_type=task_type,
            ref_type=ref_type,
            ref_id=ref_id,
            research_case_id=research_case_id,
            assignee=assignee,
            created_at=_utcnow(),
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get_task(self, task_id: uuid.UUID) -> TaskItem | None:
        return self._session.get(TaskItem, task_id)

    def find_by_ref(self, *, task_type: str, ref_type: str, ref_id: uuid.UUID) -> TaskItem | None:
        return self._session.scalar(
            select(TaskItem)
            .where(TaskItem.task_type == task_type)
            .where(TaskItem.ref_type == ref_type)
            .where(TaskItem.ref_id == ref_id)
        )

    def set_status(self, task: TaskItem, *, status: str, assignee: str | None = None) -> None:
        task.status = status
        if assignee is not None:
            task.assignee = assignee

    def close_review_task(
        self, task_type: str, ref_type: str, ref_id: uuid.UUID
    ) -> TaskItem | None:
        task = self.find_by_ref(task_type=task_type, ref_type=ref_type, ref_id=ref_id)
        if task is None or task.status == "done":
            return task
        if task.status in {"open", "in_progress"}:
            self.set_status(task, status="done")
        return task

    def tasks_page(
        self,
        *,
        case_id: uuid.UUID | None = None,
        status: str | None = None,
        assignee: str | None = None,
        after_created_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[TaskItem]:
        from sqlalchemy import tuple_

        query = select(TaskItem).order_by(
            TaskItem.created_at.desc(), TaskItem.id.desc()
        )
        if case_id is not None:
            query = query.where(TaskItem.research_case_id == case_id)
        if status is not None:
            query = query.where(TaskItem.status == status)
        if assignee is not None:
            query = query.where(TaskItem.assignee == assignee)
        if after_created_at is not None and after_id is not None:
            query = query.where(
                tuple_(TaskItem.created_at, TaskItem.id)
                < tuple_(after_created_at, after_id)
            )
        return list(self._session.scalars(query.limit(limit + 1)))


class ReviewAssignmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(
        self,
        *,
        proposal_id: uuid.UUID,
        assignee: str,
        lease_expires_at: datetime | None = None,
    ) -> ReviewAssignment:
        assignment = ReviewAssignment(
            proposal_id=proposal_id,
            assignee=assignee,
            claimed_at=_utcnow(),
            lease_expires_at=lease_expires_at,
            version=1,
        )
        self._session.add(assignment)
        self._session.flush()
        return assignment

    def active_assignment(self, proposal_id: uuid.UUID) -> ReviewAssignment | None:
        """The current, non-released, unexpired claim for a proposal, if any."""
        now = _utcnow()
        rows = list(
            self._session.scalars(
                select(ReviewAssignment)
                .where(ReviewAssignment.proposal_id == proposal_id)
                .where(ReviewAssignment.released_at.is_(None))
                .order_by(ReviewAssignment.claimed_at.desc())
            )
        )
        for row in rows:
            if row.lease_expires_at is None or row.lease_expires_at > now:
                return row
        return None


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> IdempotencyKey | None:
        return self._session.get(IdempotencyKey, key)

    def insert_in_progress(
        self, *, key: str, request_fingerprint: str, ttl_seconds: int = 3600
    ) -> IdempotencyKey:
        from datetime import timedelta

        row = IdempotencyKey(
            key=key,
            status="in_progress",
            request_fingerprint=request_fingerprint,
            created_at=_utcnow(),
            expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def complete(
        self, row: IdempotencyKey, *, response_status: int, response_payload: dict
    ) -> None:
        row.status = "completed"
        row.response_status = response_status
        row.response_payload = response_payload

    def fail(self, row: IdempotencyKey) -> None:
        # On failure, allow the client to retry: drop the row entirely so a
        # repeat with the same key starts fresh.
        self._session.delete(row)


class CheckpointRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        *,
        consumer: str,
        watermark: str | None,
        projection_schema_version: str | None = None,
        last_error: str | None = None,
    ) -> ProjectionCheckpoint:
        row = self._session.get(ProjectionCheckpoint, consumer)
        if row is None:
            row = ProjectionCheckpoint(consumer=consumer, updated_at=_utcnow())
            self._session.add(row)
        row.watermark = watermark
        row.projection_schema_version = projection_schema_version
        row.last_error = last_error
        row.updated_at = _utcnow()
        self._session.flush()
        return row
