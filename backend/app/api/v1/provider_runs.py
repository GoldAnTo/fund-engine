"""Provider-run audit v1 routes (prototype Provider 运行记录)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.provider_runs import ProviderRunQueries
from app.schemas.v1.provider_runs import ProviderRunsResponse

router = APIRouter(prefix="/provider-runs", tags=["provider-runs-v1"])


@router.get("", response_model=ProviderRunsResponse)
def list_provider_runs(
    kind: str | None = Query(default=None, pattern="^(extract|propose|assess)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return ProviderRunQueries(db).list_runs(kind=kind, limit=limit)
