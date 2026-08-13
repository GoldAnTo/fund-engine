"""Persistence for the pre-authorization research preparation workbench."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class ResearchPreparation(Base):
    """The single mutable preparation row for a research case."""

    __tablename__ = "research_preparations"
    __table_args__ = (
        UniqueConstraint("research_case_id", name="uq_research_preparations_research_case"),
        ForeignKeyConstraint(
            ["research_case_id", "research_run_id"],
            ["research_runs.research_case_id", "research_runs.id"],
            name="fk_research_preparations_case_run",
        ),
        CheckConstraint(
            "status IN ('preparing', 'awaiting_claim_review', "
            "'awaiting_protocol_confirmation', 'awaiting_plan_authorization', "
            "'recoverable_failure', 'authorized')",
            name="ck_research_preparations_status",
        ),
        CheckConstraint(
            "parse_claims_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_parse_claims_state",
        ),
        CheckConstraint(
            "draft_protocol_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_draft_protocol_state",
        ),
        CheckConstraint(
            "draft_evidence_plan_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_draft_evidence_plan_state",
        ),
        CheckConstraint(
            "claim_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_claim_review_state",
        ),
        CheckConstraint(
            "protocol_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_protocol_review_state",
        ),
        CheckConstraint(
            "plan_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_plan_review_state",
        ),
        CheckConstraint(
            "(status = 'authorized' AND research_run_id IS NOT NULL) OR "
            "(status <> 'authorized' AND research_run_id IS NULL)",
            name="ck_research_preparations_authorized_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_cases.id", name="fk_research_preparations_research_case_id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    parse_claims_state: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_protocol_state: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_evidence_plan_state: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    protocol_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchPreparationArtifact(Base):
    """Append-only artifact versions produced by a preparation step."""

    __tablename__ = "research_preparation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "research_preparation_id",
            "sequence",
            name="uq_research_preparation_artifacts_preparation_sequence",
        ),
        Index(
            "ix_research_preparation_artifacts_preparation_kind_state",
            "research_preparation_id",
            "kind",
            "state",
        ),
        Index(
            "uq_research_preparation_artifacts_current_kind",
            "research_preparation_id",
            "kind",
            unique=True,
            sqlite_where=text("state = 'current'"),
            postgresql_where=text("state = 'current'"),
        ),
        CheckConstraint(
            "kind IN ('atomic_claim_candidates', 'research_protocol_draft', 'evidence_acquisition_plan')",
            name="ck_research_preparation_artifacts_kind",
        ),
        CheckConstraint(
            "state IN ('current', 'stale', 'superseded')",
            name="ck_research_preparation_artifacts_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_preparations.id",
            name="fk_research_preparation_artifacts_preparation_id",
        ),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    preparation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    context_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchPreparationEvent(Base):
    """Append-only event stream for one preparation."""

    __tablename__ = "research_preparation_events"
    __table_args__ = (
        UniqueConstraint(
            "research_preparation_id",
            "seq",
            name="uq_research_preparation_events_preparation_seq",
        ),
        CheckConstraint(
            "step IS NULL OR step IN ('parse_claims', 'draft_protocol', 'draft_evidence_plan')",
            name="ck_research_preparation_events_step",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_preparations.id",
            name="fk_research_preparation_events_preparation_id",
        ),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
