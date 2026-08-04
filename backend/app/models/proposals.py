"""Unified proposal + review-decision model.

Design §5.3 / §6.2: AI may propose four kinds of change — ``statement``,
``evidence_link``, ``causal_edge``, ``entity_alignment`` — and a human resolves
each via a single ``ProposalReviewDecision`` (confirmed | modified | rejected).

KEY INVARIANT (design §9.2): an AI proposal NEVER becomes a reviewed relation
on its own.  The publisher appends the formal versioned entity only after a
``confirmed`` / ``modified`` decision, carrying the proposal_id +
decision_id for provenance.  ``rejected`` keeps both the proposal and the
reason on record.

Concurrency: every decision carries ``expected_proposal_version``; a mismatch
raises a review conflict (409) so two reviewers cannot both decide the same
proposal.

Note: the legacy ``review_decisions`` (assessment review) and ``evidence_reviews``
(link review) tables are retained for the existing prototype review workspace.
New proposals flow through this unified table; the two can be reconciled later
once the prototype review UI moves onto the proposal queue.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


ProposalKind = Literal["statement", "evidence_link", "causal_edge", "entity_alignment"]
ProposalStatus = Literal["pending", "decided", "withdrawn"]
ProposalDecisionOutcome = Literal["confirmed", "modified", "rejected"]


class Proposal(Base):
    """An AI (or human) suggested change awaiting human resolution."""

    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # The concrete suggested change, schema-dependent on ``kind``.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Where the proposal applies (case / thesis / statement / edge ids).
    target_context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # "ai" | "human"
    proposed_by_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ai"
    )
    # Ref to the proposer (model id, or human id).  NOT trusted as identity
    # once auth lands — resolved through the actor principal.
    proposed_by_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The cutoff the proposer used, so reviewers see the same evidence window.
    basis_cutoff: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Entity ids the proposal consumed as input (for traceability / scope).
    input_entity_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Content hash of payload+context, used to dedupe identical proposals.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Monotonic version for optimistic-concurrency on the decision endpoint.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Set when a decision is recorded.
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The originating AI run, for audit.
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # The research case this proposal is scoped to (for queue filtering).
    research_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=True
    )


class ProposalReviewDecision(Base):
    """A human resolution of one Proposal (design §6.2).

    ``modified`` does not mutate the Proposal; the publisher reads
    ``replacement_payload`` to append the corrected formal version.  Both
    ``modified`` and ``rejected`` preserve the original proposal + reason.
    """

    __tablename__ = "proposal_review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    # Present only when outcome == "modified"; the corrected change to publish.
    replacement_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The version of the proposal the reviewer based the decision on.
    expected_proposal_version: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
