"""Point-in-time metric v1 wire DTOs (prototype 数据中心)."""
from __future__ import annotations

from app.schemas.v1.common import V1Model


class MetricCatalogEntryDTO(V1Model):
    """One catalog row: an entity-metric pair with its latest frozen value."""

    stock_id: str
    stock_code: str
    stock_name: str
    metric_name: str
    latest_value: float
    latest_as_of: str
    source: str
    definition: str | None


class MetricCatalogResponse(V1Model):
    entries: list[MetricCatalogEntryDTO]


class MetricPointDTO(V1Model):
    value: float
    as_of_date: str
    source: str
    definition: str | None


class MetricSeriesResponse(V1Model):
    """冻结时点序列: every frozen value, oldest first (可用性由 as_of 判断)."""

    stock_id: str
    metric_name: str
    points: list[MetricPointDTO]
