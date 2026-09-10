"""Authenticated, redacted runtime readiness endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.dependencies import RequireCaseRoute
from app.api.v1.tenant_context import (
    ResearchActor,
    require_research_actor,
    require_runtime_administrator,
)
from app.db import get_db
from app.queries.runtime_status import RuntimeStatusQueries
from app.schemas.v1.runtime_status import CaseRuntimeStatusDTO, RuntimeStatusDTO


router = APIRouter(tags=["runtime-status-v1"])


@router.get("/runtime-status", response_model=RuntimeStatusDTO)
def runtime_status(
    db: Session = Depends(get_db),
    _actor: ResearchActor = Depends(require_runtime_administrator),
) -> RuntimeStatusDTO:
    return RuntimeStatusQueries(db).admin()


@router.get(
    "/event-research/{case_id}/runtime-status",
    response_model=CaseRuntimeStatusDTO,
)
def case_runtime_status(
    case_id: uuid.UUID,
    _case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> CaseRuntimeStatusDTO:
    return RuntimeStatusQueries(db).case(case_id, tenant_id=actor.tenant_id)

