"""Rule-based extraction of disclosed financial facts from table-like spans.

Financial tables in annual reports / earnings releases follow a stable
"header years + unit line + metric rows" structure.  Parsing them with
rules is deterministic, auditable, and free — the LLM is reserved for
narrative text (see ``app/ai/extraction.py`` which routes table spans here
first and only sends unhandled spans to the model).

Ported and adapted from the Verifiable-Company-Research-Agent modules
``financial_table_extraction.py`` / ``fact_patterns.py`` /
``fact_plausibility.py`` / ``fact_value_normalization.py`` (MIT).
Differences from the source:

- Output is statement-shaped (``TableFact.statement_text`` +
  ``observed_period``) so results flow into ``SourceStatement`` with
  ``kind=disclosed_fact``; no ``confidence`` field exists anywhere —
  statement strength is expressed by review state and source level, not by
  a self-reported number.
- The vehicle-manufacturer wide-table special case is dropped (irrelevant
  to the AI-compute evidence slice).
- ``observed_period`` uses the period-end convention: an annual figure for
  year Y is dated ``Y-12-31``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

NUMBER_PATTERN = r"\d{1,3}(?:[ ,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"
UNITS = r"亿元|亿|千元|万元|元|%|辆|台|万辆|万台|吨|万吨|GWh|MWh"
INLINE_VALUE_UNIT_PATTERN = rf"(?P<value_inline>{NUMBER_PATTERN})\s*(?P<unit_inline>{UNITS})"
YEAR_PATTERN = r"(?P<year>20\d{2})\s*年"
_BARE_NUMBER_PATTERN = rf"(?<![A-Za-z0-9])({NUMBER_PATTERN})(?![A-Za-z0-9])"

METRIC_LABELS = {
    "R&D_expenditure": "研发费用",
    "R&D_total_spending": "研发投入合计",
    "revenue": "营业收入",
    "net_profit": "净利润",
    "net_profit_parent": "归母净利润",
    "net_profit_deducted": "扣非净利润",
    "revenue_segment": "分业务收入",
    "production_capacity": "产能",
    "production_volume": "产量",
    "sales_volume": "销量",
}

_MONEY_METRICS = frozenset(
    {
        "R&D_expenditure",
        "R&D_total_spending",
        "revenue",
        "revenue_segment",
        "net_profit",
        "net_profit_parent",
        "net_profit_deducted",
    }
)

# 上市公司金额类指标以「元」计通常至少亿级；低于该值多为章节号或比例误匹配。
_MIN_LISTED_COMPANY_MONEY_YUAN = Decimal("100000000")

_MONEY_FACTORS = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
    "亿": Decimal("100000000"),
    "万": Decimal("10000"),
}

_SECTION_NUMBER_VALUE_RE = re.compile(
    r"^\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)[、.．]"
)
_CUMULATIVE_RD_RE = re.compile(r"累计.{0,12}(?:研发|R&D)")

_NOISY_DIMENSION_TOKENS = (
    "增值税", "消费税", "所得税", "税率", "税费", "银行", "存款", "债券",
    "利息", "余额", "期末", "申购", "支付总价", "金融资产", "保证金",
    "营业成本", "营业外", "销售费用", "管理费用", "财务费用",
)


# ---------------------------------------------------------------------------
# Value normalization (money units -> yuan, for plausibility checks)
# ---------------------------------------------------------------------------


def normalize_money_to_yuan(value: str) -> Decimal | None:
    """Normalize a money string like ``634亿`` / ``50,000万元`` to yuan."""
    match = re.match(
        rf"^\s*(?P<number>{NUMBER_PATTERN})\s*(?P<unit>亿元|亿|千元|万元|元|万)\s*$",
        value,
    )
    if match is None:
        return None
    try:
        number = Decimal(match.group("number").replace(",", "").replace(" ", ""))
    except InvalidOperation:
        return None
    factor = _MONEY_FACTORS.get(match.group("unit"))
    return number * factor if factor is not None else None


# ---------------------------------------------------------------------------
# Plausibility guards
# ---------------------------------------------------------------------------


def is_section_heading_line(line: str) -> bool:
    """章节标题如「4、研发投入」，不是带金额的表格行。"""
    cleaned = line.strip()
    if not _SECTION_NUMBER_VALUE_RE.match(cleaned):
        return False
    # 行内若已有典型财报金额（千分位 + 小数），按数据行处理。
    return not re.search(r"\d{1,3}(?:,\d{3})+\.\d{2}", cleaned)


def is_section_number_token(*, line: str, end: int) -> bool:
    """裸数字后紧跟「、」「.」「．」的是章节序号，不是金额。"""
    return end < len(line) and line[end] in "、.．"


def is_implausible_value(metric_base: str, value: str, *, line: str) -> bool:
    """True = 丢弃该抽取结果。"""
    value = (value or "").strip()
    if metric_base == "R&D_expenditure" and _CUMULATIVE_RD_RE.search(line):
        return True
    if value.endswith("%"):
        # 金额类指标不接受百分比值（常见于「金额 + 同比%」混排误匹配）。
        return metric_base in _MONEY_METRICS
    if metric_base not in _MONEY_METRICS:
        return False
    yuan = normalize_money_to_yuan(value)
    if yuan is None:
        return False
    return yuan < _MIN_LISTED_COMPANY_MONEY_YUAN


# ---------------------------------------------------------------------------
# Numeric extraction helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TableValue:
    value: str
    start: int
    end: int


def _value_and_unit(match: re.Match[str]) -> tuple[str, str]:
    value = match.group("value_inline").replace(",", "").replace(" ", "")
    unit = match.group("unit_inline")
    if unit == "亿":
        unit = "亿元"
    return f"{value}{unit}", unit


def extract_numeric_values(line: str, *, fallback_unit: str | None) -> list[TableValue]:
    """提取行内数值；带单位的优先，纯数字回退到表头单位行声明的单位。"""
    with_units = [
        TableValue(value=_value_and_unit(m)[0], start=m.start(), end=m.end())
        for m in re.finditer(INLINE_VALUE_UNIT_PATTERN, line)
    ]
    # 费用表常见「金额 + 同比%」混排：若仅有百分比带单位，仍回退解析千分位金额。
    if with_units and any(not item.value.endswith("%") for item in with_units):
        return with_units
    if fallback_unit is None:
        return with_units
    raw_values: list[TableValue] = []
    for match in re.finditer(_BARE_NUMBER_PATTERN, line):
        if is_section_number_token(line=line, end=match.end()):
            continue
        value = match.group(1).replace(",", "").replace(" ", "")
        raw_values.append(
            TableValue(value=f"{value}{fallback_unit}", start=match.start(), end=match.end())
        )
    return raw_values


def clean_dimension(raw: str) -> str | None:
    cleaned = re.sub(r"[\s:：,，|]+", " ", raw).strip(" -_/|：:，,")
    cleaned = cleaned.replace("项目", "").replace("产品类别", "").strip()
    if not cleaned:
        return None
    return cleaned[-40:]


# ---------------------------------------------------------------------------
# Extraction result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TableFact:
    """One disclosed numeric fact extracted from a table row."""

    metric_name: str
    statement_text: str
    observed_period: date


# ---------------------------------------------------------------------------
# Table extraction state machine
# ---------------------------------------------------------------------------


class FinancialTableExtractor:
    """从"表头年份 + 单位行 + 指标行"结构的文本中抽取披露数值。

    普通自然语言不属于本模块职责，交由 LLM 抽取链路处理。
    """

    def extract(self, text: str) -> list[TableFact]:
        facts: list[TableFact] = []
        current_years: list[str] = []
        current_unit: str | None = None

        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                continue

            years = [m.group("year") for m in re.finditer(YEAR_PATTERN, clean)]
            unit = self._unit_from_line(clean)
            if unit:
                current_unit = unit
            if is_section_heading_line(clean):
                continue

            row_metric = self._table_row_metric(clean)
            values = (
                extract_numeric_values(clean, fallback_unit=current_unit)
                if row_metric is not None
                else []
            )
            if len(years) >= 2 and row_metric is None:
                # 表头年份行（如「2025年 2024年」）
                current_years = years
            elif len(years) == 1 and not values and not current_years:
                current_years = years

            if row_metric is None or not current_years or not self._looks_like_table_row(clean):
                continue
            if not values:
                continue

            metric_base, dimension = row_metric
            for idx, value_match in enumerate(values[: len(current_years)]):
                value = value_match.value
                if self._should_skip_value(
                    metric_base=metric_base, dimension=dimension, value=value, line=clean
                ):
                    continue
                period = current_years[idx]
                metric_name = self._metric_name(metric_base, dimension)
                label = self._claim_label(metric_base, metric_name)
                facts.append(
                    TableFact(
                        metric_name=metric_name,
                        statement_text=f"{period}年{label}为{value}",
                        observed_period=date(int(period), 12, 31),
                    )
                )

        return facts

    # ------------------------------------------------------------- helpers

    def _unit_from_line(self, line: str) -> str | None:
        match = re.search(rf"单位[:：]\s*(?P<unit>{UNITS})", line)
        if not match:
            return None
        unit = match.group("unit")
        return "亿元" if unit == "亿" else unit

    def _looks_like_table_row(self, line: str) -> bool:
        # 含句读的是叙述句，不是表格行；顿号「、」不排除（常见于「A、B、C增加所致」）。
        if any(mark in line for mark in ("。", "；", ";")):
            return False
        if is_section_heading_line(line):
            return False
        return len(line) <= 220

    def _table_row_metric(self, line: str) -> tuple[str, str | None] | None:
        if not line:
            return None
        if "研发费用" in line:
            return "R&D_expenditure", None
        if "研发投入" in line:
            return "R&D_total_spending", None
        # 利润类指标须在通用「收入」判断之前，避免「净利润」被误判为收入。
        if (
            "归属于上市公司股东的净利润" in line
            or "归母净利润" in line
            or "归属于母公司所有者的净利润" in line
            or "归属于母公司股东的净利润" in line
        ):
            return "net_profit_parent", None
        if "扣非净利润" in line or "扣除非经常性损益" in line:
            return "net_profit_deducted", None
        if "净利润" in line:
            return "net_profit", None
        if "营业总收入" in line:
            return "revenue", None
        if "营业收入" in line or "收入" in line or "销售收入" in line:
            if "营业收入" in line and "合计" in line:
                return "revenue", None
            segment = self._dimension_before_value(line)
            if segment and segment not in {
                "收入", "销售收入", "营业收入", "主营业务收入", "营业",
            }:
                if self._is_noisy_dimension(segment):
                    return None
                return "revenue_segment", segment
            return "revenue", None
        if "产能" in line:
            return "production_capacity", self._dimension_before_keyword(line, ("产能状况", "产能"))
        if "产量" in line:
            return "production_volume", self._dimension_before_keyword(line, ("快报产量", "产量"))
        if "销量" in line:
            return "sales_volume", self._dimension_before_keyword(line, ("快报销量", "销量"))
        return None

    def _should_skip_value(
        self, *, metric_base: str, dimension: str | None, value: str, line: str
    ) -> bool:
        if metric_base == "revenue_segment" and self._is_noisy_dimension(dimension or ""):
            return True
        return is_implausible_value(metric_base, value, line=line)

    def _is_noisy_dimension(self, dimension: str) -> bool:
        cleaned = dimension.strip()
        if cleaned.startswith(("其中", "加", "减", "一、", "二、", "三、", "四、")):
            return True
        return any(token in cleaned for token in _NOISY_DIMENSION_TOKENS)

    def _dimension_before_value(self, line: str) -> str | None:
        value_match = re.search(INLINE_VALUE_UNIT_PATTERN, line) or re.search(
            _BARE_NUMBER_PATTERN, line
        )
        prefix = line[: value_match.start()] if value_match else line
        prefix = re.sub(YEAR_PATTERN, "", prefix)
        prefix = re.sub(r"单位[:：]?.*$", "", prefix)
        # 剥离数值 token：「营业收入 391 32.5%」的前缀里不能残留金额，
        # 否则「营业 391」会被误判为业务分部维度。
        prefix = re.sub(_BARE_NUMBER_PATTERN, "", prefix)
        prefix = prefix.replace("销售收入", "").replace("收入", "")
        return clean_dimension(prefix)

    def _dimension_before_keyword(self, line: str, keywords: tuple[str, ...]) -> str | None:
        positions = [line.find(k) for k in keywords if k in line]
        prefix = line[: min(positions)] if positions else line
        prefix = re.sub(YEAR_PATTERN, "", prefix)
        return clean_dimension(prefix)

    def _metric_name(self, metric_base: str, dimension: str | None) -> str:
        if metric_base not in {
            "revenue_segment", "production_capacity", "production_volume", "sales_volume",
        }:
            return metric_base
        cleaned = clean_dimension(dimension or "")
        return f"{metric_base}:{cleaned}" if cleaned else metric_base

    def _claim_label(self, metric_base: str, metric_name: str) -> str:
        label = METRIC_LABELS.get(metric_base, metric_base)
        if ":" not in metric_name:
            return label
        dimension = metric_name.split(":", 1)[1]
        return f"{dimension}{label}"
