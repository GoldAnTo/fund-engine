"""Append-only metric definitions and fixed research outcomes."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class MetricDefinitionVersion(Base):
    __tablename__ = "metric_definition_versions"
    __table_args__ = (UniqueConstraint("metric_id", "version", name="uq_metric_definition_versions_metric_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_definition: Mapped[str] = mapped_column(Text, nullable=False)
    entity_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    period_semantics: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_source_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    role_eligibility: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("metric_definition_versions.id"), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutcomeBindingVersion(Base):
    __tablename__ = "outcome_binding_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("theses.id"), nullable=False, index=True)
    metric_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("metric_definition_versions.id"), nullable=False)
    entity_scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False)
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("outcome_binding_versions.id"), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MechanismTemplateVersion(Base):
    __tablename__ = "mechanism_template_versions"
    __table_args__ = (UniqueConstraint("template_key", "version", name="uq_mechanism_template_versions_key_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    template_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    industry_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("mechanism_template_versions.id"), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MechanismNodeVersion(Base):
    __tablename__ = "mechanism_node_versions"
    __table_args__ = (UniqueConstraint("template_version_id", "node_key", name="uq_mechanism_node_versions_template_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    template_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_template_versions.id"), nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MechanismEdgeVersion(Base):
    __tablename__ = "mechanism_edge_versions"
    __table_args__ = (UniqueConstraint("template_version_id", "edge_key", name="uq_mechanism_edge_versions_template_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    template_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_template_versions.id"), nullable=False, index=True)
    edge_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_node_versions.id"), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_node_versions.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseMechanismSelectionVersion(Base):
    __tablename__ = "case_mechanism_selection_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    template_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_template_versions.id"), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("case_mechanism_selection_versions.id"), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VerificationRuleVersion(Base):
    __tablename__ = "verification_rule_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # Template edges are reusable; reviewers configure the rule within one Case.
    # The column remains nullable only to preserve already-recorded pre-scope rows.
    research_case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=True, index=True)
    mechanism_edge_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_edge_versions.id"), nullable=False, index=True)
    metric_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("metric_definition_versions.id"), nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    support_predicate: Mapped[str] = mapped_column(Text, nullable=False)
    contradiction_predicate: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_source_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    observed_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    observed_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    available_at_deadline: Mapped[date] = mapped_column(Date, nullable=False)
    next_verification_event: Mapped[str] = mapped_column(String(256), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("verification_rule_versions.id"), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
