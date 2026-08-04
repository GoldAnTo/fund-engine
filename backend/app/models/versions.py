"""Versioned domain entities.

Design §6.1: research judgments (thesis, causal step/edge, source statement,
evidence link) must evolve by *appending a successor version* that points back
to the superseded version via ``supersedes_id``.  Corrections never overwrite
the prior version; projections pick the version valid at a ``basis`` cutoff.

Strategy (agreed): we ADD new ``*Version`` tables alongside the existing
ledger tables and a slow migration routes writes through them.  The original
tables remain the source of truth for already-published data until a read
path is flipped; new published/reviewed objects go through the version tables.

Each version carries:
  * ``version``        — monotonically increasing per logical entity family
  * ``supersedes_id``  — FK back to the prior version (NULL for the first)
  * ``applicable_from``/``applicable_to`` — business validity window
  * ``proposal_id`` / ``review_decision_id`` — provenance of how it was born
  * ``review_state``   — machine_generated | reviewed | rejected
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


# --------------------------------------------------------------------------- #
# ResearchCaseVersion
# --------------------------------------------------------------------------- #
class ResearchCaseVersion(Base):
    """Version-separated case description.

    The base ``research_cases`` row keeps identity + framing; the evolving
    summary/status live here so a case can be re-framed without losing history.
    """

    __tablename__ = "research_case_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_case_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=True
    )
    review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# ThesisVersion
# --------------------------------------------------------------------------- #
class ThesisVersion(Base):
    """Version-separated thesis (the verifiable proposition)."""

    __tablename__ = "thesis_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theses.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    support_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    falsification_condition: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    next_verification_event: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    applicable_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("thesis_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=True
    )
    review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=True
    )
    creator_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="human"
    )
    review_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="confirmed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# SourceStatementVersion
# --------------------------------------------------------------------------- #
class SourceStatementVersion(Base):
    """Version-separated atomic statement."""

    __tablename__ = "source_statement_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    observed_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Optional structured unit / metric definition for quantitative statements.
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("source_statement_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=True
    )
    review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# CausalStepVersion
# --------------------------------------------------------------------------- #
class CausalStepVersion(Base):
    """Version-separated causal step within a thesis chain."""

    __tablename__ = "causal_step_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    causal_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("causal_steps.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Scope narrows where/when the step is taken to apply.
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("causal_step_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=True
    )
    review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# CausalEdgeVersion
# --------------------------------------------------------------------------- #
class CausalEdgeVersion(Base):
    """Version-separated causal edge between two steps."""

    __tablename__ = "causal_edge_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    causal_edge_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("causal_edges.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    applicable_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("causal_edge_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=True
    )
    review_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=True
    )
    creator_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ai"
    )
    review_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="machine_generated"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# EvidenceLinkVersion
# --------------------------------------------------------------------------- #
class EvidenceLinkVersion(Base):
    """Version-separated evidence relationship, published by a review decision.

    This is the formal, reviewable edge in the graph.  AI proposals create a
    ``Proposal(kind=evidence_link)``; on ``confirmed`` the publisher appends an
    ``EvidenceLinkVersion`` (and the legacy ``evidence_links`` row for the
    transition window) carrying ``proposal_id`` + ``review_decision_id``.
    """

    __tablename__ = "evidence_link_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_links.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theses.id"), nullable=False
    )
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    applicable_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_link_versions.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=False
    )
    review_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("proposal_review_decisions.id"), nullable=False
    )
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
