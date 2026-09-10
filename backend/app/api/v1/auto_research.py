from __future__ import annotations
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.research_monitor import ResearchRunEvent
from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.db import get_db
from app.schemas.v1.auto_research import (
    CancelRunResponse,
    CancelRunRequest,
    ResearchRunEventsItemDTO,
    ResearchRunEventsResponse,
    ActiveResearchRunDTO,
    ActiveResearchRunsResponse,
    ResearchWorkerStatusDTO,
    FrozenRunScopeDTO,
    ResearchRunArchiveDTO,
    ResearchRunArchiveResponse,
    RunListResponse,
    RunSummaryDTO,
    StartResearchRunRequest,
    ResearchRunResponse,
)
from app.services.auto_research import AutoResearchService
from app.api.v1.tenant_context import (
    ResearchActor,
    require_research_actor,
    require_research_tenant,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute
from app.services.research_worker_heartbeat import WorkerHeartbeatService

router = APIRouter(
    tags=["auto-research-v1"], dependencies=[Depends(require_research_tenant)]
)


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _require_run(
    db: Session,
    run_id: uuid.UUID,
    actor: ResearchActor,
    case_policy: RequireCaseRoute,
) -> ResearchRun:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    _require_case(db, run.research_case_id, actor.tenant_id)
    case_policy.require(run.research_case_id)
    return run


def _frozen_scope(db: Session, run: ResearchRun) -> FrozenRunScopeDTO:
    scope_event = db.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run.id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    payload = scope_event.payload_json if scope_event is not None else {}
    return FrozenRunScopeDTO(
        trigger=payload.get("trigger"),
        monitor_version_id=payload.get("monitor_version_id"),
        factor_ids=list(payload.get("factor_ids") or []),
        factor_statements=list(payload.get("factor_statements") or []),
        allowed_source_types=list(payload.get("allowed_source_types") or []),
        budget=payload.get("budget"),
        frequency=payload.get("frequency"),
        next_verification_event=payload.get("next_verification_event"),
        configured_by=payload.get("configured_by"),
        configuration_change_reason=payload.get("configuration_change_reason"),
    )


def _next_action(run: ResearchRun) -> str:
    if run.status == "waiting_for_review":
        return "审核待审候选"
    if run.status == "failed":
        return "查看失败原因"
    if run.status == "cancelled":
        return "查看取消记录"
    return "查看本次运行"


def _run_item(db: Session, run: ResearchRun) -> ActiveResearchRunDTO:
    case = db.get(ResearchCase, run.research_case_id)
    return ActiveResearchRunDTO(
        run_id=str(run.id),
        case_id=str(run.research_case_id),
        case_title=case.title if case is not None else "已删除 Case",
        status=run.status,
        stage=run.stage,
        updated_at=run.updated_at.isoformat(),
        processed_count=run.budget_used,
        next_action=_next_action(run),
        scope=_frozen_scope(db, run),
    )


@router.get("/research-runs/active", response_model=ActiveResearchRunsResponse)
def list_active_runs(
    case_policy: RequireCaseRoute,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    """Return active work with the run's recorded scope, never live monitor settings."""
    runs = list(
        db.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.research_case_id.in_(
                    CaseTenantAccess(db).case_ids(actor.tenant_id)
                )
            )
            .where(
                ResearchRun.research_case_id.in_(
                    case_policy.authorized_case_ids()
                )
            )
            .where(ResearchRun.status.in_(("queued", "running", "waiting_for_review")))
            .order_by(ResearchRun.updated_at.desc(), ResearchRun.id.desc())
            .limit(limit + 1)
        )
    )
    page = runs[:limit]
    items = [_run_item(db, run) for run in page]
    return ActiveResearchRunsResponse(
        items=items,
        has_more=len(runs) > limit,
        next_cursor=None,
    )


@router.get("/research-runs/worker-status", response_model=ResearchWorkerStatusDTO)
def worker_status(
    db: Session = Depends(get_db),
):
    """Expose whether queued runs can currently be claimed by a loop worker."""
    status = WorkerHeartbeatService(db).status()
    return ResearchWorkerStatusDTO(
        status=status["status"],
        last_seen_at=status["last_seen_at"],
        mode=status["mode"],
        state=status["state"],
    )


@router.get("/research-runs", response_model=ResearchRunArchiveResponse)
def list_run_archive(
    case_policy: RequireCaseRoute,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    """List current and terminal runs without reconstructing their scope."""
    runs = list(
        db.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.research_case_id.in_(
                    CaseTenantAccess(db).case_ids(actor.tenant_id)
                )
            )
            .where(
                ResearchRun.research_case_id.in_(
                    case_policy.authorized_case_ids()
                )
            )
            .order_by(ResearchRun.updated_at.desc(), ResearchRun.id.desc())
            .limit(limit + 1)
        )
    )
    page = runs[:limit]
    items = [
        ResearchRunArchiveDTO(
            **_run_item(db, run).model_dump(),
            created_at=run.created_at.isoformat(),
            stop_reason=run.stop_reason,
        )
        for run in page
    ]
    return ResearchRunArchiveResponse(
        items=items,
        has_more=len(runs) > limit,
        next_cursor=None,
    )

@router.post("/research-cases/{case_id}/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def start_run(
    case_id: uuid.UUID,
    request: StartResearchRunRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_case(db, case_id, actor.tenant_id)
    try:
        run = AutoResearchService(db).start(
            case_id,
            max_rounds=request.max_rounds,
            budget=request.budget,
            auto_execute=request.auto_execute,
            initiated_by=actor.server_actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AutoResearchService(db).detail(run.id)

@router.get("/research-cases/{case_id}/runs", response_model=RunListResponse)
def list_runs(
    case_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    after_created_at: datetime | None = Query(default=None),
    after_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    _require_case(db, case_id, tenant_id)
    service = AutoResearchService(db)
    runs = service.list_runs(
        case_id,
        limit=limit + 1,
        after_created_at=after_created_at,
        after_id=after_id,
    )
    page = runs[:limit]
    has_more = len(runs) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last['created_at']}|{last['id']}"
    return RunListResponse(
        items=[RunSummaryDTO(**run) for run in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/research-runs/{run_id}/cancel", response_model=CancelRunResponse)
def cancel_run(
    case_policy: RequireCaseRoute,
    run_id: uuid.UUID,
    request: CancelRunRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_run(db, run_id, actor, case_policy)
    service = AutoResearchService(db)
    try:
        summary = service.cancel_run(
            run_id,
            actor=actor.server_actor,
            change_reason=request.change_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CancelRunResponse(**summary)


@router.get("/research-runs/{run_id}/events", response_model=ResearchRunEventsResponse)
def get_run_events(
    case_policy: RequireCaseRoute,
    run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_run(db, run_id, actor, case_policy)
    service = AutoResearchService(db)
    detail = service.detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    rows = list(
        db.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run_id)
            .order_by(ResearchRunEvent.seq)
            .limit(limit + 1)
        )
    )
    page = rows[:limit]
    items = [
        ResearchRunEventsItemDTO(
            seq=event.seq,
            status=event.status,
            stage=event.stage,
            message=event.message,
            details=event.payload_json,
            created_at=event.created_at.isoformat(),
        )
        for event in page
    ]
    return ResearchRunEventsResponse(
        run_id=str(run_id),
        items=items,
        has_more=len(rows) > limit,
    )


@router.get("/research-runs/{run_id}", response_model=ResearchRunResponse)
def get_run(
    case_policy: RequireCaseRoute,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_run(db, run_id, actor, case_policy)
    detail = AutoResearchService(db).detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    return detail
