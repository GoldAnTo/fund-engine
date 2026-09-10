"""Traceability contracts for the frozen CATL 2024 historical basis.

These are deliberately fixture-level tests: they protect the historical
research input from quietly becoming a current-data snapshot.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.fixtures.catl_answerable_case import (
    REQUIRED_INPUT_KEYS,
    load_catl_answerable_case_fixture,
)
from app.underwriting.fixtures.catl_baseline import (
    load_catl_fixture,
    verify_source_bytes,
)


def test_catl_fixture_uses_fixed_cutoff_and_real_source_anchors() -> None:
    frozen = load_catl_fixture()

    assert frozen.cutoff.isoformat() == "2025-05-15T15:59:59+00:00"
    assert frozen.observation("company.revenue").value == Decimal(362012554000)
    assert frozen.observation("company.operating_cash_flow").value == Decimal(
        96990345000
    )
    assert frozen.observation("segment.power_battery.volume_gwh").value == Decimal(381)
    assert frozen.observation("segment.energy_storage.volume_gwh").value == Decimal(93)


def test_every_observation_traces_to_source_and_locator_without_future_leakage() -> (
    None
):
    frozen = load_catl_fixture()

    for observation in frozen.observations:
        assert frozen.source(observation.source_id) is not None
        assert observation.source_locator
        assert observation.available_at <= frozen.cutoff


def test_fixture_keeps_four_authoritative_sources_and_verified_annual_report_digest() -> (
    None
):
    frozen = load_catl_fixture()

    assert set(frozen.sources) == {
        "catl-2024-annual-report-cninfo",
        "catl-2024-annual-report-official",
        "iea-global-ev-outlook-2025",
        "china-battery-alliance-2024-installations",
    }
    assert frozen.source("catl-2024-annual-report-cninfo")["content_sha256"] == (
        "b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad"
    )


def test_unavailable_baselines_are_explicit_unknowns_not_synthetic_numbers() -> None:
    frozen = load_catl_fixture()

    unknowns = [item for item in frozen.observations if item.value is None]
    assert {item.definition_key for item in unknowns} >= {
        "industry.nominal_capacity_gwh",
        "industry.effective_capacity_gwh",
    }
    assert all(item.dimensions["disclosure_status"] == "unknown" for item in unknowns)


def test_authenticated_numeric_batch_excludes_documented_unknowns() -> None:
    frozen = load_catl_fixture()

    assert len(frozen.frozen_observations) == len(
        [item for item in frozen.observations if item.value is not None]
    )
    assert all(item.value.is_finite() for item in frozen.frozen_observations)


def test_china_2024_installations_are_a_published_observation_not_an_unknown() -> None:
    frozen = load_catl_fixture()

    source = frozen.source("china-battery-alliance-2024-installations")
    observation = frozen.observation("industry.china_power_battery_installations_gwh")
    assert source is not None
    assert source["published_at"] == "2025-01-19T16:00:00+00:00"
    assert source["first_available_at"] == "2025-01-19T16:00:00+00:00"
    assert observation.value == Decimal("548.4")
    assert observation.observation_status == "official_industry"
    assert observation.available_at.isoformat() == "2025-01-19T16:00:00+00:00"
    assert "548.4GWh" in observation.source_locator


def test_other_business_cost_is_a_replayable_derived_residual() -> None:
    frozen = load_catl_fixture()

    observation = frozen.observation("segment.other.cost")
    assert observation.value == Decimal(8436147000)
    assert observation.source_role == "derived"
    assert observation.observation_status == "derived"
    assert observation.derivation_formula == (
        "company.cost_of_revenue - sum(named_segment_costs)"
    )
    assert observation.derivation_parents == (
        "company.cost_of_revenue",
        "segment.power_battery.cost",
        "segment.energy_storage.cost",
        "segment.materials_recycling.cost",
        "segment.mineral_resources.cost",
    )
    frozen_value = next(
        item
        for item in frozen.frozen_observations
        if item.definition_key == "segment.other.cost"
    )
    frozen_dimensions = dict(frozen_value.dimensions)
    assert frozen_dimensions["_derivation_formula"] == observation.derivation_formula
    assert frozen_dimensions["_derivation_parent_observation_ids"]
    assert frozen_dimensions["_derivation_parent_content_hashes"]


def test_source_digest_verification_accepts_only_matching_nonempty_bytes() -> None:
    payload = b"controlled source bytes for fixture digest verification"
    digest = hashlib.sha256(payload).hexdigest()

    assert verify_source_bytes(payload, expected_sha256=digest) == digest
    with pytest.raises(ValidationError, match="does not match"):
        verify_source_bytes(payload, expected_sha256="0" * 64)
    with pytest.raises(ValidationError, match="must not be empty"):
        verify_source_bytes(b"", expected_sha256=digest)


def test_required_answerable_inputs_are_present_and_unknown_is_never_zero() -> None:
    fixture = load_catl_answerable_case_fixture()
    facts = {item.fact_key: item for item in fixture.facts}

    assert REQUIRED_INPUT_KEYS.issubset(facts)
    for key in REQUIRED_INPUT_KEYS:
        fact = facts[key]
        assert type(fact.value) is Decimal
        assert fact.value != Decimal(0)
        assert fact.unit
        assert fact.currency == (None if key == "basic_shares_2024" else "CNY")
        assert fact.source_locator
        assert fact.available_at <= fixture.cutoff


def test_answerable_source_lineage_uses_only_frozen_issuer_and_exchange_records() -> (
    None
):
    fixture = load_catl_answerable_case_fixture()

    assert {item.source_role for item in fixture.facts} == {"regulatory_filing"}
    assert all(
        item.source_url.startswith("https://static.cninfo.com.cn/")
        for item in fixture.facts
    )
    price = fixture.market_inputs.price
    assert price.source_role == "official_exchange"
    assert price.source_url.startswith("https://www.szse.cn/")
    assert price.source_locator == (
        "picupdata row 2025-11-06; fields date/open/close/low/high/change/pct/volume/turnover"
    )
    assert price.value == Decimal("394.68")
    assert price.available_at <= fixture.cutoff


def test_all_answerable_numeric_inputs_have_hash_locator_unit_and_availability() -> (
    None
):
    fixture = load_catl_answerable_case_fixture()

    for item in fixture.facts:
        assert len(item.raw_hash) == 64
        assert item.source_locator
        assert item.unit
        assert item.available_at <= fixture.cutoff
    price = fixture.market_inputs.price
    assert len(price.raw_hash) == 64
    assert price.unit == "per_share"
    assert price.available_at <= fixture.cutoff


def test_answerable_working_capital_binds_equation_and_all_parent_facts() -> None:
    fixture = load_catl_answerable_case_fixture()
    working_capital = next(
        item for item in fixture.facts if item.fact_key == "working_capital_change_2024"
    )

    assert working_capital.value_kind == "derived"
    assert working_capital.equation_id == "catl.change-in-net-working-capital.v1"
    assert working_capital.parent_fact_keys == (
        "inventory_cash_flow_effect_2024",
        "operating_payables_cash_flow_effect_2024",
        "operating_receivables_cash_flow_effect_2024",
    )


def test_exchange_capture_hash_authenticates_the_retained_normalized_row() -> None:
    fixture = load_catl_answerable_case_fixture()

    assert fixture.market_inputs.verify_price_record_hash() == (
        fixture.market_inputs.price.raw_hash
    )
