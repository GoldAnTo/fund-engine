"""Traceability contracts for the frozen CATL 2024 historical basis.

These are deliberately fixture-level tests: they protect the historical
research input from quietly becoming a current-data snapshot.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib

import pytest

from app.models.ledger import ValidationError
from app.underwriting.fixtures.catl_baseline import load_catl_fixture, verify_source_bytes


def test_catl_fixture_uses_fixed_cutoff_and_real_source_anchors() -> None:
    frozen = load_catl_fixture()

    assert frozen.cutoff.isoformat() == "2025-05-15T15:59:59+00:00"
    assert frozen.observation("company.revenue").value == Decimal("362012554000")
    assert frozen.observation("company.operating_cash_flow").value == Decimal("96990345000")
    assert frozen.observation("segment.power_battery.volume_gwh").value == Decimal("381")
    assert frozen.observation("segment.energy_storage.volume_gwh").value == Decimal("93")


def test_every_observation_traces_to_source_and_locator_without_future_leakage() -> None:
    frozen = load_catl_fixture()

    for observation in frozen.observations:
        assert frozen.source(observation.source_id) is not None
        assert observation.source_locator
        assert observation.available_at <= frozen.cutoff


def test_fixture_keeps_four_authoritative_sources_and_verified_annual_report_digest() -> None:
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

    observation = frozen.observation("industry.china_power_battery_installations_gwh")
    assert observation.value == Decimal("548.4")
    assert observation.observation_status == "official_industry"
    assert observation.available_at.isoformat() == "2025-01-12T16:00:00+00:00"
    assert "548.4GWh" in observation.source_locator


def test_other_business_cost_is_a_replayable_derived_residual() -> None:
    frozen = load_catl_fixture()

    observation = frozen.observation("segment.other.cost")
    assert observation.value == Decimal("8436147000")
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


def test_source_digest_verification_accepts_only_matching_nonempty_bytes() -> None:
    payload = b"controlled source bytes for fixture digest verification"
    digest = hashlib.sha256(payload).hexdigest()

    assert verify_source_bytes(payload, expected_sha256=digest) == digest
    with pytest.raises(ValidationError, match="does not match"):
        verify_source_bytes(payload, expected_sha256="0" * 64)
    with pytest.raises(ValidationError, match="must not be empty"):
        verify_source_bytes(b"", expected_sha256=digest)
