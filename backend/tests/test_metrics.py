"""Tests for metric families, name normalization, and unit normalization.

These are the comparability guarantees the future contradiction detector
relies on: accounting boundaries stay distinct, unit variants of the same
figure collapse to one comparable key.
"""
from __future__ import annotations

from decimal import Decimal

from app.domain.metrics import (
    FactValueNormalizer,
    MetricNameNormalizer,
    MetricRegistry,
)


# ---------------------------------------------------------------------------
# Metric families (AI-compute slice)
# ---------------------------------------------------------------------------


def test_detect_families_ai_compute_text():
    registry = MetricRegistry()
    families = registry.detect_families("数据中心 GPU 出货量与交付量持续爬坡")
    assert "compute_capacity" in families


def test_detect_families_revenue_structure_wins_over_revenue():
    registry = MetricRegistry()
    families = registry.detect_families("分业务收入结构变化")
    assert "revenue_structure" in families
    assert "revenue" not in families


def test_detect_families_valuation():
    registry = MetricRegistry()
    assert "valuation" in registry.detect_families("当前市盈率约 300 倍")


def test_preferred_metric():
    registry = MetricRegistry()
    assert registry.preferred_metric("profit") == "net_profit_parent"
    assert registry.preferred_metric("unknown") is None


# ---------------------------------------------------------------------------
# Metric name normalization
# ---------------------------------------------------------------------------


def test_aliases_collapse_to_canonical_names():
    normalizer = MetricNameNormalizer()
    assert normalizer.comparable_key("研发费用") == "R&D_expenditure"
    assert normalizer.comparable_key("rd_expense") == "R&D_expenditure"
    assert normalizer.comparable_key("归母净利润") == "net_profit_parent"
    assert normalizer.comparable_key("市盈率") == "pe_ttm"
    assert normalizer.comparable_key("算力规模") == "compute_capacity"


def test_accounting_boundaries_stay_distinct():
    normalizer = MetricNameNormalizer()
    keys = {
        normalizer.comparable_key("净利润"),
        normalizer.comparable_key("归母净利润"),
        normalizer.comparable_key("扣非净利润"),
    }
    assert keys == {"net_profit", "net_profit_parent", "net_profit_deducted"}


def test_dimensional_metrics_preserve_normalized_dimension():
    normalizer = MetricNameNormalizer()
    a = normalizer.comparable_key("revenue_segment:云计算")
    b = normalizer.comparable_key("revenue_segment:云 计算")
    c = normalizer.comparable_key("revenue_segment:存储")
    assert a == b == "revenue_segment:云计算"
    assert c != a


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def test_money_units_collapse_to_yuan():
    normalizer = FactValueNormalizer()
    assert normalizer.comparable_key("5亿元") == normalizer.comparable_key("50000万元")
    assert normalizer.comparable_key("391亿") == normalizer.comparable_key("391亿元")
    n = normalizer.normalize("50,000万元")
    assert n is not None and n.value == Decimal("500000000") and n.kind == "money"


def test_non_money_kinds():
    normalizer = FactValueNormalizer()
    energy = normalizer.normalize("10GWh")
    assert energy is not None and energy.kind == "energy" and energy.value == 10000
    ratio = normalizer.normalize("95%")
    assert ratio is not None and ratio.kind == "ratio"
    units = normalizer.normalize("2万台")
    assert units is not None and units.kind == "unit" and units.value == 20000


def test_false_conflict_eliminated():
    normalizer = FactValueNormalizer()
    # 同一数字的不同单位写法必须得到同一个比较键，否则矛盾检测全是假冲突
    assert normalizer.comparable_key("营收 391亿元".replace("营收 ", "")) == (
        normalizer.comparable_key("39,100,000,000元")
    )


def test_unparseable_values_fall_back_to_cleaned_raw():
    normalizer = FactValueNormalizer()
    assert normalizer.normalize("大量") is None
    assert normalizer.comparable_key("约 40 亿美元".replace("约 ", ""))
