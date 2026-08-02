"""Domain service for instrument write paths (funds, holding disclosures, theme roles).

Owns domain validation; persistence delegates to ``InstrumentRepository``
(which sets timestamps and flushes). HTTP existence checks stay in the
command route layer. Raises ``app.models.ledger.ValidationError`` for domain
violations, which the route layer translates to 422.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import (
    Company,
    Fund,
    FundCompany,
    HoldingDisclosure,
    ResearchCase,
    SourceStatement,
    Stock,
    ThemeRole,
    ValidationError,
)
from app.repositories.instruments import InstrumentRepository


def _require_non_empty(value: str, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field} 不能为空")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field} 长度不能超过 {max_length} 字符")
    return cleaned


class InstrumentService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._instruments = InstrumentRepository(session)

    def create_company(self, *, code: str, name: str, type: str) -> Company:
        code = _require_non_empty(code, "code", 64)
        name = _require_non_empty(name, "name", 255)
        type = _require_non_empty(type, "type", 64)

        existing = self._session.scalar(
            select(func.count()).select_from(Company).where(Company.code == code)
        )
        if existing:
            raise ValidationError(f"公司代码 {code} 已存在")

        return self._instruments.add_company(code=code, name=name, type=type)

    def add_stock(
        self,
        *,
        company: Company,
        code: str,
        name: str,
        market: str,
    ) -> Stock:
        code = _require_non_empty(code, "code", 64)
        name = _require_non_empty(name, "name", 255)
        market = _require_non_empty(market, "market", 64)

        existing = self._session.scalar(
            select(func.count()).select_from(Stock).where(Stock.code == code)
        )
        if existing:
            raise ValidationError(f"股票代码 {code} 已存在")

        return self._instruments.add_stock(
            company_id=company.id, code=code, name=name, market=market
        )

    def create_fund(
        self,
        *,
        code: str,
        name: str,
        fund_type: str,
        scale: Decimal | None = None,
        establish_date: date | None = None,
        management_company_id: uuid.UUID | None = None,
    ) -> Fund:
        code = _require_non_empty(code, "code", 32)
        name = _require_non_empty(name, "name", 255)
        fund_type = _require_non_empty(fund_type, "fund_type", 64)

        if management_company_id is not None:
            if self._session.get(FundCompany, management_company_id) is None:
                raise ValidationError("management_company_id 对应的基金公司不存在")

        existing = self._session.scalar(
            select(func.count()).select_from(Fund).where(Fund.code == code)
        )
        if existing:
            raise ValidationError(f"基金代码 {code} 已存在")

        return self._instruments.add_fund(
            code=code,
            name=name,
            fund_type=fund_type,
            management_company_id=management_company_id,
            scale=scale,
            establish_date=establish_date,
        )

    def add_holding_disclosure(
        self,
        *,
        fund: Fund,
        stock: Stock,
        weight: Decimal,
        report_period: date,
        published_at: datetime,
        source: str,
    ) -> HoldingDisclosure:
        if weight <= 0 or weight > Decimal("100"):
            raise ValidationError("weight 必须在 (0, 100] 区间内")
        if published_at.date() < report_period:
            raise ValidationError("published_at 不能早于 report_period")
        source = _require_non_empty(source, "source", 128)

        existing = self._session.scalar(
            select(func.count())
            .select_from(HoldingDisclosure)
            .where(
                HoldingDisclosure.fund_id == fund.id,
                HoldingDisclosure.stock_id == stock.id,
                HoldingDisclosure.report_period == report_period,
                HoldingDisclosure.source == source,
            )
        )
        if existing:
            raise ValidationError("该基金在该报告期对该股票的同一来源披露已存在")

        return self._instruments.add_holding_disclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=weight,
            report_period=report_period,
            published_at=published_at,
            source=source,
        )

    def add_theme_role(
        self,
        *,
        company: Company,
        role: str,
        research_case: ResearchCase | None = None,
        scope: dict[str, Any] | None = None,
        applicable_from: date | None = None,
        applicable_to: date | None = None,
        source_statement: SourceStatement | None = None,
    ) -> ThemeRole:
        role = _require_non_empty(role, "role", 64)
        if (
            applicable_from is not None
            and applicable_to is not None
            and applicable_from > applicable_to
        ):
            raise ValidationError("applicable_from 不能晚于 applicable_to")

        return self._instruments.add_theme_role(
            company_id=company.id,
            role=role,
            scope=scope if scope is not None else {},
            research_case_id=research_case.id if research_case is not None else None,
            applicable_from=applicable_from,
            applicable_to=applicable_to,
            source_statement_id=(
                source_statement.id if source_statement is not None else None
            ),
        )
