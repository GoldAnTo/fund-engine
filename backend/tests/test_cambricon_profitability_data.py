"""Acceptance tests for the frozen Cambricon profitability observations."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.scripts.cambricon_profitability_data import load_case_data


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "cambricon_profitability_case"
)


def test_case_data_derives_exact_quarters_and_reconciles_annual_totals() -> None:
    data = load_case_data()

    assert data.single_quarter_parent_profit == {
        "2025Q1": Decimal("355465241.04"),
        "2025Q2": Decimal("682617327.53"),
        "2025Q3": Decimal("566563175.54"),
        "2025Q4": Decimal("454582794.56"),
    }
    assert sum(data.single_quarter_parent_profit.values()) == Decimal("2059228538.67")
    assert sum(data.single_quarter_adjusted_profit.values()) == Decimal("1769934157.68")
    assert sum(data.single_quarter_operating_cash_flow.values()) == Decimal("-498398137.01")


def test_case_data_supports_five_consecutive_positive_parent_profit_quarters() -> None:
    data = load_case_data()

    assert data.parent_profit_2024_q4 == Decimal("272152952.65")
    series = [data.parent_profit_2024_q4, *data.single_quarter_parent_profit.values()]
    assert len(series) == 5
    assert all(value > 0 for value in series)


def test_case_data_preserves_verbatim_source_payloads_and_reconciles_sources() -> None:
    data = load_case_data()
    payload = json.loads((FIXTURE_DIR / "juyuan_finquery_2026-08-03.json").read_text("utf-8"))

    assert data.juyuan.provider == "gildata-juyuan"
    assert data.juyuan.tool == "FinQuery"
    assert datetime.fromisoformat(data.juyuan.fetched_at).tzinfo is not None
    assert data.juyuan.queries == (
        "寒武纪 688256 2025年各报告期归属于母公司股东的净利润",
        "寒武纪 688256 2025年各报告期扣除非经常性损益后的归母净利润",
    )
    assert data.juyuan.raw_responses == tuple(payload["raw_responses"])
    assert all(response.strip() for response in data.juyuan.raw_responses)
    assert data.annual_report.source_url.startswith("https://dataclouds.cninfo.com.cn/")
    assert data.annual_report.page == 10
    assert data.annual_report.verbatim_text.strip()
    assert data.single_quarter_parent_profit == data.annual_report.parent_profit
    assert data.single_quarter_adjusted_profit == data.annual_report.adjusted_profit
