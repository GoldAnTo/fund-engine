"""Connected relationship graph v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import require_research_tenant
from app.queries.basis import HistoricalBasis
from app.queries.graph import RelationshipGraphQueries
from app.schemas.v1.graph import GraphResponse
from app.services.case_tenant_access import CaseTenantAccess

router = APIRouter(
    prefix="/research-cases",
    tags=["relationship-graph-v1"],
    dependencies=[Depends(require_research_tenant)],
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
    tenant_id: str = Depends(require_research_tenant),
):
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    return RelationshipGraphQueries(db).load(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        focus=focus,
        depth=depth,
        limit=limit,
        research_mode=research_mode,
    )
