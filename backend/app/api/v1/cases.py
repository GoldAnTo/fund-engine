"""Case list and dossier v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.schemas.v1.cases import CaseListResponse, DossierResponse

router = APIRouter(prefix="/research-cases", tags=["research-cases-v1"])


@router.get("", response_model=CaseListResponse)
def list_cases(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        return CaseReadQueries(db).list_cases(cursor=cursor, limit=limit)
    except ValueError as exc:
        # Malformed cursor -> 422. (A unified v1 422 envelope is deferred to
        # the search task, which owns validation_failed handling.)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{case_id}/dossier", response_model=DossierResponse)
def dossier(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    return CaseReadQueries(db).dossier(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
