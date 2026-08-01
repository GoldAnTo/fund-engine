"""Theme↔fund penetration v1 routes."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.penetration import PenetrationQueries
from app.schemas.v1.penetration import (
    FundCompositionResponse,
    FundExposureResponse,
)

router = APIRouter(tags=["penetration-v1"])


@router.get(
    "/research-cases/{case_id}/fund-exposure",
    response_model=FundExposureResponse,
)
def case_fund_exposure(
    case_id: uuid.UUID,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return PenetrationQueries(db).case_fund_exposure(
        case_id=case_id, as_of=as_of or date.today()
    )


@router.get("/funds/{fund_id}/composition", response_model=FundCompositionResponse)
def fund_composition(
    fund_id: uuid.UUID,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return PenetrationQueries(db).fund_composition(
        fund_id=fund_id, as_of=as_of or date.today()
    )
