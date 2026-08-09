"""Reviewed Case market-expression read endpoint."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.market_expression import MarketExpressionQueries
from app.schemas.v1.market_expression import MarketExpressionResponse


router = APIRouter(tags=["market-expression-v1"])


@router.get("/research-cases/{case_id}/market-expression", response_model=MarketExpressionResponse)
def get_market_expression(case_id: uuid.UUID, as_of: date | None = None, cutoff: datetime | None = None, db: Session = Depends(get_db)) -> MarketExpressionResponse:
    effective_as_of = as_of or datetime.now(timezone.utc).date()
    effective_cutoff = cutoff or datetime.combine(effective_as_of, time.max, tzinfo=timezone.utc)
    return MarketExpressionQueries(db).get(case_id=case_id, as_of=effective_as_of, cutoff=effective_cutoff)
