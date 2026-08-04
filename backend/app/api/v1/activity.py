"""Activity, evidence-changes and task-queue read endpoints (design §8.7).

All three feeds read from the ``domain_events`` outbox (a valid projection of
itself, design §9.4) so they stay consistent with the ledger and are
rebuildable.  Cursors are opaque event-id references; clients pass ``after=``
the last seen ``event_id`` to page forward.  SSE is an optimization only — the
plain GET here is sufficient to recover full state.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.activity import ActivityQueries
from app.repositories.operational import TaskRepository
from app.schemas.v1.operational import (
    ActivityItemDTO,
    ActivityResponse,
    TaskItemDTO,
    TasksResponse,
)

# NOTE: no prefix here — the parent v1 router already mounts under /api/v1.
router = APIRouter(tags=["activity-v1"])


def _to_dto(event) -> ActivityItemDTO:
    return ActivityItemDTO(
        event_id=str(event.id),
        type=event.type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        ref_type=event.ref_type,
        ref_id=event.ref_id,
        origin=event.origin,
        payload=event.payload,
        actor=event.actor,
        created_at=event.created_at.isoformat(),
    )


@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    case_id: uuid.UUID | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last event_id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    after_id = uuid.UUID(after) if after else None
    rows, has_more = ActivityQueries(db).activity(
        case_id=case_id,
        actor_id=actor_id,
        event_type=event_type,
        after_id=after_id,
        limit=limit,
    )
    items = [_to_dto(e) for e in rows]
    next_cursor = items[-1].event_id if has_more else None
    return ActivityResponse(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/evidence-changes", response_model=ActivityResponse)
def get_evidence_changes(
    case_id: uuid.UUID | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last event_id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    after_id = uuid.UUID(after) if after else None
    rows, has_more = ActivityQueries(db).evidence_changes(
        case_id=case_id, after_id=after_id, limit=limit
    )
    items = [_to_dto(e) for e in rows]
    next_cursor = items[-1].event_id if has_more else None
    return ActivityResponse(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/tasks", response_model=TasksResponse)
def get_tasks(
    case_id: uuid.UUID | None = None,
    status: str | None = None,
    assignee: str | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last task id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    repo = TaskRepository(db)
    after_id = uuid.UUID(after) if after else None
    after_created = None
    if after_id is not None:
        task = repo.get_task(after_id)
        after_created = task.created_at if task else None
    rows = repo.tasks_page(
        case_id=case_id,
        status=status,
        assignee=assignee,
        after_created_at=after_created,
        after_id=after_id,
        limit=limit,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        TaskItemDTO(
            id=str(t.id),
            title=t.title,
            description=t.description,
            status=t.status,
            priority=t.priority,
            task_type=t.task_type,
            ref_type=t.ref_type,
            ref_id=str(t.ref_id) if t.ref_id else None,
            research_case_id=str(t.research_case_id) if t.research_case_id else None,
            assignee=t.assignee,
            created_at=t.created_at.isoformat(),
            due_at=t.due_at.isoformat() if t.due_at else None,
        )
        for t in page
    ]
    next_cursor = items[-1].id if has_more else None
    return TasksResponse(items=items, next_cursor=next_cursor, has_more=has_more)
