"""Traceability contracts for the frozen CATL 2024 historical basis.

These are deliberately fixture-level tests: they protect the historical
research input from quietly becoming a current-data snapshot.
"""
from __future__ import annotations

from decimal import Decimal

from app.underwriting.fixtures.catl_baseline import load_catl_fixture


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
