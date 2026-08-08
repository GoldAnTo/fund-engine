"""Append-only event-impact hypotheses, relations, reviews, and observations."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    event,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class EventImpactHypothesis(Base):
    """A scope-bound candidate explanation of an event's impact."""

    __tablename__ = "event_impact_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('candidate', 'key', 'alternative', 'background', 'unresolved')",
            name="ck_event_impact_hypotheses_classification",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score_components: Mapped[dict] = mapped_column(JSON, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyImpactRelation(Base):
    """A company transmission relation belonging to one impact hypothesis."""

    __tablename__ = "company_impact_relations"
    __table_args__ = (
        CheckConstraint(
            "relation_kind IN ('supplier', 'customer', 'competitor', 'partner', 'industry_peer')",
            name="ck_company_impact_relations_relation_kind",
        ),
        CheckConstraint(
            "direction IN ('benefits', 'harms', 'mixed', 'unknown')",
            name="ck_company_impact_relations_direction",
        ),
        CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
            name="ck_company_impact_relations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_impact_hypotheses.id"), nullable=False, index=True
    )
    # Duplicated from the owning hypothesis to make scope-bound reads and
    # history checks direct; writers must set it to that hypothesis's scope.
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False, index=True
    )
    affected_company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=False, index=True
    )
    relation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    mechanism: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyImpactRelationReview(Base):
    """An append-only human outcome for a company impact relation."""

    __tablename__ = "company_impact_relation_reviews"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'needs_more')",
            name="ck_company_impact_relation_reviews_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_impact_relations.id"), nullable=False, index=True
    )
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyImpactObservation(Base):
    """An append-only, typed observation supporting or rejecting a relation."""

    __tablename__ = "company_impact_observations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('event', 'relation', 'operating', 'market', 'peer_control', 'fund')",
            name="ck_company_impact_observations_kind",
        ),
        CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
            name="ck_company_impact_observations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_impact_relations.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=True
    )
    valuation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("valuation_snapshots.id"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@event.listens_for(CompanyImpactRelation, "before_insert")
def _derive_relation_scope_from_hypothesis(_mapper, connection, target) -> None:
    """Prevent a relation from crossing the owning hypothesis's scope boundary."""

    hypothesis_scope_id = connection.scalar(
        select(EventImpactHypothesis.scope_version_id).where(
            EventImpactHypothesis.id == target.hypothesis_id
        )
    )
    if hypothesis_scope_id is None:
        raise ValueError("hypothesis_id must reference an existing impact hypothesis")
    if target.scope_version_id is None:
        target.scope_version_id = hypothesis_scope_id
    elif target.scope_version_id != hypothesis_scope_id:
        raise ValueError("scope_version_id must match the owning hypothesis scope")
