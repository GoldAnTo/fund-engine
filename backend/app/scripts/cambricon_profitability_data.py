"""Load auditable, immutable Cambricon profitability observations.

The official annual reports are the exact-yuan source.  Juyuan is retained
only as a separately parsed, rounded-亿元 corroboration of parent profit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
from typing import Any


_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cambricon_profitability_case"
_JY_PERIODS = {
    "2025Q1": ("2025-03-31", "一季报"),
    "2025H1": ("2025-06-30", "半年报"),
    "2025Q1-Q3": ("2025-09-30", "三季报"),
    "2025FY": ("2025-12-31", "年报"),
}
_QUARTERS_2025 = ("2025Q1", "2025Q2", "2025Q3", "2025Q4")
_QUARTERS_2024 = ("2024Q1", "2024Q2", "2024Q3", "2024Q4")
_DECIMAL_PATTERN = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"


@dataclass(frozen=True)
class JuyuanObservation:
    provider: str
    tool: str
    fetched_at: str
    query: str
    raw_response: str
    rounded_parent_profit_yi: dict[str, Decimal]


@dataclass(frozen=True)
class AnnualReportObservation:
    title: str
    source_url: str
    published_at: str
    page: int
    verbatim_text: str
    revenue: dict[str, Decimal]
    parent_profit: dict[str, Decimal]
    adjusted_profit: dict[str, Decimal]
    operating_cash_flow: dict[str, Decimal]


@dataclass(frozen=True)
class CaseData:
    juyuan: JuyuanObservation
    annual_report: AnnualReportObservation
    annual_report_2024: AnnualReportObservation
    parent_profit_2024_q4: Decimal
    single_quarter_parent_profit: dict[str, Decimal]
    single_quarter_adjusted_profit: dict[str, Decimal]
    single_quarter_operating_cash_flow: dict[str, Decimal]


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an exact decimal string")
    try:
        result = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _read_json(name: str) -> dict[str, Any]:
    try:
        payload = json.loads((_FIXTURE_DIR / name).read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON fixture: {name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON fixture must be an object: {name}")
    return payload


def _validate_aware_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{field} must be timezone-aware ISO-8601") from exc
    return value


def _parse_markdown_table(markdown: object, *, field: str) -> list[dict[str, str]]:
    if not isinstance(markdown, str):
        raise ValueError(f"{field} must be a Markdown table")
    lines = [line for line in markdown.splitlines() if line.startswith("|")]
    if len(lines) < 3:
        raise ValueError(f"{field} has no header and data rows")
    headers = lines[0].split("|")[1:-1]
    if not headers or any(not header for header in headers):
        raise ValueError(f"{field} has invalid headers")
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = line.split("|")[1:-1]
        if len(cells) != len(headers):
            raise ValueError(f"{field} has malformed row")
        rows.append(dict(zip(headers, cells, strict=True)))
    if not rows:
        raise ValueError(f"{field} has no data rows")
    return rows


def _parse_juyuan_parent_profit(raw_response: object) -> dict[str, Decimal]:
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ValueError("Juyuan raw_response must be nonempty exact response text")
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ValueError("Juyuan raw_response is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("code") != "0":
        raise ValueError("Juyuan raw_response has unsuccessful code")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Juyuan raw_response has no results")

    parent_rows: list[dict[str, str]] = []
    for result in results:
        if not isinstance(result, dict) or result.get("api_name") != "财务报表":
            continue
        parent_rows.extend(
            row
            for row in _parse_markdown_table(result.get("table_markdown"), field="Juyuan financial table")
            if row.get("股票名称") == "寒武纪"
            and row.get("股票代码") == "688256"
            and row.get("财务科目名称") == "归属于母公司所有者的净利润"
            and row.get("财务科目代码") == "NPParentCompanyOwners"
            and row.get("核算方式") == "累计值"
            and row.get("展示单位") == "亿元"
        )

    observed: dict[str, Decimal] = {}
    for period, (date, report_period) in _JY_PERIODS.items():
        matches = [
            row
            for row in parent_rows
            if row.get("时间") == date and row.get("报告期") == report_period
        ]
        if len(matches) != 1:
            raise ValueError(f"Juyuan missing or ambiguous parent-profit row for {period}")
        observed[period] = _decimal(matches[0].get("财务科目数额"), field=f"Juyuan.{period}")
    return observed


def _validate_juyuan_adjusted_profit(raw_response: object) -> None:
    """Validate the second metric in the combined response without using its precision."""
    if not isinstance(raw_response, str):
        raise ValueError("Juyuan raw_response must be text")
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ValueError("Juyuan raw_response is not valid JSON") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("Juyuan raw_response has invalid results")
    adjusted_rows: list[dict[str, str]] = []
    for result in results:
        if not isinstance(result, dict) or result.get("api_name") != "财务分析":
            continue
        adjusted_rows.extend(
            row
            for row in _parse_markdown_table(result.get("table_markdown"), field="Juyuan adjusted-profit table")
            if row.get("股票名称") == "寒武纪"
            and row.get("股票代码") == "688256"
            and row.get("财务分析指标名称") == "扣除非经常损益后的归母净利润"
            and row.get("财务分析指标代码") == "NetProfitCut"
            and row.get("核算方式") == "累计值"
            and row.get("展示单位") == "元"
        )
    for period, (date, report_period) in _JY_PERIODS.items():
        matches = [
            row
            for row in adjusted_rows
            if row.get("时间") == date and row.get("报告期") == report_period
        ]
        if len(matches) != 1:
            raise ValueError(f"Juyuan missing or ambiguous adjusted-profit row for {period}")
        _decimal(matches[0].get("财务分析指标数额"), field=f"Juyuan.adjusted.{period}")


def _juyuan_observation(payload: dict[str, Any]) -> JuyuanObservation:
    required = {
        "provider",
        "tool",
        "fetched_at",
        "query",
        "raw_response",
        "rounded_parent_profit_yi",
    }
    if set(payload) != required:
        raise ValueError("Juyuan fixture has missing or unexpected schema fields")
    if payload["provider"] != "gildata-juyuan" or payload["tool"] != "FinQuery":
        raise ValueError("Juyuan fixture has invalid provider metadata")
    query = payload["query"]
    if query != "寒武纪688256 2025年各季度归母净利润、扣非归母净利润":
        raise ValueError("Juyuan fixture has an unsupported supporting query")
    rounded_raw = payload["rounded_parent_profit_yi"]
    if not isinstance(rounded_raw, dict) or set(rounded_raw) != set(_JY_PERIODS):
        raise ValueError("Juyuan rounded observations have missing or extra periods")
    rounded = {
        period: _decimal(rounded_raw[period], field=f"Juyuan.rounded_parent_profit_yi.{period}")
        for period in _JY_PERIODS
    }
    parsed = _parse_juyuan_parent_profit(payload["raw_response"])
    _validate_juyuan_adjusted_profit(payload["raw_response"])
    if rounded != parsed:
        raise ValueError("Juyuan normalized rounded observations differ from raw response")
    return JuyuanObservation(
        provider=payload["provider"],
        tool=payload["tool"],
        fetched_at=_validate_aware_timestamp(payload["fetched_at"], field="Juyuan fetched_at"),
        query=query,
        raw_response=payload["raw_response"],
        rounded_parent_profit_yi=rounded,
    )


def _load_provenance(name: str, *, host: str, page: int) -> dict[str, Any]:
    payload = _read_json(name)
    if set(payload) != {"title", "source_url", "published_at", "page"}:
        raise ValueError(f"official provenance has invalid schema: {name}")
    if not isinstance(payload["title"], str) or not payload["title"].strip():
        raise ValueError(f"official provenance has invalid title: {name}")
    if not isinstance(payload["source_url"], str) or not payload["source_url"].startswith(host):
        raise ValueError(f"official provenance has invalid source URL: {name}")
    if payload["page"] != page:
        raise ValueError(f"official provenance has invalid page: {name}")
    _validate_aware_timestamp(payload["published_at"], field=f"official provenance published_at {name}")
    return payload


def _extract_four_values(text: str, pattern: str, *, field: str) -> list[Decimal]:
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"official report missing {field} verbatim section")
    values = re.findall(_DECIMAL_PATTERN, match.group("values"))
    if len(values) != 4:
        raise ValueError(f"official report {field} must contain exactly four displayed values")
    return [_decimal(value, field=f"official report.{field}") for value in values]


def _as_quarters(periods: tuple[str, ...], values: list[Decimal]) -> dict[str, Decimal]:
    return dict(zip(periods, values, strict=True))


def _parse_2025_annual_report() -> AnnualReportObservation:
    provenance = _load_provenance(
        "cninfo_2025_annual_report_page_10.provenance.json",
        host="https://dataclouds.cninfo.com.cn/",
        page=10,
    )
    verbatim_text = (_FIXTURE_DIR / "cninfo_2025_annual_report_page_10.txt").read_text("utf-8")
    if "[ROW" in verbatim_text or not verbatim_text.startswith("                  中科寒武纪科技股份有限公司2025"):
        raise ValueError("official 2025 report must be a separate verbatim text fixture")
    try:
        quarterly_table = verbatim_text.split("八、2025年分季度主要财务数据", maxsplit=1)[1]
    except IndexError as exc:
        raise ValueError("official 2025 report has no quarterly-data table") from exc
    revenue = _extract_four_values(
        quarterly_table,
        r"营业收入\s*(?P<values>.*?)\s*归属于上市\s*公司股东的",
        field="2025 revenue",
    )
    parent_profit = _extract_four_values(
        quarterly_table,
        r"归属于上市\s*公司股东的\s*(?P<values>.*?)\s*净利润\s*归属于上市\s*公司股东的\s*扣除",
        field="2025 parent profit",
    )
    adjusted_profit = _extract_four_values(
        quarterly_table,
        r"扣除非经常\s*(?P<values>.*?)\s*性损益后的\s*净利润\s*经营活动产",
        field="2025 adjusted profit",
    )
    operating_cash_flow = _extract_four_values(
        quarterly_table,
        r"经营活动产\s*生的现金流\s*(?P<values>.*?)\s*量净额",
        field="2025 operating cash flow",
    )
    return AnnualReportObservation(
        title=provenance["title"],
        source_url=provenance["source_url"],
        published_at=provenance["published_at"],
        page=provenance["page"],
        verbatim_text=verbatim_text,
        revenue=_as_quarters(_QUARTERS_2025, revenue),
        parent_profit=_as_quarters(_QUARTERS_2025, parent_profit),
        adjusted_profit=_as_quarters(_QUARTERS_2025, adjusted_profit),
        operating_cash_flow=_as_quarters(_QUARTERS_2025, operating_cash_flow),
    )


def _parse_2024_annual_report() -> AnnualReportObservation:
    provenance = _load_provenance(
        "sse_2024_annual_report_page_11.provenance.json",
        host="https://big5.sse.com.cn/",
        page=11,
    )
    verbatim_text = (_FIXTURE_DIR / "sse_2024_annual_report_page_11.txt").read_text("utf-8")
    if "[ROW" in verbatim_text or not verbatim_text.startswith("                  中科寒武纪科技股份有限公司2024"):
        raise ValueError("official 2024 report must be a separate verbatim text fixture")
    parent_profit = _extract_four_values(
        verbatim_text,
        r"八、2024年分季度主要财务数据.*?归属于上市公司\s*(?P<values>.*?)\s*股东的净利润",
        field="2024 parent profit",
    )
    return AnnualReportObservation(
        title=provenance["title"],
        source_url=provenance["source_url"],
        published_at=provenance["published_at"],
        page=provenance["page"],
        verbatim_text=verbatim_text,
        revenue={},
        parent_profit=_as_quarters(_QUARTERS_2024, parent_profit),
        adjusted_profit={},
        operating_cash_flow={},
    )


def load_case_data() -> CaseData:
    """Load and reconcile the official exact source with Juyuan's rounded check."""
    juyuan = _juyuan_observation(_read_json("juyuan_finquery_2026-08-03.json"))
    annual_report = _parse_2025_annual_report()
    annual_report_2024 = _parse_2024_annual_report()
    parent_quarters = annual_report.parent_profit
    official_cumulative = {
        "2025Q1": parent_quarters["2025Q1"],
        "2025H1": parent_quarters["2025Q1"] + parent_quarters["2025Q2"],
        "2025Q1-Q3": parent_quarters["2025Q1"] + parent_quarters["2025Q2"] + parent_quarters["2025Q3"],
        "2025FY": sum(parent_quarters.values()),
    }
    official_rounded = {
        period: (value / Decimal("100000000")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for period, value in official_cumulative.items()
    }
    if official_rounded != juyuan.rounded_parent_profit_yi:
        raise ValueError("official parent profit does not reconcile to Juyuan rounded observations")
    return CaseData(
        juyuan=juyuan,
        annual_report=annual_report,
        annual_report_2024=annual_report_2024,
        parent_profit_2024_q4=annual_report_2024.parent_profit["2024Q4"],
        single_quarter_parent_profit=parent_quarters,
        single_quarter_adjusted_profit=annual_report.adjusted_profit,
        single_quarter_operating_cash_flow=annual_report.operating_cash_flow,
    )
