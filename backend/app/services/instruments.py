"""Domain service for instrument write paths (funds, holding disclosures, theme roles).

Owns domain validation; persistence delegates to ``InstrumentRepository``
(which sets timestamps and flushes). HTTP existence checks stay in the
command route layer. Raises ``app.models.ledger.ValidationError`` for domain
violations (translated to 422) and ``app.models.ledger.ConflictError`` for
uniqueness collisions (translated to 409).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import (
    Company,
    ConflictError,
    DocumentVersion,
    Fund,
    FundCompany,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
    ValidationError,
    ValuationSnapshot,
)
from app.models.source_governance import ProviderRecord, SourceContract
from app.repositories.instruments import InstrumentRepository


def _require_non_empty(value: str, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field} 不能为空")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field} 长度不能超过 {max_length} 字符")
    return cleaned


def _today_utc() -> date:
    """Server-clock today (UTC).

    Valuation snapshots are factual market observations, so as-of dates in
    the future cannot be sourced honestly. Anchoring to UTC keeps the cut-off
    deterministic across timezones.
    """
    return datetime.now(timezone.utc).date()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


_FILING_KIND_PRECEDENCE = {
    "other": 0,
    "quarterly": 1,
    "annual": 2,
    "correction": 3,
}


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
            raise ConflictError(f"公司代码 {code} 已存在")

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
            raise ConflictError(f"股票代码 {code} 已存在")

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
            raise ConflictError(f"基金代码 {code} 已存在")

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
        source_document_version_id: uuid.UUID | None = None,
        source_span_id: uuid.UUID | None = None,
        provider_record_id: uuid.UUID | None = None,
        coverage_status: str = "not_recorded",
        filing_kind: str = "other",
        supersedes_disclosure_id: uuid.UUID | None = None,
    ) -> HoldingDisclosure:
        if weight <= 0 or weight > Decimal("100"):
            raise ValidationError("weight 必须在 (0, 100] 区间内")
        if published_at.date() < report_period:
            raise ValidationError("published_at 不能早于 report_period")
        if report_period > _today_utc():
            # Future-dated quarterly reports are not yet filed; the
            # 报告期 itself is the natural-key anchor of the disclosure,
            # so a future report_period is a 422 (malformed request), not
            # a 409. Keeping 422 here is intentional.
            raise ValidationError("report_period 不能晚于今天")
        source = _require_non_empty(source, "source", 128)
        if coverage_status not in {"complete", "partial", "not_recorded"}:
            raise ValidationError("coverage_status 必须为 complete、partial 或 not_recorded")
        if filing_kind not in _FILING_KIND_PRECEDENCE:
            raise ValidationError("filing_kind 必须为 quarterly、annual、correction 或 other")
        document = self._session.get(DocumentVersion, source_document_version_id) if source_document_version_id else None
        span = self._session.get(SourceSpan, source_span_id) if source_span_id else None
        provider = self._session.get(ProviderRecord, provider_record_id) if provider_record_id else None
        if source_document_version_id and document is None:
            raise ValidationError("source_document_version_id 不存在")
        if source_span_id and span is None:
            raise ValidationError("source_span_id 不存在")
        if provider_record_id and provider is None:
            raise ValidationError("provider_record_id 不存在")
        if span is not None:
            if document is None:
                document = self._session.get(DocumentVersion, span.document_version_id)
                source_document_version_id = span.document_version_id
            elif span.document_version_id != document.id:
                raise ValidationError("source_span_id 必须属于 source_document_version_id")
        if provider is not None:
            if document is None:
                document = self._session.get(DocumentVersion, provider.document_version_id)
                source_document_version_id = provider.document_version_id
            elif provider.document_version_id != document.id:
                raise ValidationError("provider_record_id 必须属于 source_document_version_id")
        if coverage_status != "not_recorded" and document is None:
            raise ValidationError("complete 或 partial 披露必须关联冻结来源版本")
        if document is not None:
            contract = self._session.scalar(select(SourceContract).where(SourceContract.document_version_id == document.id))
            if contract is None or not contract.allow_display:
                raise ValidationError("持仓来源版本必须具有可展示的来源许可")

        existing_query = (
            select(func.count())
            .select_from(HoldingDisclosure)
            .where(
                HoldingDisclosure.fund_id == fund.id,
                HoldingDisclosure.stock_id == stock.id,
                HoldingDisclosure.report_period == report_period,
                HoldingDisclosure.source == source,
            )
        )
        if source_document_version_id is None:
            existing_query = existing_query.where(
                HoldingDisclosure.source_document_version_id.is_(None)
            )
        else:
            existing_query = existing_query.where(
                HoldingDisclosure.source_document_version_id == source_document_version_id
            )
        existing = self._session.scalar(existing_query)
        if existing:
            raise ConflictError("该基金在该报告期对该股票的同一来源披露已存在")

        if supersedes_disclosure_id is not None:
            predecessor = self._session.get(HoldingDisclosure, supersedes_disclosure_id)
            if predecessor is None:
                raise ValidationError("supersedes_disclosure_id 不存在")
            if (
                predecessor.fund_id != fund.id
                or predecessor.stock_id != stock.id
                or predecessor.report_period != report_period
            ):
                raise ValidationError("前序披露必须属于同一基金、股票和报告期")
            predecessor_priority = _FILING_KIND_PRECEDENCE[predecessor.filing_kind]
            filing_priority = _FILING_KIND_PRECEDENCE[filing_kind]
            if predecessor_priority > filing_priority:
                raise ValidationError("新披露必须高于前序披露的文件优先级")
            if predecessor_priority == filing_priority and _as_utc(predecessor.published_at) >= _as_utc(published_at):
                raise ValidationError("同优先级披露必须晚于前序披露的发布时间")

        return self._instruments.add_holding_disclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=weight,
            report_period=report_period,
            published_at=published_at,
            source=source,
            source_document_version_id=source_document_version_id,
            source_span_id=source_span_id,
            provider_record_id=provider_record_id,
            coverage_status=coverage_status,
            filing_kind=filing_kind,
            supersedes_disclosure_id=supersedes_disclosure_id,
        )

    def add_valuation_snapshot(
        self,
        *,
        stock: Stock,
        as_of_date: date,
        metric_name: str,
        metric_value: Decimal,
        source: str,
        definition: str,
    ) -> ValuationSnapshot:
        metric_name = _require_non_empty(metric_name, "metric_name", 64)
        source = _require_non_empty(source, "source", 128)
        definition = _require_non_empty(definition, "definition", 255)
        if not metric_value.is_finite():
            raise ValidationError("metric_value 必须是有限数值")
        # Timepoint consistency: a valuation snapshot is an observation tied
        # to a trading day. A future-dated as_of cannot be sourced honestly,
        # so reject before uniqueness check (422, malformed request).
        if as_of_date > _today_utc():
            raise ValidationError("as_of_date 不能晚于今天")

        existing = self._session.scalar(
            select(func.count())
            .select_from(ValuationSnapshot)
            .where(
                ValuationSnapshot.stock_id == stock.id,
                ValuationSnapshot.metric_name == metric_name,
                ValuationSnapshot.as_of_date == as_of_date,
                ValuationSnapshot.source == source,
            )
        )
        if existing:
            raise ConflictError("该股票在该日期该指标的同一来源估值快照已存在")

        return self._instruments.add_valuation_snapshot(
            stock_id=stock.id,
            as_of_date=as_of_date,
            metric_name=metric_name,
            metric_value=metric_value,
            source=source,
            definition=definition,
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
