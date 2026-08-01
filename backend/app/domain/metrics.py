"""Metric families, name normalization, and unit normalization.

This is the comparability foundation for contradiction detection ("信息有
左有右"): two statements about "营收 5 亿" and "营收 50000 万元" must
normalize to the same comparable key before any support/contradict
judgment, otherwise the detector reports fake conflicts.  Accounting
boundaries are preserved as first-class distinctions — 归母净利润, 净利润
and 扣非净利润 are different metrics, not aliases of one another.

Structure ported from the Verifiable-Company-Research-Agent
``domain/metric_registry.py`` / ``services/fact_metric_normalization.py`` /
``services/fact_value_normalization.py`` (MIT); family contents are rebuilt
for the AI-compute evidence slice (compute capacity, valuation) rather than
the vehicle-manufacturer sample.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache


# ---------------------------------------------------------------------------
# Metric families
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricFamily:
    id: str
    base_metrics: tuple[str, ...]
    intent_tokens: tuple[str, ...]
    claim_tokens: tuple[str, ...]
    preferred_metric_for_family: str
    unit_families: tuple[str, ...]


_DEFAULT_FAMILIES: tuple[MetricFamily, ...] = (
    MetricFamily(
        id="rd",
        base_metrics=("R&D_expenditure", "R&D_total_spending"),
        intent_tokens=("研发", "r&d", "rd", "research", "研发费用", "研发投入"),
        claim_tokens=("研发", "研究开发", "研发费用", "研发投入"),
        preferred_metric_for_family="R&D_total_spending",
        unit_families=("money",),
    ),
    MetricFamily(
        id="profit",
        base_metrics=("net_profit", "net_profit_parent", "net_profit_deducted"),
        intent_tokens=("利润", "净利润", "归母", "扣非", "profit"),
        claim_tokens=("利润", "净利润", "归母", "扣非"),
        preferred_metric_for_family="net_profit_parent",
        unit_families=("money",),
    ),
    MetricFamily(
        id="revenue",
        base_metrics=("revenue", "operating_revenue", "sales_revenue"),
        intent_tokens=("收入", "营收", "营业收入", "revenue"),
        claim_tokens=("收入", "营收", "营业收入"),
        preferred_metric_for_family="revenue",
        unit_families=("money",),
    ),
    MetricFamily(
        id="revenue_structure",
        base_metrics=("revenue_segment", "segment_revenue"),
        intent_tokens=("收入结构", "营收结构", "分业务", "业务结构", "收入构成"),
        claim_tokens=("收入结构", "分业务", "分产品", "构成"),
        preferred_metric_for_family="revenue_segment",
        unit_families=("money", "ratio"),
    ),
    MetricFamily(
        id="compute_capacity",
        base_metrics=(
            "production_capacity", "production_volume", "sales_volume",
            "compute_capacity", "shipments", "deliveries",
        ),
        intent_tokens=(
            "算力", "产能", "产量", "销量", "出货量", "交付量", "机柜",
            "数据中心", "服务器", "gpu", "capacity", "production", "shipment",
        ),
        claim_tokens=(
            "算力", "产能", "产量", "销量", "出货", "交付", "机柜",
            "数据中心", "服务器",
        ),
        preferred_metric_for_family="compute_capacity",
        unit_families=("unit", "energy", "count"),
    ),
    MetricFamily(
        id="valuation",
        base_metrics=("pe_ttm", "pb", "ps", "market_cap"),
        intent_tokens=(
            "估值", "市盈率", "市净率", "市销率", "市值", "pe", "pb", "ps",
        ),
        claim_tokens=("估值", "市盈率", "市净率", "市值", "倍"),
        preferred_metric_for_family="pe_ttm",
        unit_families=("ratio", "money"),
    ),
    MetricFamily(
        id="business",
        base_metrics=("business", "industry", "operation_scope"),
        intent_tokens=(
            "主要业务", "主营业务", "业务板块", "业务范围", "经营范围",
            "产品服务", "business",
        ),
        claim_tokens=("主营", "业务", "经营范围", "产品", "服务"),
        preferred_metric_for_family="business",
        unit_families=("text",),
    ),
    MetricFamily(
        id="risk",
        base_metrics=("risk", "uncertainty"),
        intent_tokens=("风险", "不确定", "risk"),
        claim_tokens=("风险", "不确定", "波动"),
        preferred_metric_for_family="risk",
        unit_families=("text",),
    ),
)


class MetricRegistry:
    """Single source for metric-family matching used by intent and answer code."""

    def __init__(self, families: tuple[MetricFamily, ...] = _DEFAULT_FAMILIES) -> None:
        self._families = {family.id: family for family in families}

    @property
    def families(self) -> dict[str, MetricFamily]:
        return dict(self._families)

    def get(self, family_id: str) -> MetricFamily | None:
        return self._families.get(family_id)

    def detect_families(self, text: str) -> frozenset[str]:
        value = (text or "").lower()
        matches = {
            family.id
            for family in self._families.values()
            if any(token.lower() in value for token in family.intent_tokens)
        }
        # 更具体的族优先：命中收入结构时不再按通用收入族处理。
        if "revenue_structure" in matches:
            matches.discard("revenue")
        return frozenset(matches)

    def matches_family(
        self,
        *,
        metric_name: str | None,
        claim: str,
        family_ids: frozenset[str] | set[str],
    ) -> bool:
        if not family_ids:
            return True
        metric = (metric_name or "").lower()
        claim_l = (claim or "").lower()
        for family_id in family_ids:
            family = self._families.get(family_id)
            if family is None:
                continue
            tokens = family.base_metrics + family.claim_tokens
            if any(token.lower() in metric or token.lower() in claim_l for token in tokens):
                return True
        return False

    def preferred_metric(self, family_id: str) -> str | None:
        family = self._families.get(family_id)
        return family.preferred_metric_for_family if family else None


@lru_cache
def get_metric_registry() -> MetricRegistry:
    return MetricRegistry()


# ---------------------------------------------------------------------------
# Metric name normalization (aliases -> canonical, boundaries preserved)
# ---------------------------------------------------------------------------

_DIMENSIONAL_METRICS = {
    "revenue_segment",
    "production_capacity",
    "production_volume",
    "sales_volume",
    "compute_capacity",
}

_ALIASES = {
    # 研发
    "r&d_expenditure": "R&D_expenditure",
    "r_and_d": "R&D_expenditure",
    "rd": "R&D_expenditure",
    "rd_expense": "R&D_expenditure",
    "research_expense": "R&D_expenditure",
    "research_expenditure": "R&D_expenditure",
    "研发费用": "R&D_expenditure",
    "r&d_total_spending": "R&D_total_spending",
    "rd_total": "R&D_total_spending",
    "rd_spending": "R&D_total_spending",
    "研发投入": "R&D_total_spending",
    "研发投入合计": "R&D_total_spending",
    # 收入
    "rev": "revenue",
    "operating_revenue": "revenue",
    "营业收入": "revenue",
    "营业总收入": "revenue",
    "营收": "revenue",
    # 利润（会计边界各自独立，互不合并）
    "np": "net_profit",
    "净利润": "net_profit",
    "parent_net_profit": "net_profit_parent",
    "归母净利润": "net_profit_parent",
    "归属于上市公司股东的净利润": "net_profit_parent",
    "deducted_net_profit": "net_profit_deducted",
    "扣非净利润": "net_profit_deducted",
    "扣非归母净利润": "net_profit_deducted",
    # 算力/产能
    "算力": "compute_capacity",
    "算力规模": "compute_capacity",
    "出货量": "shipments",
    "交付量": "deliveries",
    # 估值
    "pe": "pe_ttm",
    "pe_ttm": "pe_ttm",
    "市盈率": "pe_ttm",
    "市盈率ttm": "pe_ttm",
    "pb": "pb",
    "市净率": "pb",
    "ps": "ps",
    "市销率": "ps",
    "市值": "market_cap",
    "market_cap": "market_cap",
}


class MetricNameNormalizer:
    """Build stable metric keys while preserving accounting boundaries."""

    def comparable_key(self, metric_name: str | None) -> str:
        if not metric_name:
            return ""
        base, dimension = self._split_dimension(metric_name)
        normalized_base = self._normalize_base(base)
        if normalized_base in _DIMENSIONAL_METRICS and dimension:
            return f"{normalized_base}:{self._normalize_dimension(dimension)}"
        return normalized_base

    def _split_dimension(self, metric_name: str) -> tuple[str, str | None]:
        if ":" not in metric_name:
            return metric_name, None
        base, dimension = metric_name.split(":", 1)
        return base, dimension

    def _normalize_base(self, metric_name: str) -> str:
        key = metric_name.strip().lower()
        key = key.replace(" ", "_").replace("-", "_")
        key = re.sub(r"_+", "_", key)
        return _ALIASES.get(key, metric_name.strip())

    def _normalize_dimension(self, dimension: str) -> str:
        cleaned = dimension.strip().lower()
        cleaned = re.sub(r"[\s　]+", "", cleaned)
        return cleaned.replace("（", "(").replace("）", ")")


# ---------------------------------------------------------------------------
# Value normalization (money -> yuan, energy -> MWh, etc.)
# ---------------------------------------------------------------------------

_NUMBER_UNIT_RE = re.compile(
    r"(?P<number>[-+]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>亿元|万元|千元|万辆|万台|万吨|GWh|MWh|元|亿|万|%|辆|台|吨)",
    re.IGNORECASE,
)

_MONEY_FACTORS = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
    "万": Decimal("10000"),
    "亿": Decimal("100000000"),
}
_COUNT_FACTORS = {
    "辆": ("vehicle", Decimal("1")),
    "万辆": ("vehicle", Decimal("10000")),
    "台": ("unit", Decimal("1")),
    "万台": ("unit", Decimal("10000")),
    "吨": ("ton", Decimal("1")),
    "万吨": ("ton", Decimal("10000")),
}
_ENERGY_FACTORS = {
    "MWh": Decimal("1"),
    "GWh": Decimal("1000"),
}


@dataclass(frozen=True, slots=True)
class NormalizedValue:
    """Comparable representation of a numeric value.

    The raw string stays on the statement/citation; this object exists only
    to eliminate false conflicts caused by unit differences.
    """

    kind: str
    value: Decimal
    unit: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{format(self.value.normalize(), 'f')}:{self.unit}"


class FactValueNormalizer:
    """Convert common financial-report units into stable comparison keys."""

    def comparable_key(self, value: str | None) -> str:
        if not value:
            return ""
        normalized = self.normalize(value)
        if normalized is not None:
            return normalized.key
        return value.replace(" ", "").replace(",", "").strip().lower()

    def normalize(self, value: str | None) -> NormalizedValue | None:
        if not value:
            return None
        match = _NUMBER_UNIT_RE.search(value.replace(" ", ""))
        if match is None:
            return None
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            return None

        unit = self._canonical_unit(match.group("unit"))
        if unit in _MONEY_FACTORS:
            return NormalizedValue(
                kind="money", value=number * _MONEY_FACTORS[unit], unit="yuan"
            )
        if unit in _COUNT_FACTORS:
            kind, factor = _COUNT_FACTORS[unit]
            return NormalizedValue(kind=kind, value=number * factor, unit=kind)
        if unit in _ENERGY_FACTORS:
            return NormalizedValue(
                kind="energy", value=number * _ENERGY_FACTORS[unit], unit="MWh"
            )
        if unit == "%":
            return NormalizedValue(kind="ratio", value=number, unit="percent")
        return None

    def _canonical_unit(self, unit: str) -> str:
        upper = unit.upper()
        if upper in {"GWH", "MWH"}:
            return upper.replace("WH", "Wh")
        return unit
