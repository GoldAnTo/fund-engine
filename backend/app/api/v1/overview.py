"""Honest research overview v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.overview import OverviewQueries
from app.schemas.v1.overview import OverviewResponse

router = APIRouter(prefix="/overview", tags=["overview-v1"])


@router.get("", response_model=OverviewResponse)
def overview(
    case_id: uuid.UUID,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    return OverviewQueries(db).load(
        case_id=case_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
