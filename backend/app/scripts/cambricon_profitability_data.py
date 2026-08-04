"""Load auditable, immutable Cambricon profitability observations.

The official annual reports are the exact-yuan source.  Juyuan is retained
only as a separately parsed, rounded-亿元 corroboration of parent profit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


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
_SOURCE_SHA256 = MappingProxyType(
    {
        "cninfo_2025_annual_report_page_10.txt": "d9c912fcf58ddf23f677248c17d5c30b51076cfa1fbcd3166527e7d526add96f",
        "sse_2024_annual_report_page_11.txt": "af4c70658266f51f11cf264185d27890e1b38fa67aa0f004b1460e18c28e55f2",
    }
)
_JUYUAN_RAW_RESPONSE_SHA256 = "bf9f166e16854f0838ab3bfeb11811a9acef6d32df73053576d5d3fdb17f5407"
_JUYUAN_PROVENANCE = MappingProxyType(
    {
        "provider": "gildata-juyuan",
        "tool": "FinQuery",
        "fetched_at": "2026-08-03T15:21:55+08:00",
        "query": "寒武纪688256 2025年各季度归母净利润、扣非归母净利润",
    }
)
_OFFICIAL_PROVENANCE = MappingProxyType(
    {
        "cninfo_2025_annual_report_page_10.provenance.json": MappingProxyType(
            {
                "title": "中科寒武纪科技股份有限公司2025年年度报告",
                "source_url": "https://dataclouds.cninfo.com.cn/shgonggao/hsomarket/2026/20260312/05ca784762a7401b9ed371d917e436dc.PDF",
                "published_at": "2026-03-12T00:00:00+08:00",
                "page": 10,
            }
        ),
        "sse_2024_annual_report_page_11.provenance.json": MappingProxyType(
            {
                "title": "中科寒武纪科技股份有限公司2024年年度报告",
                "source_url": "https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-04-19/688256_20250419_11FJ.pdf",
                "published_at": "2025-04-19T00:00:00+08:00",
                "page": 11,
            }
        ),
    }
)


@dataclass(frozen=True)
class JuyuanObservation:
    provider: str
    tool: str
    fetched_at: str
    query: str
    raw_response: str
    rounded_parent_profit_yi: Mapping[str, Decimal]


@dataclass(frozen=True)
class AnnualReportObservation:
    title: str
    source_url: str
    published_at: str
    page: int
    verbatim_text: str
    revenue: Mapping[str, Decimal]
    parent_profit: Mapping[str, Decimal]
    adjusted_profit: Mapping[str, Decimal]
    operating_cash_flow: Mapping[str, Decimal]


@dataclass(frozen=True)
class CaseData:
    juyuan: JuyuanObservation
    annual_report: AnnualReportObservation
    annual_report_2024: AnnualReportObservation
    parent_profit_2024_q4: Decimal
    single_quarter_parent_profit: Mapping[str, Decimal]
    single_quarter_adjusted_profit: Mapping[str, Decimal]
    single_quarter_operating_cash_flow: Mapping[str, Decimal]


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


def _verify_sha256(data: bytes, *, expected: str, field: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"fixture integrity check failed for {field}")


def _read_verified_text(name: str) -> str:
    try:
        expected = _SOURCE_SHA256[name]
    except KeyError as exc:
        raise ValueError(f"no fixture integrity pin for {name}") from exc
    raw = (_FIXTURE_DIR / name).read_bytes()
    _verify_sha256(raw, expected=expected, field=name)
    return raw.decode("utf-8")


def _immutable_mapping(values: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    return MappingProxyType(dict(values))


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


def _validate_juyuan_adjusted_profit(raw_response: object) -> dict[str, Decimal]:
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
    observed: dict[str, Decimal] = {}
    for period, (date, report_period) in _JY_PERIODS.items():
        matches = [
            row
            for row in adjusted_rows
            if row.get("时间") == date and row.get("报告期") == report_period
        ]
        if len(matches) != 1:
            raise ValueError(f"Juyuan missing or ambiguous adjusted-profit row for {period}")
        observed[period] = _decimal(
            matches[0].get("财务分析指标数额"), field=f"Juyuan.adjusted.{period}"
        )
    return observed


def _juyuan_observation(
    payload: dict[str, Any], *, official_adjusted_cumulative: dict[str, Decimal]
) -> JuyuanObservation:
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
    for field, expected in _JUYUAN_PROVENANCE.items():
        if payload[field] != expected:
            raise ValueError(f"Juyuan fixture has invalid provenance field: {field}")
    query = payload["query"]
    rounded_raw = payload["rounded_parent_profit_yi"]
    if not isinstance(rounded_raw, dict) or set(rounded_raw) != set(_JY_PERIODS):
        raise ValueError("Juyuan rounded observations have missing or extra periods")
    rounded = {
        period: _decimal(rounded_raw[period], field=f"Juyuan.rounded_parent_profit_yi.{period}")
        for period in _JY_PERIODS
    }
    raw_response = payload["raw_response"]
    if not isinstance(raw_response, str):
        raise ValueError("Juyuan raw_response must be text")
    _verify_sha256(
        raw_response.encode("utf-8"),
        expected=_JUYUAN_RAW_RESPONSE_SHA256,
        field="Juyuan raw_response",
    )
    parsed = _parse_juyuan_parent_profit(raw_response)
    parsed_adjusted = _validate_juyuan_adjusted_profit(raw_response)
    if rounded != parsed:
        raise ValueError("Juyuan normalized rounded observations differ from raw response")
    if parsed_adjusted != official_adjusted_cumulative:
        raise ValueError("Juyuan adjusted profit differs from official CNINFO cumulative values")
    return JuyuanObservation(
        provider=payload["provider"],
        tool=payload["tool"],
        fetched_at=_validate_aware_timestamp(payload["fetched_at"], field="Juyuan fetched_at"),
        query=query,
        raw_response=raw_response,
        rounded_parent_profit_yi=_immutable_mapping(rounded),
    )


def _load_provenance(name: str) -> dict[str, Any]:
    payload = _read_json(name)
    expected = _OFFICIAL_PROVENANCE.get(name)
    if expected is None or payload != dict(expected):
        raise ValueError(f"official provenance differs from pinned fields: {name}")
    return payload


def _extract_four_values(text: str, pattern: str, *, field: str) -> list[Decimal]:
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"official report missing {field} verbatim section")
    values = re.findall(_DECIMAL_PATTERN, match.group("values"))
    if len(values) != 4:
        raise ValueError(f"official report {field} must contain exactly four displayed values")
    return [_decimal(value, field=f"official report.{field}") for value in values]


def _as_quarters(periods: tuple[str, ...], values: list[Decimal]) -> Mapping[str, Decimal]:
    return _immutable_mapping(dict(zip(periods, values, strict=True)))


def _cumulative_2025(quarters: Mapping[str, Decimal]) -> dict[str, Decimal]:
    return {
        "2025Q1": quarters["2025Q1"],
        "2025H1": quarters["2025Q1"] + quarters["2025Q2"],
        "2025Q1-Q3": quarters["2025Q1"] + quarters["2025Q2"] + quarters["2025Q3"],
        "2025FY": sum(quarters.values()),
    }


def _require_quarter_table_context(verbatim_text: str, *, year: int) -> str:
    heading = f"八、{year}年分季度主要财务数据"
    if year not in {2024, 2025} or verbatim_text.count(heading) != 1:
        raise ValueError(f"official {year} report has invalid quarterly table context")
    quarterly_table = verbatim_text.split(heading, maxsplit=1)[1]
    expected_header = re.compile(
        r"单位：元\s+币种：人民币\s+"
        r"第一季度\s+第二季度\s+第三季度\s+第四季度\s+"
        r"（1-3月份）\s+（4-6\s*月份）\s+（7-9月份）\s+（10-12月份）"
    )
    if expected_header.search(quarterly_table) is None:
        raise ValueError(f"official {year} report has invalid quarterly table context")
    return quarterly_table


def _parse_2025_annual_report() -> AnnualReportObservation:
    provenance = _load_provenance("cninfo_2025_annual_report_page_10.provenance.json")
    verbatim_text = _read_verified_text("cninfo_2025_annual_report_page_10.txt")
    if "[ROW" in verbatim_text or not verbatim_text.startswith("                  中科寒武纪科技股份有限公司2025"):
        raise ValueError("official 2025 report must be a separate verbatim text fixture")
    quarterly_table = _require_quarter_table_context(verbatim_text, year=2025)
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
    provenance = _load_provenance("sse_2024_annual_report_page_11.provenance.json")
    verbatim_text = _read_verified_text("sse_2024_annual_report_page_11.txt")
    if "[ROW" in verbatim_text or not verbatim_text.startswith("                  中科寒武纪科技股份有限公司2024"):
        raise ValueError("official 2024 report must be a separate verbatim text fixture")
    quarterly_table = _require_quarter_table_context(verbatim_text, year=2024)
    parent_profit = _extract_four_values(
        quarterly_table,
        r"归属于上市公司\s*(?P<values>.*?)\s*股东的净利润",
        field="2024 parent profit",
    )
    return AnnualReportObservation(
        title=provenance["title"],
        source_url=provenance["source_url"],
        published_at=provenance["published_at"],
        page=provenance["page"],
        verbatim_text=verbatim_text,
        revenue=_immutable_mapping({}),
        parent_profit=_as_quarters(_QUARTERS_2024, parent_profit),
        adjusted_profit=_immutable_mapping({}),
        operating_cash_flow=_immutable_mapping({}),
    )


def load_case_data() -> CaseData:
    """Load and reconcile the official exact source with Juyuan's rounded check."""
    annual_report = _parse_2025_annual_report()
    annual_report_2024 = _parse_2024_annual_report()
    juyuan = _juyuan_observation(
        _read_json("juyuan_finquery_2026-08-03.json"),
        official_adjusted_cumulative=_cumulative_2025(annual_report.adjusted_profit),
    )
    parent_quarters = annual_report.parent_profit
    official_cumulative = _cumulative_2025(parent_quarters)
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
