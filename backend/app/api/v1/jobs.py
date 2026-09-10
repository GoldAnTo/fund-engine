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
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError
from app.repositories.operational import JobRepository
from app.repositories.outbox import emit_event
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
