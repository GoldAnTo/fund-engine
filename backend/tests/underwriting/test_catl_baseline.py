"""Atomic, evidence-only publication for the incomplete CATL 2024 basis."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.ledger import ValidationError
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.persistence.research_models import (
    UnderwritingEarningsEngineVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)
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
    assert result.research_version.content_hash == result.preview_hash
    assert result.research_version.version_kind == "catl_economic_model_evidence_only"


def test_import_is_deterministic_for_the_same_frozen_fixture(session) -> None:
    result = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())

    assert result.preview_hash == result.research_version.content_hash
    assert result.snapshot_hash


def test_import_rolls_back_every_wave_two_write_when_fixture_cannot_publish(session) -> None:
    bad_fixture = replace(load_catl_fixture(), mechanisms=tuple())
    service = CatlBaselineService(session, now=lambda: NOW)

    with pytest.raises(ValidationError, match="six governed mechanism keys"):
        service.import_fixture(bad_fixture)

    for model in (
        UnderwritingSourceManifestVersion,
        UnderwritingMetricObservation,
        UnderwritingMechanismPackVersion,
        UnderwritingIndustryStateVersion,
        UnderwritingEarningsEngineVersion,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0
