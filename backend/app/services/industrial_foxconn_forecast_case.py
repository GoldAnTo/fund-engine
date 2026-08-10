"""Strict live-source selector for the Industrial Foxconn historical case.

This module deliberately does not turn a broad provider search into a generic
"best match".  The single-case demonstration needs a reproducible input set,
so every title, publication date, metric and fund-position row is checked
before the ledger runner may persist it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

from app.datasources.gildata.adapters import parse_content, parse_table_markdown_payload


REPORT_QUERY = "工业富联 海通证券 2024-03-25"
ANNUAL_REPORT_QUERY = "工业富联:富士康工业互联网股份有限公司2024年年度报告"
FUND_REPORT_QUERY = "华夏中证5G通信主题交易型开放式指数证券投资基金2024年年度报告"
FUND_HOLDING_QUERY = "查询基金515050 2024年第4季度公开披露的股票持仓明细，包括股票代码、股票名称、持仓权重、报告期"

REPORT_TITLE = "工业富联(601138)：盈利整体平稳增长 AI业务表现强劲"
ANNUAL_REPORT_TITLE = "工业富联:富士康工业互联网股份有限公司2024年年度报告"
FUND_REPORT_TITLE = "华夏中证5G通信主题交易型开放式指数证券投资基金2024年年度报告"


class GildataClient(Protocol):
    def call_tool(self, name: str, arguments: dict[str, str]) -> str: ...


class SourceSelectionError(ValueError):
    """A provider response does not satisfy the predeclared case contract."""


@dataclass(frozen=True)
class ProviderDocument:
    title: str
    published_at: datetime
    content: str
    raw_response: str
    tool: str
    query: str


@dataclass(frozen=True)
class FundPosition:
    code: str
    name: str
    report_period: date
    stock_code: str
    stock_name: str
    position_weight: Decimal
    raw_response: str


@dataclass(frozen=True)
class IndustrialFoxconnSourceBundle:
    report: ProviderDocument
    annual_report: ProviderDocument
    fund_holding_snapshot: ProviderDocument
    fund_report: ProviderDocument
    expected_profit: Decimal
    baseline_profit: Decimal
    actual_profit: Decimal
    fund: FundPosition


def _utc_day(value: str, *, label: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SourceSelectionError(f"{label}发布时间不是 YYYY-MM-DD: {value!r}") from exc


def _rows(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in parse_content(raw):
        rows.extend(parse_table_markdown_payload(item.get("table_markdown", "")))
    return rows


def _document(
    client: GildataClient,
    *,
    tool: str,
    query: str,
    required_title: str,
    title_key: str,
) -> ProviderDocument:
    # Gildata ranks a query result set and can return a different page on a
    # repeat request.  Retry only to locate this exact approved title; never
    # accept a near-match or silently substitute another report.
    for _attempt in range(3):
        raw = client.call_tool(tool, {"query": query})
        for row in _rows(raw):
            title = row.get(title_key, "").strip()
            if title != required_title:
                continue
            published_at = _utc_day(row.get("发布时间", ""), label=required_title)
            content = row.get("原文", "").strip()
            if not content:
                raise SourceSelectionError(f"{required_title}未返回可冻结原文")
            return ProviderDocument(
                title=title,
                published_at=published_at,
                content=content,
                raw_response=raw,
                tool=tool,
                query=query,
            )
    raise SourceSelectionError(f"未找到已批准来源: {required_title}")


def _cny_yi(content: str, value: str, *, label: str) -> Decimal:
    marker = f"{value}亿元"
    if marker not in content:
        raise SourceSelectionError(f"{label}未包含预设数值 {marker}")
    try:
        return Decimal(value) * Decimal("100000000")
    except InvalidOperation as exc:
        raise SourceSelectionError(f"{label}数值不可解析: {value}") from exc


def _cny_million(content: str, value: str, *, label: str) -> Decimal:
    if value not in content:
        raise SourceSelectionError(f"{label}未包含预设数值 {value} 百万元")
    try:
        return Decimal(value) * Decimal("1000000")
    except InvalidOperation as exc:
        raise SourceSelectionError(f"{label}数值不可解析: {value}") from exc


def _forecast_baseline_profit(content: str) -> Decimal:
    # Two live payload variants expose the same 2023 baseline either as the
    # source table's 21040 百万元 or prose's 210.40 亿元.  Both are explicit
    # source values and normalize to the identical CNY amount.
    if "21040" in content:
        return _cny_million(content, "21040", label="研报冻结基线")
    return _cny_yi(content, "210.40", label="研报冻结基线")


def _fund_position(client: GildataClient) -> tuple[ProviderDocument, FundPosition]:
    raw = client.call_tool("FinQuery", {"query": FUND_HOLDING_QUERY})
    document = ProviderDocument(
        title="基金515050 2024年第四季度公开披露股票持仓明细",
        published_at=datetime(2025, 3, 31, tzinfo=timezone.utc),
        content=raw,
        raw_response=raw,
        tool="FinQuery",
        query=FUND_HOLDING_QUERY,
    )
    for row in _rows(raw):
        if row.get("基金代码", "").strip().split(".")[0] != "515050":
            continue
        if row.get("股票代码", "").strip() != "601138.SH":
            continue
        if row.get("报告期", "").strip() != "2024-12-31":
            continue
        try:
            weight = Decimal(row["持仓市值占资产净值比(%)"].strip()) / Decimal("100")
        except (KeyError, InvalidOperation) as exc:
            raise SourceSelectionError("基金515050持仓权重不可解析") from exc
        return document, FundPosition(
            code="515050",
            name=row.get("基金简称", "").strip(),
            report_period=date(2024, 12, 31),
            stock_code="601138.SH",
            stock_name=row.get("股票简称", "").strip(),
            position_weight=weight,
            raw_response=raw,
        )
    raise SourceSelectionError("未找到基金515050于2024-12-31披露的工业富联持仓")


def load_industrial_foxconn_sources(client: GildataClient) -> IndustrialFoxconnSourceBundle:
    """Return the four frozen source inputs for the approved live case only."""
    report = _document(
        client,
        tool="FinancialResearchReport",
        query=REPORT_QUERY,
        required_title=REPORT_TITLE,
        title_key="报告标题",
    )
    annual_report = _document(
        client,
        tool="AnnouncementData",
        query=ANNUAL_REPORT_QUERY,
        required_title=ANNUAL_REPORT_TITLE,
        title_key="公告标题",
    )
    fund_report = _document(
        client,
        tool="AnnouncementData",
        query=FUND_REPORT_QUERY,
        required_title=FUND_REPORT_TITLE,
        title_key="公告标题",
    )
    fund_holding_snapshot, fund = _fund_position(client)
    return IndustrialFoxconnSourceBundle(
        report=report,
        annual_report=annual_report,
        fund_holding_snapshot=fund_holding_snapshot,
        fund_report=fund_report,
        expected_profit=_cny_yi(report.content, "251.49", label="研报预测"),
        # 海通研报的“主要财务数据及预测”表以百万元列出 2023 年净利润
        # 21040；保持原单位文本在冻结原文中，账本中统一换算为 CNY。
        baseline_profit=_forecast_baseline_profit(report.content),
        actual_profit=_cny_yi(annual_report.content, "232.16", label="年度报告实际值"),
        fund=fund,
    )
