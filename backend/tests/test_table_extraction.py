"""Tests for rule-based financial table extraction and its pipeline routing.

Samples mimic Chinese annual-report table fragments: header year rows,
unit declaration lines, metric rows, narrative lines that must NOT be
treated as tables, and classic misparse traps (percentages, section
numbers, noisy dimensions, cumulative figures).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.ai.client import LLMClient
from app.ai.extraction import StatementExtractor
from app.models.ledger import AIRun
from app.services.table_extraction import (
    FinancialTableExtractor,
    is_implausible_value,
    normalize_money_to_yuan,
)

TABLE_SNIPPET = """主要会计数据 单位：千元
指标 2025年 2024年
营业收入 50,000,000 40,000,000
归属于上市公司股东的净利润 8,600,000 7,200,000
扣非净利润 8,100,000 6,900,000
研发费用 3,000,000 2,500,000
"""


def _extract(text):
    return FinancialTableExtractor().extract(text)


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def test_normalize_money_to_yuan():
    assert normalize_money_to_yuan("391亿元") == 391 * 10**8
    assert normalize_money_to_yuan("50,000万元") == 50_000 * 10**4
    assert normalize_money_to_yuan("634亿") == 634 * 10**8
    assert normalize_money_to_yuan("45%") is None
    assert normalize_money_to_yuan("") is None


# ---------------------------------------------------------------------------
# Plausibility guards
# ---------------------------------------------------------------------------


def test_implausible_rejects_percent_for_money_metrics():
    assert is_implausible_value("revenue", "32.5%", line="营业收入 391亿元 32.5%")
    assert not is_implausible_value("production_volume", "95%", line="产能利用率 95%")


def test_implausible_rejects_small_money_values():
    assert is_implausible_value("R&D_expenditure", "50万元", line="研发费用 50万元")
    assert not is_implausible_value("R&D_expenditure", "3亿元", line="研发费用 3亿元")


def test_implausible_rejects_cumulative_rd():
    assert is_implausible_value(
        "R&D_expenditure", "10亿元", line="累计三年研发投入合计 10亿元"
    )


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------


def test_extracts_multi_year_metrics_with_periods():
    facts = _extract(TABLE_SNIPPET)
    texts = {f.statement_text for f in facts}
    assert "2025年营业收入为50000000千元" in texts
    assert "2024年营业收入为40000000千元" in texts
    assert "2025年归母净利润为8600000千元" in texts
    assert "2025年扣非净利润为8100000千元" in texts
    assert "2025年研发费用为3000000千元" in texts
    periods = {f.observed_period for f in facts}
    assert date(2025, 12, 31) in periods
    assert date(2024, 12, 31) in periods


def test_profit_metrics_keep_accounting_boundaries():
    facts = _extract(
        "单位：亿元\n指标 2025年\n净利润 100\n归属于上市公司股东的净利润 90\n扣非净利润 85\n"
    )
    metrics = {f.metric_name for f in facts}
    assert metrics == {"net_profit", "net_profit_parent", "net_profit_deducted"}


def test_narrative_sentences_are_not_tables():
    assert _extract("公司管理层表示，未来三年算力需求将保持高速增长。") == []
    assert _extract("4、研发投入") == []


def test_noisy_dimension_rows_are_skipped():
    facts = _extract(
        "单位：亿元\n指标 2025年\n营业收入 391\n其中：增值税 20\n"
    )
    assert all("增值税" not in f.statement_text for f in facts)


def test_revenue_segment_keeps_dimension():
    facts = _extract(
        "单位：亿元\n指标 2025年\n云计算收入 120\n"
    )
    assert any(
        f.metric_name.startswith("revenue_segment:") and "云计算" in f.statement_text
        for f in facts
    )


def test_percent_values_not_admitted_for_money_metrics():
    facts = _extract(
        "单位：亿元\n指标 2025年\n营业收入 391 32.5%\n"
    )
    revenue_facts = [f for f in facts if f.metric_name == "revenue"]
    assert len(revenue_facts) == 1
    assert revenue_facts[0].statement_text.endswith("391亿元")


# ---------------------------------------------------------------------------
# Pipeline routing: table spans -> rules, narrative spans -> LLM
# ---------------------------------------------------------------------------


def test_extractor_routes_table_spans_to_rules(
    session, document_service, document
):
    table_span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=TABLE_SNIPPET,
    )
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 2},
        verbatim_text="管理层表示订单能见度良好",
    )
    client = LLMClient(model_version="mock-test", mock=True)
    statements = StatementExtractor(client).extract(document.id, session)

    rule_based = [s for s in statements if s.source_span_id == table_span.id]
    assert len(rule_based) >= 8  # 4 个指标 × 2 年
    assert all(s.kind == "disclosed_fact" for s in rule_based)
    assert any(s.observed_period == date(2025, 12, 31) for s in rule_based)

    run = session.scalars(select(AIRun).where(AIRun.kind == "extract")).one()
    assert "rule-based" in run.output_summary


def test_extractor_skips_llm_when_all_spans_are_tables(
    session, document_service, document
):
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=TABLE_SNIPPET,
    )

    class _FailIfCalled(LLMClient):
        def chat_json(self, messages, schema_hint=""):  # pragma: no cover
            raise AssertionError("LLM must not be called for table-only spans")

    client = _FailIfCalled(model_version="mock-test", mock=True)
    statements = StatementExtractor(client).extract(document.id, session)
    assert statements

    run = session.scalars(select(AIRun).where(AIRun.kind == "extract")).one()
    assert run.status == "success"


def test_extractor_llm_still_handles_narrative_spans(
    session, document_service, document
):
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="管理层表示订单能见度良好，预计明年交付量将增长",
    )
    client = LLMClient(model_version="mock-test", mock=True)
    statements = StatementExtractor(client).extract(document.id, session)
    assert len(statements) == 1
    assert statements[0].kind in {
        "management_attribution", "forecast",
    }
