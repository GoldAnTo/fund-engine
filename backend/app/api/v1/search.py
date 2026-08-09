"""Grouped ledger search v1 routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import require_research_tenant
from app.queries.basis import HistoricalBasis
from app.queries.search import LedgerSearchQueries
from app.schemas.v1.search import SearchResponse

router = APIRouter(
    prefix="/search",
    tags=["search-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.get("", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2, max_length=200),
    types: str | None = None,
    cutoff: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    research_mode: bool = False,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    requested = (
        {t.strip() for t in types.split(",") if t.strip()} if types else None
    )
    return LedgerSearchQueries(db).search(
        q=q,
        types=requested,
        basis=HistoricalBasis.from_cutoff(cutoff),
        limit=limit,
        research_mode=research_mode,
        tenant_id=tenant_id,
    )
