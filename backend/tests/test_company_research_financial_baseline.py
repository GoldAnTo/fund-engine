from __future__ import annotations

import importlib
import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.hashing import canonical_hash

CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _module():
    name = "app.underwriting.services.company_research_financial_baseline"
    assert importlib.util.find_spec(name) is not None, "financial baseline is not implemented"
    return importlib.import_module(name)


def _facts(payload):
    return {fact["fact_key"]: fact for fact in payload["facts"]}


def test_real_baseline_exposes_period_and_scope_specific_history():
    value = _module().load_alphabet_financial_baseline(CUTOFF)
    assert set(value) == {"schema_version", "content_hash", "sources", "facts", "research_gaps"}
    assert value["schema_version"] == "company-research.financial-baseline.v1"
    assert value["content_hash"] == canonical_hash({k: v for k, v in value.items() if k != "content_hash"})
    assert len(value["sources"]) == 4
    facts = _facts(value)
    expected = {
        "fy2025_group_revenue": "402836",
        "fy2025_group_operating_income": "129039",
        "fy2025_group_cfo": "164713",
        "fy2025_group_ppe_depreciation": "21136",
        "fy2025_group_capex": "91447",
        "fy2025_group_sbc_cashflow_adjustment": "24953",
        "fy2025_group_share_repurchases_cash": "45709",
        "fy2025_group_diluted_weighted_average_shares": "12230",
        "h1_2025_group_revenue": "186662",
        "h1_2026_group_revenue": "229692",
        "h1_2026_group_operating_income": "80466",
        "h1_2026_group_cfo": "84859",
        "h1_2026_group_capex": "80598",
        "q2_2026_group_revenue": "119796",
        "q2_2026_group_operating_income": "40770",
        "q2_2026_group_cfo": "39069",
        "q2_2026_group_capex": "44924",
        "q2_2026_google_cloud_revenue": "24768",
        "q2_2026_google_cloud_operating_income": "8814",
    }
    for key, amount in expected.items():
        assert facts[key]["value"] == amount
        assert facts[key]["state"] == "reported"
    assert facts["h1_2026_group_revenue"]["period_start"] == "2026-01-01"
    assert facts["h1_2026_group_revenue"]["period_end"] == "2026-06-30"
    assert facts["q2_2026_group_revenue"]["period_start"] == "2026-04-01"
    assert facts["q2_2026_google_cloud_revenue"]["scope"] == "google_cloud"
    assert "利润表口径，非现金实缴税额" in facts["fy2025_group_tax_expense"]["label"]


def test_derived_results_preserve_negative_cash_flow_zero_buybacks_and_exact_inputs():
    facts = _facts(_module().load_alphabet_financial_baseline(CUTOFF))
    assert facts["fy2025_group_free_cash_flow"]["value"] == "73266"
    assert facts["h1_2026_group_free_cash_flow"]["value"] == "4261"
    assert facts["q2_2026_group_free_cash_flow"]["value"] == "-5855"
    assert facts["h1_2026_group_share_repurchases_cash"]["value"] == "0"
    assert facts["q2_2026_group_share_repurchases_cash"]["value"] == "0"
    for period in ("fy2025", "h1_2026", "q2_2026"):
        for scope in ("group", "google_cloud"):
            margin = facts[f"{period}_{scope}_operating_margin"]
            numerator = facts[f"{period}_{scope}_operating_income"]
            denominator = facts[f"{period}_{scope}_revenue"]
            assert Decimal(margin["value"]) == Decimal(numerator["value"]) / Decimal(denominator["value"])
            assert margin["state"] == "derived" and margin["unit"] == "ratio"
            assert margin["input_fact_keys"] == [numerator["fact_key"], denominator["fact_key"]]
    growth = facts["q2_2026_google_cloud_revenue_growth_yoy"]
    assert Decimal(growth["value"]) == Decimal(24768) / Decimal(13624) - 1


@pytest.mark.parametrize("cutoff", [datetime(2026, 8, 25), datetime(2026, 7, 22, tzinfo=UTC)])  # noqa: DTZ001 - intentionally reject a naive boundary.
def test_invalid_or_pre_disclosure_cutoff_is_rejected(cutoff):
    with pytest.raises(ValidationError):
        _module().load_alphabet_financial_baseline(cutoff)


def test_gaps_do_not_turn_proxy_metrics_or_old_guidance_into_realized_facts():
    value = _module().load_alphabet_financial_baseline(CUTOFF)
    gaps = {gap["key"]: gap for gap in value["research_gaps"]}
    assert {
        "cloud_workload", "infrastructure_depreciation", "infrastructure_opex",
        "search_query_intensity", "search_ad_monetization", "youtube_usage",
        "youtube_ad_monetization", "youtube_subscription_growth", "rpo_definition_change",
        "fcf_not_fcff", "sbc_scope", "latest_capex_guidance_missing",
    } <= set(gaps)
    assert all(fact["state"] in {"reported", "derived"} for fact in value["facts"])
    assert not any(fact["fact_key"].startswith("fy2026_") for fact in value["facts"])
    assert "FCFF" in gaps["fcf_not_fcff"]["detail"]


def test_packaged_sources_replay_without_workspace_outputs(tmp_path, monkeypatch):
    module = _module()
    original = module.load_alphabet_financial_baseline(CUTOFF)
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    assert module.load_alphabet_financial_baseline(CUTOFF) == original


@pytest.mark.parametrize("change", ["value", "scope", "period", "quote", "formula", "source_time", "source_hash"])
def test_rehashed_manifest_tampering_is_rejected(tmp_path, monkeypatch, change):
    module = _module()
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    path = packaged / "baseline.json"
    value = json.loads(path.read_text())
    fact = next(row for row in value["facts"] if row["fact_key"] == "fy2025_group_revenue")
    if change == "value":
        fact["value"] = "58705"
    elif change == "scope":
        fact["scope"] = "google_cloud"
    elif change == "period":
        fact["period_end"] = "2026-06-30"
    elif change == "quote":
        fact["quote"] = "invented disclosure"
    elif change == "formula":
        next(row for row in value["facts"] if row["state"] == "derived")["formula"] = "1 + 1"
    elif change == "source_time":
        value["sources"][0]["available_at"] = "2026-09-01T00:00:00+00:00"
    else:
        value["sources"][0]["raw_content_hash"] = "0" * 64
    value["content_hash"] = canonical_hash({k: v for k, v in value.items() if k != "content_hash"})
    path.write_text(json.dumps(value))
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    with pytest.raises(ValidationError):
        module.load_alphabet_financial_baseline(CUTOFF)


@pytest.mark.parametrize("kind", ["pdf", "truncated_gzip", "corrupt_gzip"])
def test_changed_original_bytes_are_rejected(tmp_path, monkeypatch, kind):
    module = _module()
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    original = next((packaged / "raw").glob("*.pdf" if kind == "pdf" else "*.gz"))
    raw = original.read_bytes()
    if kind == "pdf":
        changed = raw + b"changed"
    elif kind == "truncated_gzip":
        changed = raw[:30]
    else:
        changed = raw[:10] + b"\xff" + raw[11:]
    original.write_bytes(changed)
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    with pytest.raises(ValidationError):
        module.load_alphabet_financial_baseline(CUTOFF)


def test_callers_cannot_mutate_later_reads():
    module = _module()
    first = module.load_alphabet_financial_baseline(CUTOFF)
    expected = first["content_hash"]
    first["facts"][0]["value"] = "0"
    second = module.load_alphabet_financial_baseline(CUTOFF)
    assert second["content_hash"] == expected
    assert second["facts"][0]["value"] != "0"


def test_verified_bytes_avoid_reparsing_but_are_still_revalidated(tmp_path, monkeypatch):
    module = _module()
    if hasattr(module, "_cached_source_texts"):
        module._cached_source_texts.cache_clear()
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    reader = module.PdfReader
    parsed = []

    def counted_reader(stream):
        parsed.append(True)
        return reader(stream)

    monkeypatch.setattr(module, "PdfReader", counted_reader)
    first = module.load_alphabet_financial_baseline(CUTOFF)
    assert module.load_alphabet_financial_baseline(CUTOFF) == first
    assert len(parsed) == 2, "the same two verified PDFs should each be parsed once"
    original = next((packaged / "raw").glob("*.pdf"))
    original.write_bytes(original.read_bytes() + b"changed after cached extraction")
    with pytest.raises(ValidationError):
        module.load_alphabet_financial_baseline(CUTOFF)


@pytest.mark.parametrize("change", ["reported_amount", "derived_amount"])
def test_math_and_original_validation_are_independent_of_package_signature(tmp_path, monkeypatch, change):
    module = _module()
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    path = packaged / "baseline.json"
    value = json.loads(path.read_text())
    key = "fy2025_group_revenue" if change == "reported_amount" else "fy2025_group_free_cash_flow"
    next(fact for fact in value["facts"] if fact["fact_key"] == key)["value"] = "1"
    value["content_hash"] = canonical_hash({k: v for k, v in value.items() if k != "content_hash"})
    path.write_text(json.dumps(value))
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    monkeypatch.setattr(module, "_BASELINE_HASH", value["content_hash"])
    with pytest.raises(ValidationError):
        module.load_alphabet_financial_baseline(CUTOFF)


def test_saved_v1_snapshot_uses_its_frozen_loader_after_default_version_changes(monkeypatch):
    module = _module()
    saved = module.load_alphabet_financial_baseline(CUTOFF)
    monkeypatch.setattr(
        module, "load_alphabet_financial_baseline",
        lambda cutoff: {"schema_version": "company-research.financial-baseline.v2"},
    )

    authenticated = module.authenticate_financial_baseline_snapshot(saved, CUTOFF)
    assert authenticated == saved == module.load_alphabet_financial_baseline_v1(CUTOFF)
    authenticated["facts"][0]["value"] = "changed by caller"
    assert module.authenticate_financial_baseline_snapshot(saved, CUTOFF) == saved


@pytest.mark.parametrize("change", ["unsupported_schema", "unknown_hash", "value", "rehashed_value"])
def test_saved_snapshot_rejects_unsupported_versions_and_tampering(change):
    module = _module()
    saved = module.load_alphabet_financial_baseline(CUTOFF)
    if change == "unsupported_schema":
        saved["schema_version"] = "company-research.financial-baseline.v2"
    elif change == "unknown_hash":
        saved["content_hash"] = "0" * 64
    else:
        saved["facts"][0]["value"] = "1"
        if change == "rehashed_value":
            saved["content_hash"] = canonical_hash({k: v for k, v in saved.items() if k != "content_hash"})
    with pytest.raises(ValidationError):
        module.authenticate_financial_baseline_snapshot(saved, CUTOFF)


def test_saved_snapshot_rechecks_its_disclosure_cutoff_and_original_bytes(tmp_path, monkeypatch):
    module = _module()
    saved = module.load_alphabet_financial_baseline(CUTOFF)
    with pytest.raises(ValidationError):
        module.authenticate_financial_baseline_snapshot(saved, datetime(2026, 7, 22, tzinfo=UTC))
    packaged = tmp_path / "baseline"
    shutil.copytree(module._DATA_ROOT, packaged)
    monkeypatch.setattr(module, "_DATA_ROOT", packaged)
    original = next((packaged / "raw").glob("*.pdf"))
    original.write_bytes(original.read_bytes() + b"changed since snapshot")
    with pytest.raises(ValidationError):
        module.authenticate_financial_baseline_snapshot(saved, CUTOFF)
