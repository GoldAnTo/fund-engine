"""Honest research overview v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import ResearchActor, require_research_actor, require_research_tenant
from app.queries.basis import HistoricalBasis
from app.queries.overview import OverviewQueries
from app.schemas.v1.overview import OverviewResponse
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(
    prefix="/overview",
    tags=["overview-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.get("", response_model=OverviewResponse)
def overview(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
    case_policy.require(case_id)
    return OverviewQueries(db).load(
        case_id=case_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
