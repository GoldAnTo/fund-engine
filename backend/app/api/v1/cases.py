"""Case list and dossier v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import ResearchActor, require_research_actor, require_research_tenant
from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.queries.gaps import CaseGapQueries
from app.schemas.v1.cases import CaseListResponse, DossierResponse
from app.schemas.v1.gaps import CaseGapsResponse
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(
    prefix="/research-cases",
    tags=["research-cases-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.get("", response_model=CaseListResponse)
def list_cases(
    case_policy: RequireCaseRoute,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    # A malformed cursor raises ValidationFailedError, mapped globally to a
    # 422 validation_failed v1 envelope.
    return CaseReadQueries(db).list_cases(
        cursor=cursor,
        limit=limit,
        tenant_id=actor.tenant_id,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )


@router.get("/{case_id}/dossier", response_model=DossierResponse)
def dossier(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    research_mode: bool = False,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    CaseTenantAccess(db).require_case(case_id, tenant_id)
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
    tenant_id: str = Depends(require_research_tenant),
):
    """证据缺口聚合 (prototype 研究计划): open gaps across latest assessments."""
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    return CaseGapQueries(db).list_gaps(case_id=case_id, cutoff=cutoff)
