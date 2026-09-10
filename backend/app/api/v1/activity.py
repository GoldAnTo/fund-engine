"""Activity, evidence-changes and task-queue read endpoints (design §8.7).

All three feeds read from the ``domain_events`` outbox (a valid projection of
itself, design §9.4) so they stay consistent with the ledger and are
rebuildable.  Cursors are opaque event-id references; clients pass ``after=``
the last seen ``event_id`` to page forward.  SSE is an optimization only — the
plain GET here is sufficient to recover full state.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.activity import ActivityQueries
from app.repositories.operational import TaskRepository
from app.schemas.v1.operational import (
    ActivityItemDTO,
    ActivityResponse,
    TaskCreateRequest,
    TaskItemDTO,
    TasksResponse,
    TaskUpdateRequest,
)
from app.api.v1.dependencies import RequireCaseRoute
from app.services.case_authorization import CaseAuthorizationService

# NOTE: no prefix here — the parent v1 router already mounts under /api/v1.
router = APIRouter(tags=["activity-v1"])


def _to_dto(event) -> ActivityItemDTO:
    payload = event.payload
    if event.type in {
        "case_access_granted",
        "case_access_role_changed",
        "case_access_revoked",
    }:
        private_payload = event.payload if isinstance(event.payload, dict) else {}
        payload = {
            key: private_payload[key]
            for key in ("case_id", "old_role", "new_role")
            if key in private_payload
        }
    return ActivityItemDTO(
        event_id=str(event.id),
        type=event.type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        ref_type=event.ref_type,
        ref_id=event.ref_id,
        origin=event.origin,
        payload=payload,
        actor=event.actor,
        created_at=event.created_at.isoformat(),
    )


@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last event_id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        case_policy.require(case_id)
    visible_case_ids = {
        str(value) for value in db.scalars(case_policy.authorized_case_ids())
    }
    after_id = uuid.UUID(after) if after else None
    rows, has_more = ActivityQueries(db).activity(
        case_id=case_id,
        authorized_case_ids=visible_case_ids,
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
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last event_id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        case_policy.require(case_id)
    visible_case_ids = {
        str(value) for value in db.scalars(case_policy.authorized_case_ids())
    }
    after_id = uuid.UUID(after) if after else None
    rows, has_more = ActivityQueries(db).evidence_changes(
        case_id=case_id,
        authorized_case_ids=visible_case_ids,
        after_id=after_id,
        limit=limit,
    )
    items = [_to_dto(e) for e in rows]
    next_cursor = items[-1].event_id if has_more else None
    return ActivityResponse(items=items, next_cursor=next_cursor, has_more=has_more)


@router.post("/tasks", response_model=TaskItemDTO, status_code=status.HTTP_201_CREATED)
def create_task(
    case_policy: RequireCaseRoute,
    payload: TaskCreateRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    research_case_id = (
        uuid.UUID(payload.research_case_id) if payload.research_case_id else None
    )
    if research_case_id is None:
        from app.errors import ValidationFailedError

        raise ValidationFailedError("research_case_id is required")
    case_policy.require(research_case_id)
    assignee = None
    if payload.assignee is not None:
        assignee = str(
            CaseAuthorizationService(db)
            .require_assignable_user(research_case_id, actor, payload.assignee)
            .id
        )
    repo = TaskRepository(db)
    task = repo.add_task(
        title=payload.title,
        description=payload.description,
        task_type=payload.task_type,
        priority=payload.priority,
        ref_type=payload.ref_type,
        ref_id=uuid.UUID(payload.ref_id) if payload.ref_id else None,
        research_case_id=research_case_id,
        assignee=assignee,
    )
    db.commit()
    return _task_to_dto(task)


@router.patch("/tasks/{task_id}", response_model=TaskItemDTO)
def update_task(
    case_policy: RequireCaseRoute,
    task_id: uuid.UUID,
    payload: TaskUpdateRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    repo = TaskRepository(db)
    task = repo.get_task(task_id)
    if task is None:
        from app.errors import NotFoundError
        raise NotFoundError(f"task {task_id} not found")
    if task.research_case_id is None:
        from app.errors import NotFoundError

        raise NotFoundError(f"task {task_id} not found")
    case_policy.require(task.research_case_id)
    assignee = None
    if payload.assignee is not None:
        assignee = str(
            CaseAuthorizationService(db)
            .require_assignable_user(task.research_case_id, actor, payload.assignee)
            .id
        )
    repo.set_status(task, status=payload.status, assignee=assignee)
    db.commit()
    return _task_to_dto(task)


def _task_to_dto(task) -> TaskItemDTO:
    return TaskItemDTO(
        id=str(task.id),
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        task_type=task.task_type,
        ref_type=task.ref_type,
        ref_id=str(task.ref_id) if task.ref_id else None,
        research_case_id=str(task.research_case_id) if task.research_case_id else None,
        assignee=task.assignee,
        created_at=task.created_at.isoformat(),
        due_at=task.due_at.isoformat() if task.due_at else None,
    )

@router.get("/tasks", response_model=TasksResponse)
def get_tasks(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    status: str | None = None,
    assignee: str | None = None,
    after: str | None = Query(default=None, description="opaque cursor = last task id"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    repo = TaskRepository(db)
    after_id = uuid.UUID(after) if after else None
    after_created = None
    if case_id is not None:
        case_policy.require(case_id)
    if after_id is not None:
        task = repo.get_task(after_id)
        if task is None or task.research_case_id is None:
            from app.errors import NotFoundError

            raise NotFoundError(f"task cursor {after_id} not found")
        case_policy.require(task.research_case_id)
        after_created = task.created_at
    rows = repo.tasks_page(
        case_id=case_id,
        authorized_case_ids=case_policy.authorized_case_ids(),
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
