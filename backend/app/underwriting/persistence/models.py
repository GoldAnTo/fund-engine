"""Append-only persistence models for the underwriting research kernel."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Boolean,
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


class UnderwritingResearchObject(Base):
    __tablename__ = "uw_research_objects"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('industry', 'company', 'security')",
            name="ck_uw_object_kind",
        ),
        UniqueConstraint(
            "kind", "external_key", name="uq_uw_object_external_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingObjectRelation(Base):
    __tablename__ = "uw_object_relations"
    __table_args__ = (
        UniqueConstraint(
            "parent_id",
            "child_id",
            "relation_type",
            name="uq_uw_object_relation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    parent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(48), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMandateVersion(Base):
    __tablename__ = "uw_mandate_versions"
    __table_args__ = (
        CheckConstraint(
            "horizon_years BETWEEN 3 AND 5",
            name="ck_uw_mandate_horizon",
        ),
        CheckConstraint(
            "required_return >= 0 AND required_return < 1",
            name="ck_uw_required_return",
        ),
        CheckConstraint(
            "permanent_loss_limit >= 0 AND permanent_loss_limit <= 1",
            name="ck_uw_loss_limit",
        ),
        UniqueConstraint(
            "mandate_key", "version", name="uq_uw_mandate_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mandate_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_years: Mapped[int] = mapped_column(Integer, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    required_return: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    permanent_loss_limit: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False
    )
    comparison_set: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_mandate_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingHistoricalBasis(Base):
    __tablename__ = "uw_historical_bases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_as_of: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingLedgerEntry(Base):
    __tablename__ = "uw_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "ledger_kind IN ('reality', 'belief', 'decision', 'calibration')",
            name="ck_uw_ledger_kind",
        ),
        UniqueConstraint(
            "object_id",
            "ledger_kind",
            "family_key",
            "version",
            name="uq_uw_ledger_family_version",
        ),
        Index(
            "ix_uw_ledger_entries_object_kind_available_at",
            "object_id",
            "ledger_kind",
            "available_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    basis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_historical_bases.id"), nullable=False
    )
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    family_key: Mapped[str] = mapped_column(String(160), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_boundary: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_ledger_entries.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingResearchVersion(Base):
    __tablename__ = "uw_research_versions"
    __table_args__ = (
        UniqueConstraint(
            "object_id",
            "version_kind",
            "sequence",
            name="uq_uw_research_version_sequence",
        ),
        Index(
            "ix_uw_research_versions_object_kind_sequence",
            "object_id",
            "version_kind",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    basis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_historical_bases.id"), nullable=False
    )
    version_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_research_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingAnswerabilityEvaluation(Base):
    __tablename__ = "uw_answerability_evaluations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('answerable', 'partially_answerable', 'not_answerable')",
            name="ck_uw_answerability_state",
        ),
        CheckConstraint(
            "allowed_action IN ('observe', 'wait_for_validation', "
            "'eligible_for_probe_entry', 'eligible_for_staged_entry', 'do_not_enter')",
            name="ck_uw_answerability_action",
        ),
        UniqueConstraint(
            "object_id",
            "basis_id",
            "version",
            name="uq_uw_answerability_version",
        ),
        Index(
            "ix_uw_answerability_evaluations_object_basis_version",
            "object_id",
            "basis_id",
            "version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    basis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_historical_bases.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    blockers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    research_debt_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    resolvable_within_mandate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allowed_action: Mapped[str] = mapped_column(String(48), nullable=False)
    resolution_requirements: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_answerability_evaluations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
