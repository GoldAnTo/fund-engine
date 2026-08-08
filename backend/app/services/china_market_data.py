"""Ledger-first China A-share and fund-data gateway.

This module deliberately reads only immutable ledger facts.  A live provider
may be used by a separate ingest command, but it must first normalize and
append ``ValuationSnapshot`` / ``HoldingDisclosure`` rows before impact
research can consume it.  Consequently an unconfigured provider is honest:
the gateway returns no observations and callers record an evidence gap rather
than inventing coverage.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import HoldingDisclosure, Stock, ValuationSnapshot


OPERATING_METRICS = frozenset({"REVENUE_YOY", "GROSS_MARGIN", "ORDER_GUIDANCE"})
MARKET_METRICS = frozenset({"EVENT_RETURN_1D", "TURNOVER_RATE", "PE_TTM"})
PEER_METRICS = frozenset({"PEER_RETURN_1D", "INDUSTRY_RETURN_1D", "PEER_PE_TTM"})


class ChinaMarketData(Protocol):
    def operating_observations(
        self, stock: Stock, *, as_of: date
    ) -> Sequence[ValuationSnapshot]: ...

    def market_observations(
        self, stock: Stock, *, as_of: date
    ) -> Sequence[ValuationSnapshot]: ...

    def peer_observations(
        self, stock: Stock, *, as_of: date
    ) -> Sequence[ValuationSnapshot]: ...

    def fund_holdings(
        self, stock_ids: Sequence[uuid.UUID], *, as_of: date
    ) -> Sequence[HoldingDisclosure]: ...


class LedgerChinaMarketData:
    """Point-in-time market-data reads backed solely by ledger facts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def operating_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        return self._metric_snapshots(stock.id, OPERATING_METRICS, as_of)

    def market_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        return self._metric_snapshots(stock.id, MARKET_METRICS, as_of)

    def peer_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        return self._metric_snapshots(stock.id, PEER_METRICS, as_of)

    def fund_holdings(
        self, stock_ids: Sequence[uuid.UUID], *, as_of: date
    ) -> list[HoldingDisclosure]:
        """Visible latest report per ``(fund, stock)`` at the requested date."""
        if not stock_ids:
            return []
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        rows = self._session.scalars(
            select(HoldingDisclosure)
            .where(HoldingDisclosure.stock_id.in_(stock_ids))
            .where(HoldingDisclosure.published_at <= cutoff)
            .order_by(
                HoldingDisclosure.report_period.desc(),
                HoldingDisclosure.published_at.desc(),
            )
        )
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for row in rows:
            latest.setdefault((row.fund_id, row.stock_id), row)
        return list(latest.values())

    def _metric_snapshots(
        self,
        stock_id: uuid.UUID,
        metric_names: frozenset[str],
        as_of: date,
    ) -> list[ValuationSnapshot]:
        """Latest pre-cutoff snapshot per requested metric."""
        rows = self._session.scalars(
            select(ValuationSnapshot)
            .where(ValuationSnapshot.stock_id == stock_id)
            .where(ValuationSnapshot.metric_name.in_(metric_names))
            .where(ValuationSnapshot.as_of_date <= as_of)
            .order_by(ValuationSnapshot.as_of_date.desc())
        )
        latest: dict[str, ValuationSnapshot] = {}
        for row in rows:
            latest.setdefault(row.metric_name, row)
        return list(latest.values())
