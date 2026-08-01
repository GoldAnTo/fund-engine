"""Case list and dossier v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.queries.gaps import CaseGapQueries
from app.schemas.v1.cases import CaseListResponse, DossierResponse
from app.schemas.v1.gaps import CaseGapsResponse

router = APIRouter(prefix="/research-cases", tags=["research-cases-v1"])


@router.get("", response_model=CaseListResponse)
def list_cases(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # A malformed cursor raises ValidationFailedError, mapped globally to a
    # 422 validation_failed v1 envelope.
    return CaseReadQueries(db).list_cases(cursor=cursor, limit=limit)


@router.get("/{case_id}/dossier", response_model=DossierResponse)
def dossier(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    research_mode: bool = False,
    db: Session = Depends(get_db),
):
    return CaseReadQueries(db).dossier(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        research_mode=research_mode,
    )


@router.get("/{case_id}/gaps", response_model=CaseGapsResponse)
def gaps(
    case_id: uuid.UUID,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    """证据缺口聚合 (prototype 研究计划): open gaps across latest assessments."""
    return CaseGapQueries(db).list_gaps(case_id=case_id, cutoff=cutoff)
