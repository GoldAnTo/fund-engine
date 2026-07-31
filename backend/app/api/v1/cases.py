"""Case list and dossier v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.queries.graph import RelationshipGraphQueries
from app.schemas.v1.cases import CaseListResponse, DossierResponse
from app.schemas.v1.graph import GraphResponse

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


@router.get("/{case_id}/graph", response_model=GraphResponse)
def graph(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    focus: str | None = None,
    depth: int = Query(default=4, ge=1, le=8),
    limit: int = Query(default=200, ge=1, le=500),
    research_mode: bool = False,
    db: Session = Depends(get_db),
):
    return RelationshipGraphQueries(db).load(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        focus=focus,
        depth=depth,
        limit=limit,
        research_mode=research_mode,
    )
