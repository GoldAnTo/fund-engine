"""Case monitor read and append-only update endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ValidationFailedError
from app.queries.case_monitor import CaseMonitorQuery
from app.schemas.v1.case_monitor import (
    CaseMonitorDTO,
    CaseMonitorDetailResponse,
    ConfirmedFactorOptionDTO,
    LatestResearchRunDTO,
    UpdateCaseMonitorRequest,
    SetCaseMonitorStatusRequest,
    StartFactorMonitorRunRequest,
)
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
from app.services.auto_research import AutoResearchService
from app.services.monitor_scheduler import (
    ACCEPTANCE_FREQUENCY,
    MonitorScheduler,
    acceptance_frequency_enabled,
)
from app.schemas.v1.auto_research import ResearchRunResponse
from app.api.v1.tenant_context import ResearchActor, require_research_actor, require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess


router = APIRouter(
    tags=["case-monitor-v1"], dependencies=[Depends(require_research_tenant)]
)


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _dto(monitor) -> CaseMonitorDTO:
    return CaseMonitorDTO(
        id=str(monitor.id),
        version=monitor.version,
        status=monitor.status,
        frequency=monitor.frequency,
        factor_ids=monitor.factor_ids,
        allowed_source_types=monitor.allowed_source_types,
        next_verification_event=monitor.next_verification_event,
        budget=monitor.budget,
        changed_by=monitor.changed_by,
        change_reason=monitor.change_reason,
        created_at=monitor.created_at,
    )


@router.get("/research-cases/{case_id}/monitor", response_model=CaseMonitorDetailResponse)
def get_monitor(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    _require_case(db, case_id, tenant_id)
    query = CaseMonitorQuery(db)
    monitor = query.effective(case_id)
    run = query.latest_run(case_id)
    return CaseMonitorDetailResponse(
        monitor=_dto(monitor) if monitor is not None else None,
        history=[_dto(item) for item in query.history(case_id)],
        latest_run=(
            LatestResearchRunDTO(
                id=str(run.id), status=run.status, stage=run.stage, updated_at=run.updated_at
            )
            if run is not None
            else None
        ),
        next_scheduled_at=(MonitorScheduler.next_due_at(monitor.frequency) if monitor is not None and monitor.status == "active" else None),
        confirmed_factors=[
            ConfirmedFactorOptionDTO(id=str(factor.id), statement=factor.statement)
            for factor in query.confirmed_factors(case_id, monitor)
        ],
        available_confirmed_factors=[
            ConfirmedFactorOptionDTO(id=str(factor.id), statement=factor.statement)
            for factor in query.available_confirmed_factors(case_id)
        ],
    )


@router.put("/research-cases/{case_id}/monitor", response_model=CaseMonitorDTO)
def save_monitor(
    case_id: uuid.UUID,
    request: UpdateCaseMonitorRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_case(db, case_id, actor.tenant_id)
    try:
        normalized_frequency = request.frequency.strip()
        if (
            normalized_frequency == ACCEPTANCE_FREQUENCY
            and not acceptance_frequency_enabled()
        ):
            raise ValueError("acceptance-only monitor frequency is disabled")
        monitor = CaseMonitorService(db).save(
            case_id,
            actor=actor.server_actor,
            config=CaseMonitorConfig(
                frequency=normalized_frequency,
                factor_ids=[uuid.UUID(value) for value in request.factor_ids],
                allowed_source_types=list(request.allowed_source_types),
                next_verification_event=request.next_verification_event,
                budget=request.budget,
                change_reason=request.change_reason,
            ),
        )
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _dto(monitor)


@router.post(
    "/research-cases/{case_id}/monitor/runs",
    response_model=ResearchRunResponse,
    status_code=201,
)
def start_manual_monitor_run(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    """Queue an explicit replenishment using the effective monitor version."""
    _require_case(db, case_id, actor.tenant_id)
    try:
        service = AutoResearchService(db)
        run = service.start_from_monitor(case_id, initiated_by=actor.server_actor)
        return service.detail(run.id)
    except ValueError as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc


@router.post(
    "/research-cases/{case_id}/monitor/factor-runs",
    response_model=ResearchRunResponse,
    status_code=201,
)
def start_factor_monitor_run(
    case_id: uuid.UUID,
    request: StartFactorMonitorRunRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_case(db, case_id, actor.tenant_id)
    try:
        service = AutoResearchService(db)
        run = service.start_from_key_factor(
            case_id,
            key_factor_id=uuid.UUID(request.key_factor_id),
            initiated_by=actor.server_actor,
        )
        return service.detail(run.id)
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc


@router.post("/research-cases/{case_id}/monitor/{target_status}", response_model=CaseMonitorDTO)
def set_monitor_status(
    case_id: uuid.UUID,
    target_status: str,
    request: SetCaseMonitorStatusRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    _require_case(db, case_id, actor.tenant_id)
    try:
        monitor = CaseMonitorService(db).set_status(case_id, actor=actor.server_actor, status=target_status, reason=request.change_reason)
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _dto(monitor)
