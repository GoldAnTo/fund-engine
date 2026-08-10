"""Read model for the Case-scoped fund disclosure task."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fund_disclosure_sync import (
    FundDisclosureSyncConfigVersion,
    FundDisclosureSyncRun,
    FundDisclosureSyncRunEvent,
)
from app.models.ledger import Fund, HoldingDisclosure, Stock
from app.models.research_expression import MarketInstrumentBinding


@dataclass(frozen=True)
class FundDisclosureSuggestion:
    fund_code: str
    fund_name: str
    matching_stock_codes: list[str]
    latest_report_period: date


@dataclass(frozen=True)
class FundDisclosureSyncRunView:
    run: FundDisclosureSyncRun
    events: list[FundDisclosureSyncRunEvent]


@dataclass(frozen=True)
class FundDisclosureSyncDetail:
    suggestions: list[FundDisclosureSuggestion]
    manual_code_fallback: bool
    effective_config: FundDisclosureSyncConfigVersion | None
    config_history: list[FundDisclosureSyncConfigVersion]
    runs: list[FundDisclosureSyncRunView]


class FundDisclosureSyncQuery:
    def __init__(self, session: Session) -> None:
        self._session = session

    def detail(self, case_id: uuid.UUID) -> FundDisclosureSyncDetail:
        configs = list(
            self._session.scalars(
                select(FundDisclosureSyncConfigVersion)
                .where(FundDisclosureSyncConfigVersion.research_case_id == case_id)
                .order_by(FundDisclosureSyncConfigVersion.version.desc())
            )
        )
        runs = list(
            self._session.scalars(
                select(FundDisclosureSyncRun)
                .where(FundDisclosureSyncRun.research_case_id == case_id)
                .order_by(FundDisclosureSyncRun.created_at.desc(), FundDisclosureSyncRun.id.desc())
            )
        )
        return FundDisclosureSyncDetail(
            suggestions=self._suggestions(case_id),
            manual_code_fallback=not bool(self._suggestions(case_id)),
            effective_config=configs[0] if configs else None,
            config_history=configs,
            runs=[
                FundDisclosureSyncRunView(
                    run=run,
                    events=list(
                        self._session.scalars(
                            select(FundDisclosureSyncRunEvent)
                            .where(FundDisclosureSyncRunEvent.run_id == run.id)
                            .order_by(FundDisclosureSyncRunEvent.seq)
                        )
                    ),
                )
                for run in runs
            ],
        )

    def _suggestions(self, case_id: uuid.UUID) -> list[FundDisclosureSuggestion]:
        stock_ids = list(
            self._session.scalars(
                select(MarketInstrumentBinding.stock_id)
                .where(MarketInstrumentBinding.research_case_id == case_id)
                .where(MarketInstrumentBinding.review_state == "reviewed")
                .where(MarketInstrumentBinding.stock_id.is_not(None))
                .distinct()
            )
        )
        if not stock_ids:
            return []
        rows = self._session.execute(
            select(Fund, Stock, HoldingDisclosure)
            .join(HoldingDisclosure, HoldingDisclosure.fund_id == Fund.id)
            .join(Stock, Stock.id == HoldingDisclosure.stock_id)
            .where(HoldingDisclosure.stock_id.in_(stock_ids))
            .order_by(Fund.code, HoldingDisclosure.report_period.desc(), Stock.code)
        ).all()
        grouped: dict[uuid.UUID, tuple[Fund, set[str], date]] = {}
        for fund, stock, disclosure in rows:
            existing = grouped.get(fund.id)
            if existing is None:
                grouped[fund.id] = (fund, {stock.code}, disclosure.report_period)
                continue
            current_fund, stock_codes, latest_period = existing
            stock_codes.add(stock.code)
            grouped[fund.id] = (current_fund, stock_codes, max(latest_period, disclosure.report_period))
        return [
            FundDisclosureSuggestion(
                fund_code=fund.code,
                fund_name=fund.name,
                matching_stock_codes=sorted(stock_codes),
                latest_report_period=latest_period,
            )
            for fund, stock_codes, latest_period in grouped.values()
        ]
