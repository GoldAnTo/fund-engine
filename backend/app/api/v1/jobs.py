"""Job read + control endpoints (design §8.7).

Reads the ``jobs`` / ``job_events`` operational tables.  Cancel / retry mutate
job state (guarded, not append-only) and emit a DomainEvent so the activity
feed reflects the transition.  The heavy AI work itself still runs synchronously
inside the engine command endpoints for now; this module owns the Job *contract*
and will later hand execution to a worker without changing these routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError
from app.repositories.operational import JobRepository
from app.repositories.outbox import emit_event
from app.models.operational import ResearchRun, ResearchTask
from app.models.event_research import EventResearchScopeVersion
from app.services.auto_research import IMPACT_STAGE_TASK_TYPES
from app.schemas.v1.common import CursorPage
from app.schemas.v1.operational import (
    ActivityItemDTO,
    JobDTO,
    JobEventDTO,
    JobEventsResponse,
)

# NOTE: no prefix here — the parent v1 router already mounts under /api/v1.
router = APIRouter(tags=["jobs-v1"])


def _job_dto(job) -> JobDTO:
    return JobDTO(
        id=str(job.id),
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        attempt=job.attempt,
        step=job.step,
        error=job.error,
        cancel_requested=job.cancel_requested,
        target_type=job.target_type,
        target_id=str(job.target_id) if job.target_id else None,
        research_case_id=str(job.research_case_id) if job.research_case_id else None,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.get("/jobs/{job_id}", response_model=JobDTO)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = JobRepository(db).get_job(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    return _job_dto(job)


@router.get("/jobs/{job_id}/events", response_model=JobEventsResponse)
def get_job_events(
    job_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    repo = JobRepository(db)
    if repo.get_job(job_id) is None:
        raise NotFoundError(f"job {job_id} not found")
    events = repo.events_after(job_id, after_seq)
    page = events[:limit]
    events_dto = [
        JobEventDTO(
            seq=e.seq,
            status=e.status,
            step=e.step,
            progress=e.progress,
            message=e.message,
            created_at=e.created_at.isoformat(),
        )
        for e in page
    ]
    has_more = len(events) > limit
    next_cursor = str(page[-1].seq) if has_more else None
    return JobEventsResponse(
        job_id=str(job_id),
        events=events_dto,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobDTO, status_code=status.HTTP_200_OK)
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = JobRepository(db)
    job = repo.get_job(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    if job.status in {"succeeded", "failed", "cancelled"}:
        raise NotFoundError(f"job {job_id} already terminal ({job.status})")
    repo.mark_cancellation_requested(job)
    repo.append_event(
        job_id=job.id,
        seq=repo.next_event_seq(job.id),
        status=job.status,
        message="cancel requested",
    )
    emit_event(
        db,
        type="job_progressed",
        aggregate_type="job",
        aggregate_id=job.id,
        payload={"status": job.status, "cancel": True},
        origin="operational",
    )
    db.commit()
    return _job_dto(job)


@router.post("/jobs/{job_id}/retries", response_model=JobDTO, status_code=status.HTTP_200_OK)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = JobRepository(db)
    job = repo.get_job(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    if job.status not in {"failed", "cancelled"}:
        raise NotFoundError(f"job {job_id} is not retryable (status={job.status})")
    if job.kind == "research_run" and job.target_id is not None:
        run = db.get(ResearchRun, job.target_id)
        if run is not None and run.status != "cancelled":
            latest_scope = db.scalar(
                select(EventResearchScopeVersion.id)
                .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
                .order_by(EventResearchScopeVersion.version.desc())
                .limit(1)
            )
            recovered_impact = False
            recovered_round: int | None = None
            recovered_scope_id: str | None = None
            for task in db.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.task_type == "impact_refresh")
                .where(ResearchTask.status == "failed")
            ):
                parts = task.query.split(":", 3)
                if len(parts) == 4 and str(latest_scope) == parts[2]:
                    task.status = "queued"
                    task.stage = "planned"
                    task.result = None
                    recovered_impact = True
                    recovered_round = task.round
                    recovered_scope_id = parts[2]
            # ``execute`` advances a run's round before doing its queued
            # tasks.  A retry of a failed first-round impact task therefore
            # must reopen that round; otherwise the task remains queued but
            # can never be selected by the worker.  Its failed attempt also
            # consumed one unit in ``execute``; refund that unit only for the
            # requeued impact task so a budget-bound retry can run it.
            if recovered_impact:
                # Reopen only dependent work for the same durable scope
                # handoff.  Stages never become successful stand-ins for a
                # failed refresh, and a retry must not revive a superseded or
                # already-completed scope task.
                for dependent in db.scalars(
                    select(ResearchTask)
                    .where(ResearchTask.run_id == run.id)
                    .where(ResearchTask.task_type.in_(IMPACT_STAGE_TASK_TYPES))
                    .where(ResearchTask.query.like(f"impact_stage:{recovered_scope_id}:%"))
                    .where(ResearchTask.status.in_(("queued", "failed", "blocked")))
                ):
                    dependent.status = "queued"
                    dependent.stage = "planned"
                    dependent.result = None
                # A late retry may happen after later rounds have already
                # advanced the run.  Reopen the failed task's own round,
                # never the current run round.
                run.round = max(0, (recovered_round or 1) - 1)
                run.budget_used = max(0, (run.budget_used or 0) - 1)
            run.status = "queued"
            run.stage = "planning"
            run.stop_reason = None
    job.status = "queued"
    job.attempt += 1
    job.error = None
    job.cancel_requested = False
    seq = repo.next_event_seq(job.id)
    repo.append_event(
        job_id=job.id, seq=seq, status="queued", message="retry requested"
    )
    emit_event(
        db,
        type="job_progressed",
        aggregate_type="job",
        aggregate_id=job.id,
        payload={"status": "queued", "retry": True, "attempt": job.attempt},
        origin="operational",
    )
    db.commit()
    return _job_dto(job)
