"""Immutable monitor configuration and transparent automatic-research history."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class CaseMonitorVersion(Base):
    """One human-approved, replayable monitor configuration for a Case.

    The active monitor is the newest version.  Configurations are not edited
    in place: retaining every version makes both scheduled and manual runs
    explainable against the exact scope that authorized them.
    """

    __tablename__ = "case_monitor_versions"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "version", name="uq_case_monitor_versions_case_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    frequency: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_source_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    next_verification_event: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchRunEvent(Base):
    """Append-only, ordered activity record for one automatic research run."""

    __tablename__ = "research_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_research_run_events_run_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
