"""Persistence contract for the append-only Wave 2 economic model."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.orm import Session

from app.models import Base
from app.models.ledger import IMMUTABLE_TABLES, ImmutableLedgerError
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingFalsifierVersion,
    UnderwritingForecastInputVersion,
    UnderwritingIndustryScenarioVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricDefinitionVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)

WAVE2_TABLES = frozenset(
    {
        "uw_source_manifest_versions",
        "uw_metric_definition_versions",
        "uw_metric_observations",
        "uw_mechanism_pack_versions",
        "uw_industry_state_versions",
        "uw_industry_scenario_versions",
        "uw_company_exposure_versions",
        "uw_earnings_engine_versions",
        "uw_forecast_input_versions",
        "uw_falsifier_versions",
    }
)


EXPECTED_UNIQUES = {
    "uw_source_manifest_versions": ("uq_uw_source_manifest_version", ("manifest_key", "version")),
    "uw_metric_definition_versions": ("uq_uw_metric_definition_version", ("metric_key", "version")),
    "uw_metric_observations": (
        "uq_uw_metric_observation_identity",
        ("basis_id", "metric_key", "definition_version", "observed_end", "dimension_hash", "source_id"),
    ),
    "uw_mechanism_pack_versions": ("uq_uw_mechanism_pack_version", ("mechanism_key", "version")),
    "uw_industry_state_versions": ("uq_uw_industry_state_version", ("object_id", "basis_id", "version")),
    "uw_industry_scenario_versions": (
        "uq_uw_industry_scenario_version",
        ("industry_state_id", "scenario_key", "version"),
    ),
    "uw_company_exposure_versions": (
        "uq_uw_company_exposure_version",
        ("company_id", "industry_state_id", "exposure_key", "version"),
    ),
    "uw_earnings_engine_versions": (
        "uq_uw_earnings_engine_version",
        ("company_id", "basis_id", "version"),
    ),
    "uw_forecast_input_versions": (
        "uq_uw_forecast_input_version",
        ("company_id", "basis_id", "input_key", "version"),
    ),
    "uw_falsifier_versions": (
        "uq_uw_falsifier_version",
        ("mechanism_id", "falsifier_key", "version"),
    ),
}


def _unique_columns(table_name: str, constraint_name: str) -> tuple[str, ...]:
    table = Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if item.name == constraint_name
    )
    return tuple(column.name for column in constraint.columns)


def test_wave2_tables_are_registered_and_append_only() -> None:
    assert WAVE2_TABLES <= set(Base.metadata.tables)
    assert WAVE2_TABLES <= IMMUTABLE_TABLES


@pytest.mark.parametrize(("table_name", "expected"), EXPECTED_UNIQUES.items())
def test_wave2_tables_have_their_stable_successor_identity(
    table_name: str, expected: tuple[str, tuple[str, ...]]
) -> None:
    constraint_name, columns = expected
    assert _unique_columns(table_name, constraint_name) == columns


@pytest.mark.parametrize("table_name", sorted(WAVE2_TABLES))
def test_every_wave2_row_carries_replay_provenance(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    assert {"id", "basis_id", "content_hash", "supersedes_id", "created_at"} <= set(table.c.keys())
    assert table.c.basis_id.foreign_keys
    assert table.c.supersedes_id.foreign_keys
    assert table.c.content_hash.type.length == 64


def test_wave2_parent_links_and_json_payloads_are_explicit() -> None:
    observation = Base.metadata.tables["uw_metric_observations"]
    definition = Base.metadata.tables["uw_metric_definition_versions"]
    mechanism = Base.metadata.tables["uw_mechanism_pack_versions"]
    scenario = Base.metadata.tables["uw_industry_scenario_versions"]
    exposure = Base.metadata.tables["uw_company_exposure_versions"]
    falsifier = Base.metadata.tables["uw_falsifier_versions"]

    assert {"observed_start", "observed_end", "effective_at", "available_at", "dimension_hash"} <= set(observation.c.keys())
    assert observation.c.source_manifest_id.foreign_keys
    assert observation.c.source_id.type.length == 160
    assert observation.c.definition_id.foreign_keys
    assert definition.c.source_manifest_id.foreign_keys
    assert mechanism.c.source_manifest_id.foreign_keys
    assert scenario.c.industry_state_id.foreign_keys
    assert exposure.c.company_id.foreign_keys and exposure.c.industry_state_id.foreign_keys
    assert falsifier.c.mechanism_id.foreign_keys

    for table, column in (
        ("uw_source_manifest_versions", "manifest"),
        ("uw_metric_definition_versions", "definition"),
        ("uw_mechanism_pack_versions", "payload"),
        ("uw_industry_state_versions", "payload"),
        ("uw_industry_scenario_versions", "payload"),
        ("uw_company_exposure_versions", "payload"),
        ("uw_earnings_engine_versions", "payload"),
        ("uw_forecast_input_versions", "payload"),
        ("uw_falsifier_versions", "payload"),
    ):
        assert Base.metadata.tables[table].c[column].type.__class__.__name__ == "JSON"


def test_wave2_rows_reject_update_through_the_application_guard() -> None:
    engine = create_engine("sqlite://")
    table = UnderwritingSourceManifestVersion.__table__
    Base.metadata.create_all(engine, tables=[table])
    try:
        with Session(engine) as session:
            with pytest.raises(ImmutableLedgerError):
                session.execute(
                    update(UnderwritingSourceManifestVersion).values(manifest_key="rewritten")
                )
    finally:
        Base.metadata.drop_all(engine, tables=[table])


def test_wave2_metadata_can_create_and_drop_on_sqlite() -> None:
    engine = create_engine("sqlite://")
    tables = [Base.metadata.tables[name] for name in WAVE2_TABLES]
    try:
        Base.metadata.create_all(engine, tables=tables)
        assert WAVE2_TABLES <= set(inspect(engine).get_table_names())
    finally:
        Base.metadata.drop_all(engine, tables=tables)


def test_wave2_models_are_importable_for_the_future_repository_contract() -> None:
    assert {
        UnderwritingMetricDefinitionVersion,
        UnderwritingMetricObservation,
        UnderwritingMechanismPackVersion,
        UnderwritingIndustryStateVersion,
        UnderwritingIndustryScenarioVersion,
        UnderwritingCompanyExposureVersion,
        UnderwritingEarningsEngineVersion,
        UnderwritingForecastInputVersion,
        UnderwritingFalsifierVersion,
    }
