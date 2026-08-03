"""Acceptance tests for auditable frozen Cambricon profitability observations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import shutil

import pytest

from app.scripts import cambricon_profitability_data as case_data_module
from app.scripts.cambricon_profitability_data import load_case_data


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "cambricon_profitability_case"
)


def test_case_data_derives_exact_quarters_from_official_2025_report() -> None:
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
    assert data.annual_report.source_url.startswith("https://dataclouds.cninfo.com.cn/")
    assert data.annual_report.page == 10
    assert data.annual_report.verbatim_text.strip()
    assert data.annual_report.verbatim_text.lstrip().startswith("中科寒武纪科技股份有限公司2025")
    assert "[ROW" not in data.annual_report.verbatim_text


def test_juyuan_is_only_rounded_yi_corroboration_of_the_official_parent_profit() -> None:
    data = load_case_data()

    assert data.juyuan.provider == "gildata-juyuan"
    assert data.juyuan.tool == "FinQuery"
    assert datetime.fromisoformat(data.juyuan.fetched_at).tzinfo is not None
    assert data.juyuan.query == "寒武纪688256 2025年各季度归母净利润、扣非归母净利润"
    assert data.juyuan.raw_response.strip()
    assert data.juyuan.rounded_parent_profit_yi == {
        "2025Q1": Decimal("3.55"),
        "2025H1": Decimal("10.38"),
        "2025Q1-Q3": Decimal("16.05"),
        "2025FY": Decimal("20.59"),
    }
    official_cumulative = {
        "2025Q1": data.single_quarter_parent_profit["2025Q1"],
        "2025H1": sum(data.single_quarter_parent_profit[quarter] for quarter in ("2025Q1", "2025Q2")),
        "2025Q1-Q3": sum(data.single_quarter_parent_profit[quarter] for quarter in ("2025Q1", "2025Q2", "2025Q3")),
        "2025FY": sum(data.single_quarter_parent_profit.values()),
    }
    assert {
        period: (value / Decimal("100000000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for period, value in official_cumulative.items()
    } == data.juyuan.rounded_parent_profit_yi


def test_2024_q4_parent_profit_has_a_separate_official_verbatim_source() -> None:
    data = load_case_data()

    assert data.parent_profit_2024_q4 == Decimal("272152952.65")
    assert data.annual_report_2024.page == 11
    assert data.annual_report_2024.source_url.startswith("https://big5.sse.com.cn/")
    assert data.annual_report_2024.parent_profit["2024Q4"] == data.parent_profit_2024_q4
    assert "272,152,952.65" in data.annual_report_2024.verbatim_text
    assert "[ROW" not in data.annual_report_2024.verbatim_text
    assert all(value > 0 for value in [data.parent_profit_2024_q4, *data.single_quarter_parent_profit.values()])


def _copy_fixture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copied = tmp_path / "cambricon_profitability_case"
    shutil.copytree(FIXTURE_DIR, copied)
    monkeypatch.setattr(case_data_module, "_FIXTURE_DIR", copied)
    return copied


def _replace_raw_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / "juyuan_finquery_2026-08-03.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["raw_response"] = replacement
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(
        case_data_module,
        "_JUYUAN_RAW_RESPONSE_SHA256",
        hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
    )


@pytest.mark.parametrize(
    "replacement",
    [
        "not json",
        '{"code":"0","results":[]}',
        '{"code":"0","results":[{"api_name":"财务报表","table_markdown":"|股票名称|\\n|---|\\n|错误证券|"}]}',
    ],
)
def test_loader_rejects_malformed_empty_or_wrong_security_juyuan_raw_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    _replace_raw_response(tmp_path, monkeypatch, replacement)

    with pytest.raises(ValueError, match="Juyuan"):
        load_case_data()


@pytest.mark.parametrize(
    "old,new",
    [
        ("归属于母公司所有者的净利润", "错误指标"),
        ("2025-12-31", "2025-12-30"),
        ("亿元", "万元"),
        ("20.59", "20.58"),
    ],
)
def test_loader_rejects_wrong_juyuan_metric_period_unit_or_normalized_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / "juyuan_finquery_2026-08-03.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["raw_response"] = payload["raw_response"].replace(old, new)
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(
        case_data_module,
        "_JUYUAN_RAW_RESPONSE_SHA256",
        hashlib.sha256(payload["raw_response"].encode("utf-8")).hexdigest(),
    )

    with pytest.raises(ValueError, match="Juyuan"):
        load_case_data()


@pytest.mark.parametrize("break_query", [True, False])
def test_loader_rejects_wrong_supporting_query_or_adjusted_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    break_query: bool,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / "juyuan_finquery_2026-08-03.json"
    payload = json.loads(path.read_text("utf-8"))
    if break_query:
        payload["query"] = "寒武纪688256 2025年利润"
    else:
        payload["raw_response"] = payload["raw_response"].replace(
            "扣除非经常损益后的归母净利润", "错误扣非指标"
        )
        monkeypatch.setattr(
            case_data_module,
            "_JUYUAN_RAW_RESPONSE_SHA256",
            hashlib.sha256(payload["raw_response"].encode("utf-8")).hexdigest(),
        )
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    with pytest.raises(ValueError, match="Juyuan"):
        load_case_data()


@pytest.mark.parametrize(
    "old,new",
    [
        ("275962803.95", "275962803.96"),
        ("912566847.07", "912566847.08"),
        ("1418887977.3", "1418887977.4"),
        ("1769934157.68", "1769934157.69"),
    ],
)
def test_loader_rejects_any_juyuan_adjusted_profit_value_mismatching_cninfo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / "juyuan_finquery_2026-08-03.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["raw_response"] = payload["raw_response"].replace(old, new, 1)
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(
        case_data_module,
        "_JUYUAN_RAW_RESPONSE_SHA256",
        hashlib.sha256(payload["raw_response"].encode("utf-8")).hexdigest(),
    )

    with pytest.raises(ValueError, match="Juyuan adjusted profit"):
        load_case_data()


@pytest.mark.parametrize(
    "filename",
    [
        "cninfo_2025_annual_report_page_10.txt",
        "sse_2024_annual_report_page_11.txt",
    ],
)
def test_loader_rejects_frozen_source_excerpt_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / filename
    path.write_text(path.read_text("utf-8") + "\nsource drift\n", "utf-8")

    with pytest.raises(ValueError, match="fixture integrity"):
        load_case_data()


def test_loader_rejects_juyuan_raw_response_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / "juyuan_finquery_2026-08-03.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["raw_response"] += "\n"
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    with pytest.raises(ValueError, match="fixture integrity"):
        load_case_data()


@pytest.mark.parametrize(
    "filename,field,replacement",
    [
        (
            "cninfo_2025_annual_report_page_10.provenance.json",
            "title",
            "另一家公司2025年年度报告",
        ),
        (
            "cninfo_2025_annual_report_page_10.provenance.json",
            "source_url",
            "https://dataclouds.cninfo.com.cn/unrelated.pdf",
        ),
        (
            "cninfo_2025_annual_report_page_10.provenance.json",
            "published_at",
            "2099-01-01T00:00:00+08:00",
        ),
        (
            "sse_2024_annual_report_page_11.provenance.json",
            "title",
            "另一家公司2024年年度报告",
        ),
        (
            "sse_2024_annual_report_page_11.provenance.json",
            "source_url",
            "https://big5.sse.com.cn/unrelated.pdf",
        ),
        (
            "sse_2024_annual_report_page_11.provenance.json",
            "published_at",
            "2099-01-01T00:00:00+08:00",
        ),
    ],
)
def test_loader_rejects_exact_provenance_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    field: str,
    replacement: str,
) -> None:
    copied = _copy_fixture_dir(tmp_path, monkeypatch)
    path = copied / filename
    payload = json.loads(path.read_text("utf-8"))
    payload[field] = replacement
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    with pytest.raises(ValueError, match="provenance"):
        load_case_data()


@pytest.mark.parametrize(
    "filename,year,old,new",
    [
        ("cninfo_2025_annual_report_page_10.txt", 2025, "单位：元", "单位：万元"),
        ("sse_2024_annual_report_page_11.txt", 2024, "单位：元", "单位：万元"),
        ("cninfo_2025_annual_report_page_10.txt", 2025, "币种：人民币", "币种：美元"),
        ("sse_2024_annual_report_page_11.txt", 2024, "币种：人民币", "币种：美元"),
        ("cninfo_2025_annual_report_page_10.txt", 2025, "第一季度", "第四季度"),
        ("sse_2024_annual_report_page_11.txt", 2024, "第一季度", "第四季度"),
    ],
)
def test_official_parser_rejects_wrong_unit_currency_or_quarter_header(
    filename: str,
    year: int,
    old: str,
    new: str,
) -> None:
    text = (FIXTURE_DIR / filename).read_text("utf-8").replace(old, new, 1)
    validator = getattr(case_data_module, "_require_quarter_table_context", None)
    assert callable(validator), "official parser must expose its table-context validator"

    with pytest.raises(ValueError, match="table context"):
        validator(text, year=year)


def test_loaded_money_mappings_are_immutable_and_still_support_calculations() -> None:
    data = load_case_data()

    with pytest.raises(TypeError):
        data.single_quarter_parent_profit["2025Q1"] = Decimal("0")
    with pytest.raises(TypeError):
        data.annual_report.parent_profit["2025Q1"] = Decimal("0")
    with pytest.raises(TypeError):
        data.juyuan.rounded_parent_profit_yi["2025Q1"] = Decimal("0")
    assert sum(data.single_quarter_parent_profit.values()) == Decimal("2059228538.67")
