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
from app.models import source_governance  # noqa: F401


class EventResearchBrief(Base):
    """Immutable, confirmed interpretation of the original event input."""

    __tablename__ = "event_research_briefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="pasted_snapshot")
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    event_title: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_reaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseRelation(Base):
    """Append-only reviewed or candidate relation between two Cases."""

    __tablename__ = "case_relations"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('shared_driver', 'follow_up_validation', 'potential_conflict', 'shared_material')",
            name="ck_case_relations_type",
        ),
        CheckConstraint(
            "review_state IN ('machine_generated', 'reviewed', 'rejected')",
            name="ck_case_relations_review_state",
        ),
        CheckConstraint("source_case_id <> target_case_id", name="ck_case_relations_distinct_cases"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    target_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseRelationReview(Base):
    """Append-only human review of a machine-generated Case relation."""

    __tablename__ = "case_relation_reviews"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('confirmed', 'modified', 'rejected', 'needs_more_evidence')",
            name="ck_case_relation_reviews_outcome",
        ),
        UniqueConstraint(
            "case_relation_id",
            "idempotency_key",
            name="uq_case_relation_reviews_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    case_relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("case_relations.id"), nullable=False, index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    reviewed_relation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("case_relations.id"), nullable=True
    )
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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # NULL is retained only for conclusions created before scope versions were
    # introduced. Those legacy drafts are audit records and cannot publish.
    scope_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("event_research_scope_versions.id"),
        nullable=True,
        index=True,
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
