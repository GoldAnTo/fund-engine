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
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.repositories.operational import JobRepository
from app.schemas.v1.operational import (
    JobDTO,
    JobEventDTO,
    JobEventsResponse,
)
from app.services.jobs import JobService
from app.api.v1.dependencies import RequireCaseRoute

# NOTE: no prefix here — the parent v1 router already mounts under /api/v1.
router = APIRouter(tags=["jobs-v1"])


def _authorize_job_case(job, case_policy: RequireCaseRoute) -> None:
    if job.research_case_id is not None:
        case_policy.require(job.research_case_id)


def _job_dto(job) -> JobDTO:
    return JobDTO(
        id=str(job.id),
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        attempt=job.attempt,
        failure_count=job.failure_count,
        next_retry_at=(job.next_retry_at.isoformat() if job.next_retry_at else None),
        retry_policy_version=job.retry_policy_version,
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
def get_job(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    job = JobService(db).get_authorized(job_id, tenant_id=actor.tenant_id)
    _authorize_job_case(job, case_policy)
    return _job_dto(job)


@router.get("/jobs/{job_id}/events", response_model=JobEventsResponse)
def get_job_events(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    repo = JobRepository(db)
    job = JobService(db).get_authorized(job_id, tenant_id=actor.tenant_id)
    _authorize_job_case(job, case_policy)
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
def cancel_job(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    existing = JobService(db).get_authorized(job_id, tenant_id=actor.tenant_id)
    _authorize_job_case(existing, case_policy)
    job = JobService(db).request_cancel_authorized(
        job_id, tenant_id=actor.tenant_id, actor=actor.server_actor
    )
    db.commit()
    return _job_dto(job)


@router.post("/jobs/{job_id}/retries", response_model=JobDTO, status_code=status.HTTP_200_OK)
def retry_job(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    existing = JobService(db).get_authorized(job_id, tenant_id=actor.tenant_id)
    _authorize_job_case(existing, case_policy)
    job = JobService(db).retry_authorized(
        job_id, tenant_id=actor.tenant_id, actor=actor.server_actor
    )
    db.commit()
    return _job_dto(job)
