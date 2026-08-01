"""Point-in-time metric v1 routes (prototype 数据中心)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.metrics import MetricQueries
from app.schemas.v1.metrics import MetricCatalogResponse, MetricSeriesResponse

router = APIRouter(prefix="/metrics", tags=["metrics-v1"])


@router.get("/catalog", response_model=MetricCatalogResponse)
def metric_catalog(
    stock_id: uuid.UUID | None = None,
    metric_name: str | None = None,
    db: Session = Depends(get_db),
):
    return MetricQueries(db).catalog(
        stock_id=stock_id, metric_name=metric_name
    )


@router.get("/series", response_model=MetricSeriesResponse)
def metric_series(
    stock_id: uuid.UUID,
    metric_name: str,
    db: Session = Depends(get_db),
):
    return MetricQueries(db).series(stock_id=stock_id, metric_name=metric_name)
