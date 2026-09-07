"""Job read + control endpoints (design §8.7).

Reads the ``jobs`` / ``job_events`` operational tables.  Cancel / retry mutate
job state (guarded, not append-only) and emit a DomainEvent so the activity
feed reflects the transition.  The heavy AI work itself still runs synchronously
inside the engine command endpoints for now; this module owns the Job *contract*
and will later hand execution to a worker without changing these routes.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.operational import Job

from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
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
from app.services.jobs import JobService

# NOTE: no prefix here — the parent v1 router already mounts under /api/v1.
router = APIRouter(tags=["jobs-v1"], dependencies=[Depends(require_research_tenant)])


def _owned_job(db: Session, job_id: uuid.UUID, tenant_id: str):
    job = db.scalar(select(Job).where(
        Job.id == job_id,
        Job.research_case_id.in_(CaseTenantAccess(db).case_ids(tenant_id)),
    ))
    if job is None:
        raise NotFoundError("job not found")
    return job


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
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), tenant_id: str = Depends(require_research_tenant)):
    job = _owned_job(db, job_id, tenant_id)
    return _job_dto(job)


@router.get("/jobs/{job_id}/events", response_model=JobEventsResponse)
def get_job_events(
    job_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    repo = JobRepository(db)
    _owned_job(db, job_id, tenant_id)
    events = repo.events_after(job_id, after_seq, limit=limit + 1)
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
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db), tenant_id: str = Depends(require_research_tenant)):
    repo = JobRepository(db)
    job = _owned_job(db, job_id, tenant_id)
    JobService(db).request_cancel(job)
    db.commit()
    return _job_dto(job)


@router.post("/jobs/{job_id}/retries", response_model=JobDTO, status_code=status.HTTP_200_OK)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db), tenant_id: str = Depends(require_research_tenant)):
    repo = JobRepository(db)
    job = _owned_job(db, job_id, tenant_id)
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
