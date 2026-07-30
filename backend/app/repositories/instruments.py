from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.ledger import (
    Company,
    Fund,
    FundCompany,
    HoldingDisclosure,
    Stock,
    ThemeRole,
    ValuationSnapshot,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware_datetime(value: date | datetime) -> datetime:
    """Coerce a date or naive datetime into a timezone-aware datetime (UTC)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


class InstrumentRepository:
    """Append-only persistence for companies, stocks, funds, holdings, and roles.

    No update or delete methods are exposed by design.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ writers

    def add_company(
        self,
        *,
        code: str,
        name: str,
        type: str,
    ) -> Company:
        company = Company(
            code=code,
            name=name,
            type=type,
            created_at=_utcnow(),
        )
        self._session.add(company)
        self._session.flush()
        return company

    def add_stock(
        self,
        *,
        company_id: uuid.UUID,
        code: str,
        name: str,
        market: str,
    ) -> Stock:
        stock = Stock(
            company_id=company_id,
            code=code,
            name=name,
            market=market,
            created_at=_utcnow(),
        )
        self._session.add(stock)
        self._session.flush()
        return stock

    def add_fund_company(
        self,
        *,
        code: str,
        name: str,
    ) -> FundCompany:
        fund_company = FundCompany(
            code=code,
            name=name,
            created_at=_utcnow(),
        )
        self._session.add(fund_company)
        self._session.flush()
        return fund_company

    def add_fund(
        self,
        *,
        code: str,
        name: str,
        fund_type: str,
        management_company_id: uuid.UUID | None = None,
        scale: Decimal | None = None,
        establish_date: date | None = None,
    ) -> Fund:
        fund = Fund(
            code=code,
            name=name,
            fund_type=fund_type,
            management_company_id=management_company_id,
            scale=scale,
            establish_date=establish_date,
            created_at=_utcnow(),
        )
        self._session.add(fund)
        self._session.flush()
        return fund

    def add_valuation_snapshot(
        self,
        *,
        stock_id: uuid.UUID,
        as_of_date: date,
        metric_name: str,
        metric_value: Decimal,
        source: str,
        definition: str,
    ) -> ValuationSnapshot:
        snapshot = ValuationSnapshot(
            stock_id=stock_id,
            as_of_date=as_of_date,
            metric_name=metric_name,
            metric_value=metric_value,
            source=source,
            definition=definition,
            created_at=_utcnow(),
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def add_holding_disclosure(
        self,
        *,
        fund_id: uuid.UUID,
        stock_id: uuid.UUID,
        weight: Decimal,
        report_period: date,
        published_at: date | datetime,
        source: str,
        acquired_at: datetime | None = None,
    ) -> HoldingDisclosure:
        disclosure = HoldingDisclosure(
            fund_id=fund_id,
            stock_id=stock_id,
            weight=weight,
            report_period=report_period,
            published_at=_to_aware_datetime(published_at),
            acquired_at=_to_aware_datetime(acquired_at) if acquired_at is not None else _utcnow(),
            source=source,
            created_at=_utcnow(),
        )
        self._session.add(disclosure)
        self._session.flush()
        return disclosure

    def add_theme_role(
        self,
        *,
        company_id: uuid.UUID,
        role: str,
        scope: dict,
        research_case_id: uuid.UUID | None = None,
        applicable_from: date | None = None,
        applicable_to: date | None = None,
        source_statement_id: uuid.UUID | None = None,
    ) -> ThemeRole:
        theme_role = ThemeRole(
            company_id=company_id,
            research_case_id=research_case_id,
            role=role,
            scope=scope,
            applicable_from=applicable_from,
            applicable_to=applicable_to,
            source_statement_id=source_statement_id,
            created_at=_utcnow(),
        )
        self._session.add(theme_role)
        self._session.flush()
        return theme_role

    # ------------------------------------------------------------------ readers

    def disclosures_visible_on_or_before(
        self,
        fund_id: uuid.UUID,
        as_of: date,
    ) -> list[HoldingDisclosure]:
        """Return disclosures whose ``published_at`` falls on or before *as_of*.

        Visibility is governed by the disclosure date (``published_at``), not the
        holding report period.  Results are ordered by ``report_period`` descending
        so callers can pick the latest report per stock.
        """
        cutoff = datetime.combine(as_of, time(23, 59, 59, 999999), tzinfo=timezone.utc)
        return list(
            self._session.scalars(
                select(HoldingDisclosure)
                .where(HoldingDisclosure.fund_id == fund_id)
                .where(HoldingDisclosure.published_at <= cutoff)
                .order_by(HoldingDisclosure.report_period.desc())
            )
        )

    def stock_has_theme_role(
        self,
        stock_id: uuid.UUID,
        as_of: date,
    ) -> bool:
        """True when the stock's company has an active ThemeRole at *as_of*."""
        role = self._session.scalar(
            select(ThemeRole)
            .join(Stock, Stock.company_id == ThemeRole.company_id)
            .where(Stock.id == stock_id)
            .where(
                or_(
                    ThemeRole.applicable_from.is_(None),
                    ThemeRole.applicable_from <= as_of,
                )
            )
            .where(
                or_(
                    ThemeRole.applicable_to.is_(None),
                    ThemeRole.applicable_to >= as_of,
                )
            )
            .limit(1)
        )
        return role is not None

    def latest_valuation(
        self,
        stock_id: uuid.UUID,
        as_of: date,
    ) -> ValuationSnapshot | None:
        return self._session.scalar(
            select(ValuationSnapshot)
            .where(ValuationSnapshot.stock_id == stock_id)
            .where(ValuationSnapshot.as_of_date <= as_of)
            .order_by(ValuationSnapshot.as_of_date.desc())
            .limit(1)
        )

    # ------------------------------------------------------------------ readers (workbench / projection)

    def theme_roles_for_case(
        self, research_case_id: uuid.UUID
    ) -> list[ThemeRole]:
        return list(
            self._session.scalars(
                select(ThemeRole).where(
                    ThemeRole.research_case_id == research_case_id
                )
            )
        )

    def companies_by_ids(
        self, company_ids: list[uuid.UUID]
    ) -> list[Company]:
        if not company_ids:
            return []
        return list(
            self._session.scalars(
                select(Company).where(Company.id.in_(company_ids))
            )
        )

    def stocks_for_companies(
        self, company_ids: list[uuid.UUID]
    ) -> list[Stock]:
        if not company_ids:
            return []
        return list(
            self._session.scalars(
                select(Stock).where(Stock.company_id.in_(company_ids))
            )
        )

    def stock_by_id(self, stock_id: uuid.UUID) -> Stock | None:
        return self._session.get(Stock, stock_id)

    def fund_by_id(self, fund_id: uuid.UUID) -> Fund | None:
        return self._session.get(Fund, fund_id)

    def valuation_snapshots_for_stocks(
        self, stock_ids: list[uuid.UUID]
    ) -> list[ValuationSnapshot]:
        if not stock_ids:
            return []
        return list(
            self._session.scalars(
                select(ValuationSnapshot)
                .where(ValuationSnapshot.stock_id.in_(stock_ids))
                .order_by(ValuationSnapshot.as_of_date)
            )
        )

    def holding_disclosures_for_stocks(
        self, stock_ids: list[uuid.UUID]
    ) -> list[HoldingDisclosure]:
        if not stock_ids:
            return []
        return list(
            self._session.scalars(
                select(HoldingDisclosure)
                .where(HoldingDisclosure.stock_id.in_(stock_ids))
                .order_by(HoldingDisclosure.report_period.desc())
            )
        )

    def funds_by_ids(self, fund_ids: list[uuid.UUID]) -> list[Fund]:
        if not fund_ids:
            return []
        return list(
            self._session.scalars(
                select(Fund).where(Fund.id.in_(fund_ids))
            )
        )

    # ------------------------------------------------------------------ readers (projection)

    def all_companies(self) -> list[Company]:
        return list(self._session.scalars(select(Company)))

    def all_stocks(self) -> list[Stock]:
        return list(self._session.scalars(select(Stock)))

    def all_funds(self) -> list[Fund]:
        return list(self._session.scalars(select(Fund)))

    def all_fund_companies(self) -> list[FundCompany]:
        return list(self._session.scalars(select(FundCompany)))

    def all_valuation_snapshots(self) -> list[ValuationSnapshot]:
        return list(self._session.scalars(select(ValuationSnapshot)))

    def all_holding_disclosures(self) -> list[HoldingDisclosure]:
        return list(self._session.scalars(select(HoldingDisclosure)))

    def all_theme_roles(self) -> list[ThemeRole]:
        return list(self._session.scalars(select(ThemeRole)))
