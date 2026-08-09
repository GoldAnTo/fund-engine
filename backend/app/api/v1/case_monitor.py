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
)
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
from app.services.auto_research import AutoResearchService
from app.schemas.v1.auto_research import ResearchRunResponse


router = APIRouter(tags=["case-monitor-v1"])


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
def get_monitor(case_id: uuid.UUID, db: Session = Depends(get_db)):
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
        confirmed_factors=[
            ConfirmedFactorOptionDTO(id=str(factor.id), statement=factor.statement)
            for factor in query.confirmed_factors(case_id)
        ],
    )


@router.put("/research-cases/{case_id}/monitor", response_model=CaseMonitorDTO)
def save_monitor(
    case_id: uuid.UUID,
    request: UpdateCaseMonitorRequest,
    db: Session = Depends(get_db),
):
    try:
        monitor = CaseMonitorService(db).save(
            case_id,
            actor=request.actor,
            config=CaseMonitorConfig(
                frequency=request.frequency,
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
def start_manual_monitor_run(case_id: uuid.UUID, db: Session = Depends(get_db)):
    """Queue an explicit replenishment using the effective monitor version."""
    try:
        service = AutoResearchService(db)
        run = service.start_from_monitor(case_id)
        return service.detail(run.id)
    except ValueError as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc


@router.post("/research-cases/{case_id}/monitor/{target_status}", response_model=CaseMonitorDTO)
def set_monitor_status(case_id: uuid.UUID, target_status: str, request: SetCaseMonitorStatusRequest, db: Session = Depends(get_db)):
    try:
        monitor = CaseMonitorService(db).set_status(case_id, actor=request.actor, status=target_status, reason=request.change_reason)
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _dto(monitor)
