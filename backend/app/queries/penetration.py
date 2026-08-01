"""Theme↔fund penetration read models (prototype 穿透链路).

正向穿透: a case's theme stocks → funds holding them, ranked by summed
weight (主题暴露度).  反向穿透: a fund → what it actually holds and which
research cases each position hits (「基金是壳子」).

Disclosure visibility is governed by ``published_at <= as_of`` (never the
report period), and only the latest report per (fund, stock) counts, so
superseded disclosures never double-count.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import HoldingDisclosure, Stock, ValuationSnapshot
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.penetration import (
    CompositionPositionDTO,
    ExposurePositionDTO,
    FundCompositionResponse,
    FundExposureDTO,
    FundExposureResponse,
    ThemeHitDTO,
)


class PenetrationQueries:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._instruments = InstrumentRepository(db)
        self._research = ResearchRepository(db)

    # ---------------------------------------------------------- 正向穿透

    def case_fund_exposure(
        self, *, case_id: uuid.UUID, as_of: date
    ) -> FundExposureResponse:
        if self._research.get_case(case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")

        roles = self._instruments.theme_roles_for_case(case_id)
        stocks = self._instruments.stocks_for_companies(
            [role.company_id for role in roles]
        )
        stock_by_id = {stock.id: stock for stock in stocks}
        valuations = self._latest_valuations(list(stock_by_id), as_of)

        latest = self._latest_disclosures(list(stock_by_id), as_of)
        by_fund: dict[uuid.UUID, list[HoldingDisclosure]] = {}
        for disclosure in latest:
            by_fund.setdefault(disclosure.fund_id, []).append(disclosure)

        funds = {
            fund.id: fund
            for fund in self._instruments.funds_by_ids(list(by_fund))
        }
        fund_dtos: list[FundExposureDTO] = []
        for fund_id, disclosures in by_fund.items():
            fund = funds.get(fund_id)
            if fund is None:
                continue
            positions = [
                self._exposure_position(d, stock_by_id, valuations)
                for d in sorted(disclosures, key=lambda x: x.weight, reverse=True)
            ]
            fund_dtos.append(
                FundExposureDTO(
                    fund_id=str(fund.id),
                    fund_code=fund.code,
                    fund_name=fund.name,
                    theme_exposure=round(sum(p.weight for p in positions), 6),
                    positions=positions,
                )
            )
        fund_dtos.sort(key=lambda f: f.theme_exposure, reverse=True)
        return FundExposureResponse(
            case_id=str(case_id), as_of=as_of.isoformat(), funds=fund_dtos
        )

    # ---------------------------------------------------------- 反向穿透

    def fund_composition(
        self, *, fund_id: uuid.UUID, as_of: date
    ) -> FundCompositionResponse:
        fund = self._instruments.fund_by_id(fund_id)
        if fund is None:
            raise NotFoundError(f"fund {fund_id} not found")

        disclosures = self._instruments.disclosures_visible_on_or_before(
            fund_id, as_of
        )
        latest_by_stock: dict[uuid.UUID, HoldingDisclosure] = {}
        for disclosure in disclosures:  # ordered by report_period desc
            latest_by_stock.setdefault(disclosure.stock_id, disclosure)

        stock_ids = list(latest_by_stock)
        stocks = {
            s.id: s
            for s in (self._instruments.stock_by_id(i) for i in stock_ids)
            if s is not None
        }
        valuations = self._latest_valuations(stock_ids, as_of)
        theme_hits = self._theme_hits_by_stock(stocks)

        positions = [
            CompositionPositionDTO(
                stock_id=str(stock.id),
                stock_code=stock.code,
                stock_name=stock.name,
                weight=float(disclosure.weight),
                report_period=disclosure.report_period.isoformat(),
                pe_ttm=valuations.get((stock.id, "PE_TTM")),
                pb=valuations.get((stock.id, "PB")),
                theme_hits=theme_hits.get(stock.id, []),
            )
            for stock_id, disclosure in latest_by_stock.items()
            if (stock := stocks.get(stock_id)) is not None
        ]
        positions.sort(key=lambda p: p.weight, reverse=True)
        return FundCompositionResponse(
            fund_id=str(fund.id),
            fund_code=fund.code,
            fund_name=fund.name,
            as_of=as_of.isoformat(),
            positions=positions,
        )

    # ------------------------------------------------------------- helpers

    def _latest_disclosures(
        self, stock_ids: list[uuid.UUID], as_of: date
    ) -> list[HoldingDisclosure]:
        """Latest report per (fund, stock), honouring published_at visibility."""
        from datetime import time, timezone
        from datetime import datetime as dt

        cutoff = dt.combine(
            as_of, time(23, 59, 59, 999999), tzinfo=timezone.utc
        )
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for d in self._instruments.holding_disclosures_for_stocks(stock_ids):
            if d.published_at is None:
                continue
            pub = (
                d.published_at.replace(tzinfo=timezone.utc)
                if d.published_at.tzinfo is None
                else d.published_at
            )
            if pub > cutoff:
                continue
            latest.setdefault((d.fund_id, d.stock_id), d)
        return list(latest.values())

    def _latest_valuations(
        self, stock_ids: list[uuid.UUID], as_of: date
    ) -> dict[tuple[uuid.UUID, str], float]:
        latest: dict[tuple[uuid.UUID, str], ValuationSnapshot] = {}
        for snap in self._instruments.valuation_snapshots_for_stocks(stock_ids):
            if snap.as_of_date > as_of:
                continue
            latest[(snap.stock_id, snap.metric_name)] = snap
        return {key: float(s.metric_value) for key, s in latest.items()}

    def _theme_hits_by_stock(
        self, stocks: dict[uuid.UUID, Stock]
    ) -> dict[uuid.UUID, list[ThemeHitDTO]]:
        hits: dict[uuid.UUID, list[ThemeHitDTO]] = {}
        case_ids = {
            role.research_case_id
            for role in self._instruments.all_theme_roles()
        }
        cases = {
            case.id
            for case in (
                self._research.get_case(cid) for cid in case_ids
            )
            if case is not None
        }
        for role in self._instruments.all_theme_roles():
            if role.research_case_id not in cases:
                continue
            for stock_id, stock in stocks.items():
                if stock.company_id == role.company_id:
                    hits.setdefault(stock_id, []).append(
                        ThemeHitDTO(
                            case_id=str(role.research_case_id), role=role.role
                        )
                    )
        return hits

    def _exposure_position(
        self,
        disclosure: HoldingDisclosure,
        stock_by_id: dict[uuid.UUID, Stock],
        valuations: dict[tuple[uuid.UUID, str], float],
    ) -> ExposurePositionDTO:
        stock = stock_by_id[disclosure.stock_id]
        return ExposurePositionDTO(
            stock_id=str(stock.id),
            stock_code=stock.code,
            stock_name=stock.name,
            weight=float(disclosure.weight),
            report_period=disclosure.report_period.isoformat(),
            pe_ttm=valuations.get((stock.id, "PE_TTM")),
            pb=valuations.get((stock.id, "PB")),
        )
