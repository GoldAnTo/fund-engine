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
