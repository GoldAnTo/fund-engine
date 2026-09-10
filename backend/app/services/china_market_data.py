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
from re import fullmatch
from typing import Protocol, Sequence

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.models.ledger import Fund, HoldingDisclosure, Stock, ValuationSnapshot


OPERATING_METRICS = frozenset({"REVENUE_YOY", "GROSS_MARGIN", "ORDER_GUIDANCE"})
MARKET_METRICS = frozenset({"EVENT_RETURN_1D", "TURNOVER_RATE", "PE_TTM"})
PEER_METRICS = frozenset({"PEER_RETURN_1D", "INDUSTRY_RETURN_1D", "PEER_PE_TTM"})
# Report market studies deliberately use an exact observation date rather than
# the gateway's normal latest-pre-cutoff reads.  A later snapshot cannot fill
# an absent 1d/5d window without silently changing the event study.
REPORT_TARGET_METRICS_1D = frozenset(
    {"EVENT_RETURN_1D", "VOLUME", "TURNOVER_RATE", "VOLATILITY", "PE_TTM"}
)
REPORT_TARGET_METRICS_5D = frozenset(
    {"EVENT_RETURN_5D", "VOLUME", "TURNOVER_RATE", "VOLATILITY", "PE_TTM"}
)
REPORT_PEER_METRICS_1D = frozenset({"PEER_RETURN_1D"})
REPORT_PEER_METRICS_5D = frozenset({"PEER_RETURN_5D"})
# These are the exact market labels written by the two in-repository China
# ingest paths: Gildata uses SSE/SZSE/BSE and AKShare uses SH/SZ.  Keeping
# this explicit rejects KRX, HK, US, and unknown labels instead of inferring
# an asset's jurisdiction from its display name or ticker suffix.
CHINA_A_SHARE_MARKETS = frozenset({"SSE", "SZSE", "BSE", "SH", "SZ"})


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

    def fund_holding_history(
        self, stock_ids: Sequence[uuid.UUID], *, as_of: date
    ) -> Sequence[HoldingDisclosure]: ...

    def trading_days_after(self, *, after: date, count: int) -> Sequence[date]: ...

    def report_target_observations(
        self, stock: Stock, *, as_of: date, window: str
    ) -> Sequence[ValuationSnapshot]: ...

    def report_peer_observations(
        self, stock: Stock, *, as_of: date, window: str
    ) -> Sequence[ValuationSnapshot]: ...

def is_china_a_share(stock: Stock) -> bool:
    return stock.market.upper() in CHINA_A_SHARE_MARKETS


def is_china_public_fund(fund_code: str) -> bool:
    """The current ledger's China-fund identifier is the AKShare six-digit code.

    ``Fund`` has no country column.  Until the ledger gains one, accepting
    only the source format produced by the bundled China fund adapter is the
    conservative boundary; all other codes are excluded rather than guessed.
    """
    return fullmatch(r"[0-9]{6}", fund_code or "") is not None


class LedgerChinaMarketData:
    """Point-in-time market-data reads backed solely by ledger facts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def operating_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        if not is_china_a_share(stock):
            return []
        return self._metric_snapshots(stock.id, OPERATING_METRICS, as_of)

    def market_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        if not is_china_a_share(stock):
            return []
        return self._metric_snapshots(stock.id, MARKET_METRICS, as_of)

    def peer_observations(
        self, stock: Stock, *, as_of: date
    ) -> list[ValuationSnapshot]:
        if not is_china_a_share(stock):
            return []
        return self._metric_snapshots(stock.id, PEER_METRICS, as_of)

    def fund_holdings(
        self, stock_ids: Sequence[uuid.UUID], *, as_of: date
    ) -> list[HoldingDisclosure]:
        """Visible latest report per ``(fund, stock)`` at the requested date."""
        rows = self.fund_holding_history(stock_ids, as_of=as_of)
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for row in rows:
            latest.setdefault((row.fund_id, row.stock_id), row)
        fund_ids = {row.fund_id for row in latest.values()}
        if not fund_ids:
            return []
        chinese_fund_ids = {
            fund_id
            for fund_id, code in self._session.execute(
                select(Fund.id, Fund.code).where(Fund.id.in_(fund_ids))
            )
            if is_china_public_fund(code)
        }
        return [row for row in latest.values() if row.fund_id in chinese_fund_ids]

    def fund_holding_history(
        self, stock_ids: Sequence[uuid.UUID], *, as_of: date
    ) -> list[HoldingDisclosure]:
        """Return all China A-share disclosures visible by ``as_of``.

        Consumers that must evaluate several cutoffs can derive each
        latest-per-fund/stock view in memory from this one bounded ledger read.
        """
        if not stock_ids:
            return []
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        rows = self._session.scalars(
            select(HoldingDisclosure)
            .join(Stock, Stock.id == HoldingDisclosure.stock_id)
            .where(HoldingDisclosure.stock_id.in_(stock_ids))
            .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            .where(HoldingDisclosure.published_at <= cutoff)
            .where(HoldingDisclosure.acquired_at <= cutoff)
            .order_by(
                HoldingDisclosure.report_period.desc(),
                HoldingDisclosure.published_at.desc(),
            )
        )
        return list(rows)

    def trading_days_after(self, *, after: date, count: int) -> list[date]:
        """Return ledger-observed China trading dates strictly after ``after``.

        There is intentionally no weekday heuristic here.  A weekday with no
        ledger-backed China observation is not declared a trading day, which
        makes an incomplete calendar an explicit collection gap.
        """
        if count <= 0:
            return []
        rows = self._session.execute(
            select(distinct(ValuationSnapshot.as_of_date))
            .join(Stock, Stock.id == ValuationSnapshot.stock_id)
            .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            .where(ValuationSnapshot.as_of_date > after)
            .order_by(ValuationSnapshot.as_of_date.asc())
            .limit(count)
        )
        return [value for (value,) in rows]

    def report_target_observations(
        self, stock: Stock, *, as_of: date, window: str
    ) -> list[ValuationSnapshot]:
        return self._exact_metric_snapshots(
            stock,
            REPORT_TARGET_METRICS_1D if window == "1d" else REPORT_TARGET_METRICS_5D,
            as_of,
        )

    def report_peer_observations(
        self, stock: Stock, *, as_of: date, window: str
    ) -> list[ValuationSnapshot]:
        return self._exact_metric_snapshots(
            stock,
            REPORT_PEER_METRICS_1D if window == "1d" else REPORT_PEER_METRICS_5D,
            as_of,
        )

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

    def _exact_metric_snapshots(
        self,
        stock: Stock,
        metric_names: frozenset[str],
        as_of: date,
    ) -> list[ValuationSnapshot]:
        if not is_china_a_share(stock):
            return []
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        return list(
            self._session.scalars(
                select(ValuationSnapshot)
                .where(ValuationSnapshot.stock_id == stock.id)
                .where(ValuationSnapshot.as_of_date == as_of)
                .where(ValuationSnapshot.metric_name.in_(metric_names))
                .where(ValuationSnapshot.available_at.is_not(None))
                .where(ValuationSnapshot.available_at <= cutoff)
                .order_by(ValuationSnapshot.metric_name)
            )
        )
