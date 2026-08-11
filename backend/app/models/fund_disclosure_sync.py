"""Immutable configuration and replay log for Case-scoped fund disclosures."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class FundDisclosureSyncConfigVersion(Base):
    """A saved, human-configurable scope for fund-disclosure replenishment."""

    __tablename__ = "fund_disclosure_sync_config_versions"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "version", name="uq_fund_disclosure_sync_config_case_version"
        ),
        CheckConstraint(
            "frequency IN ('weekly', 'monthly')", name="ck_fund_disclosure_sync_config_frequency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    # Nullable only for versions created before the frozen-period contract.
    # New configurations are validated by the service and always set it.
    report_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    fund_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    stock_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allow_display: Mapped[bool] = mapped_column(nullable=False, default=False)
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FundDisclosureSyncRun(Base):
    """Frozen inputs for one immediate or scheduled fund-disclosure run."""

    __tablename__ = "fund_disclosure_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'retry')", name="ck_fund_disclosure_sync_runs_trigger"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    config_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("fund_disclosure_sync_config_versions.id"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    # Nullable only for legacy run history. A new executable run always
    # snapshots the period from its immutable configuration version.
    report_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    fund_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    stock_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allow_display: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FundDisclosureSyncRunEvent(Base):
    """Ordered, append-only details explaining each fund-disclosure run."""

    __tablename__ = "fund_disclosure_sync_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_fund_disclosure_sync_run_events_run_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("fund_disclosure_sync_runs.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
