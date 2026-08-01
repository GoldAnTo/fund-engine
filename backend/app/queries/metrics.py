"""Point-in-time metric catalog and series (prototype 数据中心).

Read-only views over frozen ValuationSnapshots: the catalog lists one row
per (stock, metric) with the latest frozen value; the series returns every
frozen value oldest-first.  Revisions are never rewritten — a newer
snapshot simply extends the series (修订审计的依据).
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import ValuationSnapshot
from app.repositories.instruments import InstrumentRepository
from app.schemas.v1.metrics import (
    MetricCatalogEntryDTO,
    MetricCatalogResponse,
    MetricPointDTO,
    MetricSeriesResponse,
)


class MetricQueries:
    def __init__(self, db: Session) -> None:
        self._instruments = InstrumentRepository(db)

    def catalog(
        self,
        *,
        stock_id: uuid.UUID | None = None,
        metric_name: str | None = None,
    ) -> MetricCatalogResponse:
        stock_ids = [stock_id] if stock_id else None
        snapshots = self._instruments.all_valuation_snapshots()
        if stock_ids is not None:
            snapshots = [s for s in snapshots if s.stock_id in stock_ids]
        if metric_name is not None:
            snapshots = [s for s in snapshots if s.metric_name == metric_name]

        latest: dict[tuple[uuid.UUID, str], ValuationSnapshot] = {}
        for snap in snapshots:
            key = (snap.stock_id, snap.metric_name)
            current = latest.get(key)
            if current is None or snap.as_of_date > current.as_of_date:
                latest[key] = snap

        entries: list[MetricCatalogEntryDTO] = []
        for (sid, metric), snap in sorted(
            latest.items(), key=lambda item: (str(item[0][0]), item[0][1])
        ):
            stock = self._instruments.stock_by_id(sid)
            if stock is None:
                continue
            entries.append(
                MetricCatalogEntryDTO(
                    stock_id=str(sid),
                    stock_code=stock.code,
                    stock_name=stock.name,
                    metric_name=metric,
                    latest_value=float(snap.metric_value),
                    latest_as_of=snap.as_of_date.isoformat(),
                    source=snap.source,
                    definition=snap.definition,
                )
            )
        return MetricCatalogResponse(entries=entries)

    def series(
        self, *, stock_id: uuid.UUID, metric_name: str
    ) -> MetricSeriesResponse:
        if self._instruments.stock_by_id(stock_id) is None:
            raise NotFoundError(f"stock {stock_id} not found")
        points = [
            MetricPointDTO(
                value=float(snap.metric_value),
                as_of_date=snap.as_of_date.isoformat(),
                source=snap.source,
                definition=snap.definition,
            )
            for snap in self._instruments.valuation_snapshots_for_stocks(
                [stock_id]
            )
            if snap.metric_name == metric_name
        ]
        return MetricSeriesResponse(
            stock_id=str(stock_id), metric_name=metric_name, points=points
        )
