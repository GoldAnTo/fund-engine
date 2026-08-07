"""Append-only event framing captured when an event research case is created.

The event brief is the frozen, human-confirmed starting point of a case.  It
is deliberately separate from :class:`ResearchCase`: the generic ledger case
continues to work for older workflows, while every event has its own raw input
and AI-assisted framing that can never be silently rewritten later.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class EventResearchBrief(Base):
    """Immutable, confirmed interpretation of the original event input."""

    __tablename__ = "event_research_briefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_title: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_reaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventResearchFactorDraft(Base):
    """Immutable candidate factor selected at event-research creation time."""

    __tablename__ = "event_research_factor_drafts"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "position", name="uq_event_research_factor_drafts_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
