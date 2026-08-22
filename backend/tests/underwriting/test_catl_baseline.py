"""Atomic, evidence-only publication for the incomplete CATL 2024 basis."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ledger import Base, ValidationError
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.persistence.research_models import (
    UnderwritingEarningsEngineVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)
from app.underwriting.persistence.models import UnderwritingLedgerEntry, UnderwritingObjectRelation
from app.underwriting.services.catl_baseline import CatlBaselineService


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def test_import_publishes_replayable_evidence_only_version_without_false_completion(session) -> None:
    result = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())

    assert result.industry.external_key == "POWER_BATTERY:GLOBAL"
    assert result.company.external_key == "CN:300750:COMPANY"
    assert result.security.external_key == "SZSE:300750"
    assert result.industry_state is None
    assert result.earnings_engine is None
    assert result.formal_mechanisms == ()
    assert result.answerability.state == "not_answerable"
    assert set(result.answerability.blockers) == {
        "missing_key_baseline", "mechanism_unidentified"
    }
    assert result.answerability.allowed_action == "wait_for_validation"
    assert result.research_version.content_hash == result.preview_hash
    assert result.research_version.version_kind == "catl_economic_model_evidence_only"


def _fresh_import_hashes() -> tuple[str, str]:
    engine = create_engine("sqlite://", future=True, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    try:
        result = CatlBaselineService(db, now=lambda: NOW).import_fixture(load_catl_fixture())
        return result.snapshot_hash, result.preview_hash
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_import_is_deterministic_across_independent_databases() -> None:
    first = _fresh_import_hashes()
    second = _fresh_import_hashes()

    assert first == second


def test_repeat_import_reuses_identities_and_does_not_duplicate_relations(session) -> None:
    service = CatlBaselineService(session, now=lambda: NOW)
    first = service.import_fixture(load_catl_fixture())
    second = service.import_fixture(load_catl_fixture())

    assert second.company.id == first.company.id
    assert second.industry.id == first.industry.id
    assert second.security.id == first.security.id
    assert second.research_version.id == first.research_version.id
    assert second.preview_hash == first.preview_hash
    assert session.scalar(select(func.count()).select_from(UnderwritingObjectRelation)) == 2


def test_derived_reality_entry_retains_source_status_and_bound_lineage(session) -> None:
    result = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())
    entry = session.scalar(
        select(UnderwritingLedgerEntry).where(
            UnderwritingLedgerEntry.object_id == result.company.id,
            UnderwritingLedgerEntry.family_key == "metric:segment.other.cost",
        )
    )
    assert entry is not None
    assert entry.entry_type == "derived"
    assert entry.payload["source_role"] == "derived"
    assert entry.payload["derivation"]["formula"]
    assert entry.payload["derivation"]["parent_content_hashes"]


def test_unknown_evidence_gaps_are_persisted_and_required_for_publication(session) -> None:
    fixture = load_catl_fixture()
    result = CatlBaselineService(session, now=lambda: NOW).import_fixture(fixture)
    gaps = session.scalars(select(UnderwritingLedgerEntry).where(
        UnderwritingLedgerEntry.object_id == result.industry.id,
        UnderwritingLedgerEntry.entry_type == "unknown_evidence_gap",
    )).all()
    assert {entry.payload["metric_key"] for entry in gaps} >= {
        "industry.nominal_capacity_gwh", "industry.effective_capacity_gwh"
    }
    stripped = replace(
        fixture,
        observations=tuple(item for item in fixture.observations if item.value is not None),
    )
    with pytest.raises(ValidationError, match="authenticated full observation fixture"):
        CatlBaselineService(session, now=lambda: NOW).import_fixture(stripped)


@pytest.mark.parametrize("field", ["source_locator", "source_id", "available_at", "dimensions"])
def test_import_rejects_replaced_unknown_evidence_gap_forgery(session, field) -> None:
    fixture = load_catl_fixture()
    target = next(item for item in fixture.observations if item.value is None)
    replacement = replace(
        target,
        **{field: "forged" if field != "dimensions" else {"scope": "forged"}},
    )
    forged = replace(
        fixture,
        observations=tuple(replacement if item is target else item for item in fixture.observations),
    )

    with pytest.raises(ValidationError, match="authenticated full observation fixture"):
        CatlBaselineService(session, now=lambda: NOW).import_fixture(forged)


def test_import_rolls_back_every_wave_two_write_when_fixture_cannot_publish(session) -> None:
    bad_fixture = replace(load_catl_fixture(), mechanisms=tuple())
    service = CatlBaselineService(session, now=lambda: NOW)

    with pytest.raises(ValidationError, match="authenticated full observation fixture"):
        service.import_fixture(bad_fixture)

    for model in (
        UnderwritingSourceManifestVersion,
        UnderwritingMetricObservation,
        UnderwritingMechanismPackVersion,
        UnderwritingIndustryStateVersion,
        UnderwritingEarningsEngineVersion,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0
