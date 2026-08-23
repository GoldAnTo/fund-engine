"""Persistence contract for the append-only Wave 2 economic model."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base
from app.models.ledger import IMMUTABLE_TABLES, ImmutableLedgerError
from app.models.ledger import ValidationError
from app.underwriting.domain import (
    CandidateEvidenceDossier,
    CandidateEvidenceDossierStatus,
    CandidateEvidenceItem,
    CandidateEvidenceReview,
    CandidateEvidenceStatus,
)
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingEvidenceCandidateDossierVersion,
    UnderwritingEvidenceCandidateReviewVersion,
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

CANDIDATE_EVIDENCE_TABLES = frozenset(
    {
        "uw_evidence_candidate_dossier_versions",
        "uw_evidence_candidate_review_versions",
    }
)


def _candidate_item(**overrides: object) -> CandidateEvidenceItem:
    values: dict[str, object] = {
        "metric_key": "industry.nominal_capacity",
        "status": CandidateEvidenceStatus.CHART_APPROXIMATION,
        "value": Decimal("0.9"),
        "unit": "TWh",
        "observed_start": NOW,
        "observed_end": NOW,
        "available_at": NOW,
        "source_id": "iea-2024",
        "source_locator": "iea:chart:4",
        "scope_statement": "China lithium-ion battery cells, 2024",
        "exclusions": ("effective capacity",),
        "methodology": "manual chart reading",
        "prohibited_splicing_declaration": "Do not infer utilization from this item.",
        "transcription_method": "read bar height against labelled axis",
        "error_bound": Decimal("0.1"),
    }
    values.update(overrides)
    return CandidateEvidenceItem(**values)  # type: ignore[arg-type]


def _candidate_dossier(**overrides: object) -> CandidateEvidenceDossier:
    values: dict[str, object] = {
        "object_id": uuid4(),
        "basis_id": uuid4(),
        "source_manifest_id": uuid4(),
        "dossier_key": "battery-capacity",
        "version": 1,
        "scope_statement": "China lithium-ion battery cells, 2024",
        "items": (_candidate_item(),),
        "rejected_calculations": ("utilization = output / nominal capacity",),
        "source_manifest_hash": "a" * 64,
        "created_at": NOW,
        "status": CandidateEvidenceDossierStatus.DRAFT,
    }
    values.update(overrides)
    return CandidateEvidenceDossier(**values)  # type: ignore[arg-type]


def test_chart_candidate_requires_transcription_method() -> None:
    with pytest.raises(ValidationError, match="chart_approximation requires transcription_method"):
        _candidate_item(transcription_method=None)


def test_unknown_candidate_cannot_carry_a_numeric_value() -> None:
    with pytest.raises(ValidationError, match="unknown must not carry a numeric value"):
        _candidate_item(
            status=CandidateEvidenceStatus.UNKNOWN,
            value=Decimal("0"),
            unit=None,
            transcription_method=None,
            error_bound=None,
            unknown_reason="not published",
        )


def test_candidate_item_requires_a_prohibited_splicing_declaration() -> None:
    with pytest.raises(ValidationError, match="prohibited_splicing_declaration"):
        _candidate_item(prohibited_splicing_declaration="")


def test_dossier_rejects_empty_or_duplicate_rejected_calculations() -> None:
    values = {
        "object_id": uuid4(),
        "basis_id": uuid4(),
        "source_manifest_id": uuid4(),
        "dossier_key": "battery-capacity",
        "version": 1,
        "scope_statement": "China lithium-ion battery cells, 2024",
        "items": (_candidate_item(prohibited_splicing_declaration="Do not combine this candidate with other items."),),
        "source_manifest_hash": "a" * 64,
        "created_at": NOW,
    }
    with pytest.raises(ValidationError, match="rejected_calculations must be a non-empty tuple"):
        CandidateEvidenceDossier(**values, rejected_calculations=())
    with pytest.raises(ValidationError, match="rejected_calculations must be unique"):
        CandidateEvidenceDossier(
            **values,
            rejected_calculations=("utilization = output / nominal capacity", "utilization = output / nominal capacity"),
        )


def test_dossier_hash_binds_rejected_calculations_and_splicing_declarations() -> None:
    values = {
        "object_id": uuid4(),
        "basis_id": uuid4(),
        "source_manifest_id": uuid4(),
        "dossier_key": "battery-capacity",
        "version": 1,
        "scope_statement": "China lithium-ion battery cells, 2024",
        "source_manifest_hash": "a" * 64,
        "created_at": NOW,
        "rejected_calculations": ("utilization = output / nominal capacity",),
    }
    dossier = CandidateEvidenceDossier(
        **values,
        items=(_candidate_item(prohibited_splicing_declaration="Do not infer utilization from this item."),),
    )
    changed = CandidateEvidenceDossier(
        **values,
        items=(_candidate_item(prohibited_splicing_declaration="Do not infer effective capacity from this item."),),
    )
    changed_rejected_calculations = CandidateEvidenceDossier(
        **{**values, "rejected_calculations": ("effective capacity = nominal capacity * availability",)},
        items=(_candidate_item(prohibited_splicing_declaration="Do not infer utilization from this item."),),
    )
    assert dossier.content_hash != changed.content_hash
    assert dossier.content_hash != changed_rejected_calculations.content_hash


def test_review_rejects_a_whitespace_variant_of_an_existing_reviewer_identity() -> None:
    CandidateEvidenceReview(
        dossier_id=uuid4(),
        dossier_content_hash="a" * 64,
        reviewer_identity="reviewer:a",
        reviewer_role="provenance",
        decision="approve",
        rationale="source verified",
        reviewed_at=NOW,
    )
    with pytest.raises(ValidationError, match="reviewer_identity must be canonical"):
        CandidateEvidenceReview(
            dossier_id=uuid4(),
            dossier_content_hash="a" * 64,
            reviewer_identity=" reviewer:a ",
            reviewer_role="methodology",
            decision="approve",
            rationale="method verified",
            reviewed_at=NOW,
        )


@pytest.mark.parametrize("reviewer_identity", (None, 7))
def test_review_rejects_a_non_string_reviewer_identity_with_validation_error(
    reviewer_identity: object,
) -> None:
    with pytest.raises(ValidationError, match="reviewer_identity must not be empty"):
        CandidateEvidenceReview(
            dossier_id=uuid4(),
            dossier_content_hash="a" * 64,
            reviewer_identity=reviewer_identity,  # type: ignore[arg-type]
            reviewer_role="provenance",
            decision="approve",
            rationale="source verified",
            reviewed_at=NOW,
        )


@pytest.mark.parametrize("version", (True, 1.0, float("nan"), float("inf"), "1"))
def test_candidate_dossier_requires_an_exact_integer_version(version: object) -> None:
    with pytest.raises(ValidationError, match="version must be an integer"):
        _candidate_dossier(version=version)


def test_candidate_dossier_rejects_formal_status() -> None:
    with pytest.raises(ValidationError, match="status must be a CandidateEvidenceDossierStatus"):
        _candidate_dossier(status="formal")


def test_candidate_dossier_and_review_payloads_are_canonical_and_sealed() -> None:
    dossier = _candidate_dossier()
    review = CandidateEvidenceReview(
        dossier_id=uuid4(),
        dossier_content_hash=dossier.content_hash,
        reviewer_identity="reviewer:a",
        reviewer_role="provenance",
        decision="approve",
        rationale="source verified",
        reviewed_at=NOW,
    )
    assert dossier.canonical_payload["status"] == "draft"
    reviewed = _candidate_dossier(status=CandidateEvidenceDossierStatus.REVIEWED_CANDIDATE)
    assert dossier.content_hash != reviewed.content_hash
    assert dossier.canonical_payload["items"][0]["prohibited_splicing_declaration"]
    assert "formal" not in dossier.canonical_payload
    assert "action" not in dossier.canonical_payload
    assert "valuation" not in dossier.canonical_payload
    with pytest.raises(ValidationError, match="dossier payload does not match"):
        dossier.validate_persisted_payload(
            {**dossier.canonical_payload, "formal": True}, dossier.content_hash
        )
    rehashed_dossier_payload = {**dossier.canonical_payload, "action": "forbidden"}
    rehashed_dossier_hash = hashlib.sha256(
        json.dumps(rehashed_dossier_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="dossier payload does not match"):
        dossier.validate_persisted_payload(rehashed_dossier_payload, rehashed_dossier_hash)
    with pytest.raises(ValidationError, match="dossier content_hash does not match"):
        dossier.validate_persisted_payload(dossier.canonical_payload, "b" * 64)
    with pytest.raises(ValidationError, match="review payload does not match"):
        review.validate_persisted_payload(
            {**review.canonical_payload, "valuation": "forbidden"}, review.content_hash
        )
    rehashed_review_payload = {**review.canonical_payload, "valuation": "forbidden"}
    rehashed_review_hash = hashlib.sha256(
        json.dumps(rehashed_review_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="review payload does not match"):
        review.validate_persisted_payload(rehashed_review_payload, rehashed_review_hash)
    with pytest.raises(ValidationError, match="review content_hash does not match"):
        review.validate_persisted_payload(review.canonical_payload, "b" * 64)
    dossier_row = UnderwritingEvidenceCandidateDossierVersion.from_contract(
        id=uuid4(), contract=dossier
    )
    review_row = UnderwritingEvidenceCandidateReviewVersion.from_contract(
        id=uuid4(), contract=review
    )
    assert dossier_row.payload == dossier.canonical_payload
    assert dossier_row.content_hash == dossier.content_hash
    assert review_row.payload == review.canonical_payload
    assert review_row.content_hash == review.content_hash


def test_candidate_database_rejects_formal_status_and_non_independent_reviews() -> None:
    engine = create_engine("sqlite://")
    tables = [
        UnderwritingEvidenceCandidateDossierVersion.__table__,
        UnderwritingEvidenceCandidateReviewVersion.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    try:
        with Session(engine) as session:
            dossier = UnderwritingEvidenceCandidateDossierVersion(
                id=uuid4(), dossier_key="battery-capacity", version=1,
                object_id=uuid4(), basis_id=uuid4(), source_manifest_id=uuid4(),
                scope_statement="China lithium-ion battery cells, 2024",
                purpose="evidence_candidate", status="formal", rejected_calculations=["output / nominal capacity"],
                payload={}, source_manifest_hash="a" * 64, content_hash="a" * 64, created_at=NOW,
            )
            session.add(dossier)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            dossier.status = "draft"
            session.add(dossier)
            session.commit()
            first = UnderwritingEvidenceCandidateReviewVersion(
                id=uuid4(), dossier_id=dossier.id, dossier_content_hash="a" * 64,
                reviewer_identity="reviewer:a", reviewer_role="provenance", decision="approve",
                rationale="source verified", payload={}, content_hash="a" * 64,
                reviewed_at=NOW, created_at=NOW,
            )
            session.add(first)
            session.commit()
            session.add(UnderwritingEvidenceCandidateReviewVersion(
                id=uuid4(), dossier_id=dossier.id, dossier_content_hash="a" * 64,
                reviewer_identity="reviewer:a", reviewer_role="methodology", decision="approve",
                rationale="method verified", payload={}, content_hash="a" * 64,
                reviewed_at=NOW, created_at=NOW,
            ))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(UnderwritingEvidenceCandidateReviewVersion(
                id=uuid4(), dossier_id=dossier.id, dossier_content_hash="a" * 64,
                reviewer_identity="reviewer:b", reviewer_role="provenance", decision="approve",
                rationale="source verified", payload={}, content_hash="a" * 64,
                reviewed_at=NOW, created_at=NOW,
            ))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        Base.metadata.drop_all(engine, tables=tables)


def test_candidate_rows_capture_dossier_and_review_governance_contracts() -> None:
    dossier = Base.metadata.tables["uw_evidence_candidate_dossier_versions"]
    review = Base.metadata.tables["uw_evidence_candidate_review_versions"]

    assert {
        "object_id", "basis_id", "source_manifest_id", "dossier_key", "version",
        "scope_statement", "purpose", "rejected_calculations", "payload", "source_manifest_hash", "content_hash",
        "supersedes_id", "created_at",
    } <= set(dossier.c.keys())
    assert {"dossier_id", "dossier_content_hash", "reviewer_identity", "reviewer_role", "decision", "rationale", "payload", "content_hash", "reviewed_at", "created_at"} <= set(review.c.keys())
    assert dossier.c.object_id.foreign_keys and dossier.c.basis_id.foreign_keys
    assert dossier.c.source_manifest_id.foreign_keys and review.c.dossier_id.foreign_keys


def test_candidate_tables_are_registered_and_append_only() -> None:
    assert CANDIDATE_EVIDENCE_TABLES <= set(Base.metadata.tables)
    assert CANDIDATE_EVIDENCE_TABLES <= IMMUTABLE_TABLES


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
    "uw_evidence_candidate_dossier_versions": (
        "uq_uw_evidence_candidate_dossier_version",
        ("object_id", "basis_id", "dossier_key", "version"),
    ),
    "uw_evidence_candidate_review_versions": (
        "uq_uw_evidence_candidate_review_identity",
        ("dossier_id", "reviewer_identity", "reviewer_role"),
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


def test_candidate_metadata_can_create_and_drop_on_sqlite() -> None:
    engine = create_engine("sqlite://")
    tables = [Base.metadata.tables[name] for name in CANDIDATE_EVIDENCE_TABLES]
    try:
        Base.metadata.create_all(engine, tables=tables)
        assert CANDIDATE_EVIDENCE_TABLES <= set(inspect(engine).get_table_names())
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
        UnderwritingEvidenceCandidateDossierVersion,
        UnderwritingEvidenceCandidateReviewVersion,
    }
