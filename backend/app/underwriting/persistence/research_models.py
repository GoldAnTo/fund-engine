"""Append-only ORM rows for the Wave 2 economic research model.

Rows in this module are deliberately persistence-shaped rather than domain
objects.  They retain every compiled research artefact with its historical
basis, source lineage and successor pointer so a later reader can replay what
was knowable at a particular cutoff.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.ledger import Base, ValidationError, _uuid
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceDossier,
    CandidateEvidenceReview,
)


class UnderwritingSourceManifestVersion(Base):
    __tablename__ = "uw_source_manifest_versions"
    __table_args__ = (
        UniqueConstraint("manifest_key", "version", name="uq_uw_source_manifest_version"),
        Index("ix_uw_source_manifest_versions_basis_key_version", "basis_id", "manifest_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    manifest_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMetricDefinitionVersion(Base):
    __tablename__ = "uw_metric_definition_versions"
    __table_args__ = (
        UniqueConstraint("metric_key", "version", name="uq_uw_metric_definition_version"),
        Index("ix_uw_metric_definition_versions_basis_key_version", "basis_id", "metric_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    unit: Mapped[str] = mapped_column(String(48), nullable=False)
    period_semantics: Mapped[str] = mapped_column(String(32), nullable=False)
    source_role: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciliation_tolerance: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_metric_definition_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMetricObservation(Base):
    __tablename__ = "uw_metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "basis_id", "metric_key", "definition_version", "observed_end", "dimension_hash", "source_id",
            name="uq_uw_metric_observation_identity",
        ),
        Index("ix_uw_metric_observations_basis_metric_available_at", "basis_id", "metric_key", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_metric_definition_versions.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    unit: Mapped[str] = mapped_column(String(48), nullable=False)
    observed_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    dimension_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_metric_observations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMechanismPackVersion(Base):
    __tablename__ = "uw_mechanism_pack_versions"
    __table_args__ = (
        UniqueConstraint("mechanism_key", "version", name="uq_uw_mechanism_pack_version"),
        Index("ix_uw_mechanism_pack_versions_object_basis_status", "object_id", "basis_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mechanism_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    definition_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingIndustryStateVersion(Base):
    __tablename__ = "uw_industry_state_versions"
    __table_args__ = (
        UniqueConstraint("object_id", "basis_id", "version", name="uq_uw_industry_state_version"),
        Index("ix_uw_industry_state_versions_object_basis_version", "object_id", "basis_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    mechanism_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingIndustryScenarioVersion(Base):
    __tablename__ = "uw_industry_scenario_versions"
    __table_args__ = (
        UniqueConstraint("industry_state_id", "scenario_key", "version", name="uq_uw_industry_scenario_version"),
        Index("ix_uw_industry_scenario_versions_state_key_version", "industry_state_id", "scenario_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scenario_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_scenario_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingCompanyExposureVersion(Base):
    __tablename__ = "uw_company_exposure_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "industry_state_id", "exposure_key", "version", name="uq_uw_company_exposure_version"),
        Index("ix_uw_company_exposure_versions_company_basis_key", "company_id", "basis_id", "exposure_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=False)
    exposure_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_company_exposure_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingEarningsEngineVersion(Base):
    __tablename__ = "uw_earnings_engine_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "basis_id", "version", name="uq_uw_earnings_engine_version"),
        Index("ix_uw_earnings_engine_versions_company_basis_version", "company_id", "basis_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_earnings_engine_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingForecastInputVersion(Base):
    __tablename__ = "uw_forecast_input_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "basis_id", "input_key", "version", name="uq_uw_forecast_input_version"),
        Index("ix_uw_forecast_input_versions_company_basis_key", "company_id", "basis_id", "input_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    input_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    earnings_engine_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_earnings_engine_versions.id"), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_forecast_input_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingFalsifierVersion(Base):
    __tablename__ = "uw_falsifier_versions"
    __table_args__ = (
        UniqueConstraint("mechanism_id", "falsifier_key", "version", name="uq_uw_falsifier_version"),
        Index("ix_uw_falsifier_versions_mechanism_key_version", "mechanism_id", "falsifier_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mechanism_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=False)
    falsifier_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_falsifier_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingEvidenceCandidateDossierVersion(Base):
    __tablename__ = "uw_evidence_candidate_dossier_versions"
    __table_args__ = (
        UniqueConstraint(
            "object_id", "basis_id", "dossier_key", "version",
            name="uq_uw_evidence_candidate_dossier_version",
        ),
        UniqueConstraint("supersedes_id", name="uq_uw_evidence_candidate_dossier_successor"),
        CheckConstraint(
            "status = 'candidate'",
            name="ck_uw_evidence_candidate_dossier_status",
        ),
        Index(
            "ix_uw_evidence_candidate_dossiers_object_basis_status",
            "object_id", "basis_id", "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    dossier_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    scope_statement: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejected_calculations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_contract(
        cls, *, id: uuid.UUID, contract: CandidateEvidenceDossier
    ) -> "UnderwritingEvidenceCandidateDossierVersion":
        """Build a row solely from the sealed dossier contract."""
        return cls(
            id=id,
            dossier_key=contract.dossier_key,
            version=contract.version,
            object_id=contract.object_id,
            basis_id=contract.basis_id,
            source_manifest_id=contract.source_manifest_id,
            scope_statement=contract.scope_statement,
            purpose=contract.purpose,
            status=contract.status.value,
            rejected_calculations=list(contract.rejected_calculations),
            payload=contract.canonical_payload,
            source_manifest_hash=contract.source_manifest_hash,
            content_hash=contract.content_hash,
            supersedes_id=contract.supersedes_id,
            created_at=contract.created_at,
        )


class UnderwritingEvidenceCandidateReviewVersion(Base):
    __tablename__ = "uw_evidence_candidate_review_versions"
    __table_args__ = (
        UniqueConstraint(
            "dossier_id", "reviewer_identity", "reviewer_role",
            name="uq_uw_evidence_candidate_review_identity",
        ),
        UniqueConstraint("dossier_id", "reviewer_role", name="uq_uw_evidence_candidate_review_role"),
        UniqueConstraint("dossier_id", "reviewer_identity", name="uq_uw_evidence_candidate_review_reviewer"),
        Index("ix_uw_evidence_candidate_reviews_dossier_role", "dossier_id", "reviewer_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=False
    )
    dossier_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_identity: Mapped[str] = mapped_column(String(320), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_contract(
        cls, *, id: uuid.UUID, contract: CandidateEvidenceReview
    ) -> "UnderwritingEvidenceCandidateReviewVersion":
        """Build a row solely from the sealed review contract."""
        return cls(
            id=id,
            dossier_id=contract.dossier_id,
            dossier_content_hash=contract.dossier_content_hash,
            reviewer_identity=contract.reviewer_identity,
            reviewer_role=contract.reviewer_role,
            decision=contract.decision,
            rationale=contract.rationale,
            payload=contract.canonical_payload,
            content_hash=contract.content_hash,
            reviewed_at=contract.reviewed_at,
            created_at=contract.reviewed_at,
        )


def _validate_dossier_row(row: UnderwritingEvidenceCandidateDossierVersion) -> None:
    contract = CandidateEvidenceDossier.from_canonical_payload(row.payload)
    contract.validate_persisted_payload(row.payload, row.content_hash)
    if (row.object_id, row.basis_id, row.source_manifest_id, row.dossier_key, row.version,
        row.scope_statement, row.purpose, row.status, tuple(row.rejected_calculations),
        row.source_manifest_hash, row.supersedes_id, _stored_utc(row.created_at)) != (
        contract.object_id, contract.basis_id, contract.source_manifest_id, contract.dossier_key, contract.version,
        contract.scope_statement, contract.purpose, contract.status.value, contract.rejected_calculations,
        contract.source_manifest_hash, contract.supersedes_id, contract.created_at):
        raise ValidationError("dossier row does not match canonical contract")


def _validate_review_row(row: UnderwritingEvidenceCandidateReviewVersion) -> None:
    contract = CandidateEvidenceReview.from_canonical_payload(row.payload)
    contract.validate_persisted_payload(row.payload, row.content_hash)
    if (row.dossier_id, row.dossier_content_hash, row.reviewer_identity, row.reviewer_role,
        row.decision, row.rationale, _stored_utc(row.reviewed_at)) != (
        contract.dossier_id, contract.dossier_content_hash, contract.reviewer_identity, contract.reviewer_role,
        contract.decision, contract.rationale, contract.reviewed_at):
        raise ValidationError("review row does not match canonical contract")


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@event.listens_for(UnderwritingEvidenceCandidateDossierVersion, "before_insert")
def _validate_candidate_dossier_insert(_mapper, _connection, target) -> None:
    _validate_dossier_row(target)


@event.listens_for(UnderwritingEvidenceCandidateDossierVersion, "load")
def _validate_candidate_dossier_load(target, _context) -> None:
    _validate_dossier_row(target)


@event.listens_for(UnderwritingEvidenceCandidateReviewVersion, "before_insert")
def _validate_candidate_review_insert(_mapper, connection, target) -> None:
    _validate_review_row(target)
    expected_hash = connection.execute(
        select(UnderwritingEvidenceCandidateDossierVersion.content_hash).where(
            UnderwritingEvidenceCandidateDossierVersion.id == target.dossier_id
        )
    ).scalar_one_or_none()
    if expected_hash != target.dossier_content_hash:
        raise ValidationError("review dossier_content_hash does not match dossier")


@event.listens_for(UnderwritingEvidenceCandidateReviewVersion, "load")
def _validate_candidate_review_load(target, _context) -> None:
    _validate_review_row(target)


@event.listens_for(Session, "loaded_as_persistent")
def _validate_loaded_candidate_review_dossier_hash(session, instance) -> None:
    if not isinstance(instance, UnderwritingEvidenceCandidateReviewVersion):
        return
    dossier = session.get(UnderwritingEvidenceCandidateDossierVersion, instance.dossier_id)
    if dossier is None or dossier.content_hash != instance.dossier_content_hash:
        raise ValidationError("review dossier_content_hash does not match dossier")
