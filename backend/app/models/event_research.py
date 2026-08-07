"""Append-only event framing captured when an event research case is created.

The event brief is the frozen, human-confirmed starting point of a case.  It
is deliberately separate from :class:`ResearchCase`: the generic ledger case
continues to work for older workflows, while every event has its own raw input
and AI-assisted framing that can never be silently rewritten later.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
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


class EventResearchScopeVersion(Base):
    """An immutable, ordered snapshot of an event case's active factors."""

    __tablename__ = "event_research_scope_versions"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "version", name="uq_event_research_scope_versions_case_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventResearchScopeFactor(Base):
    """One ordered factor in an immutable :class:`EventResearchScopeVersion`."""

    __tablename__ = "event_research_scope_factors"
    __table_args__ = (
        UniqueConstraint(
            "scope_version_id", "position", name="uq_event_research_scope_factors_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class EventResearchScopeEvidenceAssignment(Base):
    """Append-only classification of reviewed evidence for one scope version."""

    __tablename__ = "event_research_scope_evidence_assignments"
    __table_args__ = (
        UniqueConstraint(
            "scope_version_id",
            "evidence_link_id",
            name="uq_event_research_scope_evidence_assignments_scope_link",
        ),
        CheckConstraint(
            "disposition IN ('mapped', 'unmapped')",
            name="ck_event_research_scope_evidence_assignment_disposition",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False, index=True
    )
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_links.id"), nullable=False
    )
    factor_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventResearchConclusion(Base):
    """Append-only conclusion drafts and human-published successors.

    A published conclusion never overwrites the AI draft it was based on.  The
    evidence-link snapshot makes every conclusion independently reviewable
    even after a later research cycle adds more material.
    """

    __tablename__ = "event_research_conclusions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    primary_factor: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_link_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    based_on_conclusion_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("event_research_conclusions.id"), nullable=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
