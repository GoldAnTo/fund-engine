"""Company-centric read routes (公司研究 v1)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.companies import CompanyReadQueries
from app.schemas.v1.companies import CompanyDossierResponse, CompanyListResponse
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(prefix="/companies", tags=["companies-v1"])


@router.get("", response_model=CompanyListResponse)
def list_companies(
    case_policy: RequireCaseRoute,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    return CompanyReadQueries(db).list_companies(
        query=q,
        limit=limit,
        cursor=cursor,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )


@router.get("/{company_id}", response_model=CompanyDossierResponse)
def company_dossier(
    case_policy: RequireCaseRoute,
    company_id: uuid.UUID,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    return CompanyReadQueries(db).dossier(
        company_id=company_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        authorized_case_ids=case_policy.authorized_case_ids(),
    )
