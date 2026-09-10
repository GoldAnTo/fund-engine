from __future__ import annotations
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.schemas.v1.auto_research import (
    CancelRunResponse,
    ResearchRunEventsItemDTO,
    ResearchRunEventsResponse,
    RunListResponse,
    RunSummaryDTO,
    StartResearchRunRequest,
    ResearchRunResponse,
)
from app.services.auto_research import AutoResearchService

router = APIRouter(tags=["auto-research-v1"])

@router.post("/research-cases/{case_id}/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def start_run(case_id: uuid.UUID, request: StartResearchRunRequest, db: Session = Depends(get_db)):
    try:
        run = AutoResearchService(db).start(case_id, max_rounds=request.max_rounds, budget=request.budget, auto_execute=request.auto_execute)
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
):
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
def cancel_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    service = AutoResearchService(db)
    try:
        summary = service.cancel_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CancelRunResponse(**summary)


@router.get("/research-runs/{run_id}/events", response_model=ResearchRunEventsResponse)
def get_run_events(
    run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    service = AutoResearchService(db)
    detail = service.detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    run = service.repo.get_run(run_id)
    items = [
        ResearchRunEventsItemDTO(
            seq=1,
            status=run.status,
            stage=run.stage,
            round=run.round,
            stop_reason=run.stop_reason,
            message="run summary",
            created_at=run.updated_at.isoformat(),
        )
    ]
    return ResearchRunEventsResponse(run_id=str(run_id), items=items[:limit], has_more=False)


@router.get("/research-runs/{run_id}", response_model=ResearchRunResponse)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    detail = AutoResearchService(db).detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    return detail
