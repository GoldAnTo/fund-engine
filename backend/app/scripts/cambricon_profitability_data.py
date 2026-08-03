"""Load the immutable Cambricon profitability observations without side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any


_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cambricon_profitability_case"
_PERIODS = ("2025Q1", "2025H1", "2025Q1-Q3", "2025FY")
_QUARTERS = ("2025Q1", "2025Q2", "2025Q3", "2025Q4")
_QUARTER_ROWS = {
    "revenue": "营业收入",
    "parent_profit": "归属于上市公司股东的净利润",
    "adjusted_profit": "扣除非经常性损益后的归母净利润",
    "operating_cash_flow": "经营活动产生的现金流量净额",
}
_REQUIRED_REPORT_METADATA = ("TITLE", "SOURCE_URL", "PUBLISHED_AT", "PAGE")


@dataclass(frozen=True)
class JuyuanObservation:
    provider: str
    tool: str
    fetched_at: str
    queries: tuple[str, ...]
    raw_responses: tuple[str, ...]


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
    parent_profit_2024_q4: Decimal
    single_quarter_parent_profit: dict[str, Decimal]
    single_quarter_adjusted_profit: dict[str, Decimal]
    single_quarter_operating_cash_flow: dict[str, Decimal]


def _as_decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _load_cumulative(payload: dict[str, Any], key: str) -> dict[str, Decimal]:
    raw = payload.get(key)
    if not isinstance(raw, dict) or set(raw) != set(_PERIODS):
        raise ValueError(f"{key} must contain exactly {_PERIODS}")
    return {period: _as_decimal(raw[period], field=f"{key}.{period}") for period in _PERIODS}


def _derive_quarters(cumulative: dict[str, Decimal]) -> dict[str, Decimal]:
    """Convert cumulative reporting periods to individual quarters exactly."""
    if set(cumulative) != set(_PERIODS):
        raise ValueError(f"cumulative data must contain exactly {_PERIODS}")
    return {
        "2025Q1": cumulative["2025Q1"],
        "2025Q2": cumulative["2025H1"] - cumulative["2025Q1"],
        "2025Q3": cumulative["2025Q1-Q3"] - cumulative["2025H1"],
        "2025Q4": cumulative["2025FY"] - cumulative["2025Q1-Q3"],
    }


def _juyuan_observation(payload: object) -> JuyuanObservation:
    if not isinstance(payload, dict):
        raise ValueError("Juyuan fixture must be a JSON object")
    required = {
        "provider",
        "tool",
        "fetched_at",
        "queries",
        "cumulative_parent_profit_yuan",
        "cumulative_adjusted_profit_yuan",
        "raw_responses",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"invalid Juyuan fixture; missing={sorted(missing)}")
    provider = payload["provider"]
    tool = payload["tool"]
    fetched_at = payload["fetched_at"]
    queries = payload["queries"]
    raw_responses = payload["raw_responses"]
    if provider != "gildata-juyuan" or tool != "FinQuery":
        raise ValueError("invalid Juyuan provider metadata")
    if not isinstance(fetched_at, str):
        raise ValueError("Juyuan fetched_at must be an ISO timestamp")
    try:
        if datetime.fromisoformat(fetched_at).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Juyuan fetched_at must be timezone-aware ISO-8601") from exc
    if not isinstance(queries, list) or not all(isinstance(query, str) and query for query in queries):
        raise ValueError("Juyuan queries must be nonempty strings")
    if not isinstance(raw_responses, list) or not raw_responses or not all(
        isinstance(response, str) and response.strip() for response in raw_responses
    ):
        raise ValueError("Juyuan raw_responses must contain nonempty exact response text")
    _load_cumulative(payload, "cumulative_parent_profit_yuan")
    _load_cumulative(payload, "cumulative_adjusted_profit_yuan")
    return JuyuanObservation(
        provider=provider,
        tool=tool,
        fetched_at=fetched_at,
        queries=tuple(queries),
        raw_responses=tuple(raw_responses),
    )


def _parse_annual_report(verbatim_text: str) -> AnnualReportObservation:
    metadata: dict[str, str] = {}
    rows: dict[str, dict[str, Decimal]] = {}
    for line in verbatim_text.splitlines():
        if line.startswith("# "):
            match = re.fullmatch(r"# ([A-Z_]+): (.+)", line)
            if match is None:
                raise ValueError(f"invalid annual-report metadata line: {line!r}")
            key, value = match.groups()
            if key in metadata:
                raise ValueError(f"duplicate annual-report metadata: {key}")
            metadata[key] = value
            continue
        row_match = re.fullmatch(r"\[ROW (.+)\] (.+)", line)
        if row_match is None:
            raise ValueError(f"invalid annual-report row: {line!r}")
        label, raw_values = row_match.groups()
        matching_name = next((name for name, expected in _QUARTER_ROWS.items() if expected == label), None)
        if matching_name is None or matching_name in rows:
            raise ValueError(f"unexpected or duplicate annual-report row: {label!r}")
        values = raw_values.split(" | ")
        if len(values) != 4:
            raise ValueError(f"annual-report row {label!r} must contain exactly four periods")
        rows[matching_name] = {
            quarter: _as_decimal(value, field=f"annual-report.{label}.{quarter}")
            for quarter, value in zip(_QUARTERS, values, strict=True)
        }

    if set(metadata) != set(_REQUIRED_REPORT_METADATA):
        raise ValueError("annual-report metadata is missing required fields or contains extras")
    if set(rows) != set(_QUARTER_ROWS):
        raise ValueError("annual-report rows are missing required periods or contain extras")
    try:
        page = int(metadata["PAGE"])
    except ValueError as exc:
        raise ValueError("annual-report PAGE must be an integer") from exc
    if page != 10 or not metadata["SOURCE_URL"].startswith("https://dataclouds.cninfo.com.cn/"):
        raise ValueError("annual-report provenance must identify official CNINFO page 10")
    try:
        if datetime.fromisoformat(metadata["PUBLISHED_AT"]).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("annual-report PUBLISHED_AT must be timezone-aware ISO-8601") from exc
    return AnnualReportObservation(
        title=metadata["TITLE"],
        source_url=metadata["SOURCE_URL"],
        published_at=metadata["PUBLISHED_AT"],
        page=page,
        verbatim_text=verbatim_text,
        revenue=rows["revenue"],
        parent_profit=rows["parent_profit"],
        adjusted_profit=rows["adjusted_profit"],
        operating_cash_flow=rows["operating_cash_flow"],
    )


def load_case_data() -> CaseData:
    """Load, validate, derive, and reconcile the frozen observations."""
    try:
        payload = json.loads((_FIXTURE_DIR / "juyuan_finquery_2026-08-03.json").read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Juyuan fixture is not valid JSON") from exc
    juyuan = _juyuan_observation(payload)
    parent = _load_cumulative(payload, "cumulative_parent_profit_yuan")
    adjusted = _load_cumulative(payload, "cumulative_adjusted_profit_yuan")
    report = _parse_annual_report(
        (_FIXTURE_DIR / "cninfo_2025_annual_report_page_10.txt").read_text("utf-8")
    )
    derived_parent = _derive_quarters(parent)
    derived_adjusted = _derive_quarters(adjusted)
    if derived_parent != report.parent_profit or derived_adjusted != report.adjusted_profit:
        raise ValueError("Juyuan quarterly derivation does not reconcile to CNINFO page 10")
    return CaseData(
        juyuan=juyuan,
        annual_report=report,
        parent_profit_2024_q4=Decimal("272152952.65"),
        single_quarter_parent_profit=derived_parent,
        single_quarter_adjusted_profit=derived_adjusted,
        single_quarter_operating_cash_flow=report.operating_cash_flow,
    )
