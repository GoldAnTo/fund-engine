"""Persistence models for the independent investment research product."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


_HASH_CHECK = "length(content_hash) = 64"


def _json_shape_constraints(
    column_name: str,
    expected_shape: str,
    constraint_name: str,
) -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint(
            f"json_type({column_name}) = '{expected_shape}'",
            name=constraint_name,
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            f"json_typeof({column_name}) = '{expected_shape}'",
            name=constraint_name,
        ).ddl_if(dialect="postgresql"),
    )


class UnderwritingObjectIdentityVersion(Base):
    __tablename__ = "uw_object_identity_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_uw_object_identity_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_uw_object_identity_effective_interval",
        ),
        CheckConstraint(
            "trading_currency IS NULL OR length(trading_currency) = 3",
            name="ck_uw_object_identity_currency",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_uw_object_identity_content_hash"),
        UniqueConstraint(
            "object_id", "version", name="uq_uw_object_identity_version"
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_object_identity_successor"
        ),
        Index(
            "ix_uw_object_identity_object_effective_from",
            "object_id",
            "effective_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(48), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    share_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trading_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_object_identity_versions.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchProject(Base):
    __tablename__ = "uw_research_projects"
    __table_args__ = (
        CheckConstraint(_HASH_CHECK, name="ck_uw_research_project_content_hash"),
        Index("ix_uw_research_projects_company", "primary_company_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    primary_company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchProjectSecurity(Base):
    __tablename__ = "uw_research_project_securities"
    __table_args__ = (
        CheckConstraint(
            _HASH_CHECK, name="ck_uw_research_project_security_content_hash"
        ),
        UniqueConstraint(
            "project_id",
            "security_id",
            name="uq_uw_project_security",
        ),
        Index("ix_uw_project_securities_security", "security_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchScopeVersion(Base):
    __tablename__ = "uw_research_scope_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_uw_research_scope_version"),
        CheckConstraint(_HASH_CHECK, name="ck_uw_research_scope_content_hash"),
        UniqueConstraint(
            "project_id", "version", name="uq_uw_research_scope_version"
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_research_scope_successor"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_scope_versions.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchAgendaVersion(Base):
    __tablename__ = "uw_research_agenda_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_uw_research_agenda_version"),
        CheckConstraint(_HASH_CHECK, name="ck_uw_research_agenda_content_hash"),
        UniqueConstraint(
            "project_id", "version", name="uq_uw_research_agenda_version"
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_research_agenda_successor"
        ),
        Index("ix_uw_research_agenda_scope", "scope_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_scope_versions.id"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    generator_provenance: Mapped[dict] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_agenda_versions.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingPriceSnapshot(Base):
    __tablename__ = "uw_price_snapshots"
    __table_args__ = (
        CheckConstraint("price > 0", name="ck_uw_price_snapshot_positive"),
        CheckConstraint("length(currency) = 3", name="ck_uw_price_snapshot_currency"),
        CheckConstraint(
            "market_at <= available_at", name="ck_uw_price_snapshot_available"
        ),
        CheckConstraint("length(raw_hash) = 64", name="ck_uw_price_snapshot_raw_hash"),
        CheckConstraint(_HASH_CHECK, name="ck_uw_price_snapshot_content_hash"),
        UniqueConstraint(
            "security_identity_id",
            "price_type",
            "adjustment_basis",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_price_snapshot_identity",
        ),
        Index(
            "ix_uw_price_snapshot_security_market",
            "security_identity_id",
            "market_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    security_identity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_type: Mapped[str] = mapped_column(String(64), nullable=False)
    adjustment_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    market_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingFXSnapshot(Base):
    __tablename__ = "uw_fx_snapshots"
    __table_args__ = (
        CheckConstraint(
            "length(base_currency) = 3 AND length(quote_currency) = 3",
            name="ck_uw_fx_snapshot_currencies",
        ),
        CheckConstraint(
            "base_currency <> quote_currency", name="ck_uw_fx_snapshot_pair"
        ),
        CheckConstraint("rate > 0", name="ck_uw_fx_snapshot_positive"),
        CheckConstraint(
            "quote_direction = 'quote_per_base'",
            name="ck_uw_fx_snapshot_quote_direction",
        ),
        CheckConstraint("market_at <= available_at", name="ck_uw_fx_snapshot_available"),
        CheckConstraint("length(raw_hash) = 64", name="ck_uw_fx_snapshot_raw_hash"),
        CheckConstraint(_HASH_CHECK, name="ck_uw_fx_snapshot_content_hash"),
        UniqueConstraint(
            "base_currency",
            "quote_currency",
            "quote_direction",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_fx_snapshot_identity",
        ),
        Index("ix_uw_fx_snapshot_pair_market", "base_currency", "quote_currency", "market_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    quote_direction: Mapped[str] = mapped_column(String(32), nullable=False)
    market_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingCapitalStructureSnapshot(Base):
    __tablename__ = "uw_capital_structure_snapshots"
    __table_args__ = (
        CheckConstraint(
            "length(currency) = 3", name="ck_uw_capital_structure_currency"
        ),
        CheckConstraint(
            "basic_shares > 0 AND diluted_shares >= basic_shares",
            name="ck_uw_capital_structure_shares",
        ),
        CheckConstraint(
            "report_period_start <= report_period_end",
            name="ck_uw_capital_structure_report_period",
        ),
        CheckConstraint(
            "market_at <= available_at", name="ck_uw_capital_structure_available"
        ),
        CheckConstraint(
            "length(raw_hash) = 64", name="ck_uw_capital_structure_raw_hash"
        ),
        CheckConstraint(_HASH_CHECK, name="ck_uw_capital_structure_content_hash"),
        UniqueConstraint(
            "company_id",
            "report_period_start",
            "report_period_end",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_capital_structure_identity",
        ),
        Index("ix_uw_capital_structure_company_market", "company_id", "market_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    debt: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    minority_interest: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    investments: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    pension_liabilities: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    other_adjustments: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    basic_shares: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    diluted_shares: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    potential_dilution_descriptors: Mapped[list[str]] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    report_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    report_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    market_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingSecurityRightsVersion(Base):
    __tablename__ = "uw_security_rights_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_uw_security_rights_version"),
        CheckConstraint(
            "economic_units > 0 AND conversion_ratio > 0 AND adr_ratio > 0",
            name="ck_uw_security_rights_positive",
        ),
        CheckConstraint(
            "votes_per_unit >= 0 AND dividend_rights_per_unit >= 0",
            name="ck_uw_security_rights_nonnegative",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_uw_security_rights_effective_interval",
        ),
        CheckConstraint("length(raw_hash) = 64", name="ck_uw_security_rights_raw_hash"),
        CheckConstraint(_HASH_CHECK, name="ck_uw_security_rights_content_hash"),
        UniqueConstraint(
            "security_identity_id",
            "version",
            name="uq_uw_security_rights_version",
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_security_rights_successor"
        ),
        Index(
            "ix_uw_security_rights_security_effective",
            "security_identity_id",
            "effective_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    security_identity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    economic_units: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    votes_per_unit: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    conversion_ratio: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    adr_ratio: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    dividend_rights_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(28, 10), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_security_rights_versions.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchAssessmentVersion(Base):
    __tablename__ = "uw_research_assessment_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_uw_research_assessment_version"),
        CheckConstraint(
            "answerability IN ('answerable', 'partially_answerable', 'not_answerable')",
            name="ck_uw_research_assessment_answerability",
        ),
        CheckConstraint(
            "direction IS NULL OR direction IN "
            "('provisional_bullish', 'provisional_neutral', 'provisional_cautious')",
            name="ck_uw_research_assessment_direction",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name="ck_uw_research_assessment_confidence",
        ),
        CheckConstraint(
            "publication_status IN ('user_frozen', 'superseded')",
            name="ck_uw_research_assessment_publication",
        ),
        CheckConstraint(
            "answerability <> 'not_answerable' OR "
            "(direction IS NULL AND confidence IS NULL)",
            name="ck_uw_research_assessment_not_answerable",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_uw_research_assessment_content_hash"),
        UniqueConstraint(
            "project_id", "version", name="uq_uw_research_assessment_version"
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_research_assessment_successor"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_assessment_versions.id"), nullable=True
    )
    answerability: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    publication_status: Mapped[str] = mapped_column(String(24), nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON(none_as_null=True), nullable=False)
    resolution_requirements: Mapped[list[str]] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingWorkspaceDraft(Base):
    __tablename__ = "uw_workspace_drafts"
    __table_args__ = (
        CheckConstraint("lock_version >= 1", name="ck_uw_workspace_draft_lock_version"),
        UniqueConstraint("project_id", name="uq_uw_workspace_draft_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_versions.id"), nullable=True
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingRevisionBoundary(Base):
    __tablename__ = "uw_revision_boundaries"
    __table_args__ = (
        CheckConstraint(
            "length(trim(schema_version)) > 0",
            name="ck_uw_revision_boundary_schema_version",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_uw_revision_boundary_content_hash"),
        *_json_shape_constraints(
            "price_snapshot_ids",
            "array",
            "ck_uw_revision_boundary_price_refs_array",
        ),
        *_json_shape_constraints(
            "fx_snapshot_ids",
            "array",
            "ck_uw_revision_boundary_fx_refs_array",
        ),
        *_json_shape_constraints(
            "security_rights_ids",
            "array",
            "ck_uw_revision_boundary_rights_refs_array",
        ),
        Index("ix_uw_revision_boundary_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    historical_basis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_historical_bases.id"), nullable=False
    )
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_mandate_versions.id"), nullable=False
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_scope_versions.id"), nullable=False
    )
    agenda_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_agenda_versions.id"), nullable=False
    )
    price_snapshot_ids: Mapped[list[str]] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    fx_snapshot_ids: Mapped[list[str]] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    capital_structure_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_capital_structure_snapshots.id"), nullable=False
    )
    security_rights_ids: Mapped[list[str]] = mapped_column(
        JSON(none_as_null=True), nullable=False
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_versions.id"), nullable=True
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingRevisionManifest(Base):
    __tablename__ = "uw_revision_manifests"
    __table_args__ = (
        CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_uw_revision_manifest_idempotency_key",
        ),
        CheckConstraint(_HASH_CHECK, name="ck_uw_revision_manifest_content_hash"),
        *_json_shape_constraints(
            "manifest",
            "object",
            "ck_uw_revision_manifest_object",
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_uw_revision_manifest_idempotency",
        ),
        UniqueConstraint("boundary_id", name="uq_uw_revision_manifest_boundary"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    boundary_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_revision_boundaries.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
