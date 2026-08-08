"""Append-only event-impact hypotheses, relations, reviews, and observations."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Base, _uuid


class EventImpactHypothesis(Base):
    """A scope-bound candidate explanation of an event's impact."""

    __tablename__ = "event_impact_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "classification IN ('candidate', 'key', 'alternative', 'background', 'unresolved')",
            name="ck_event_impact_hypotheses_classification",
        ),
        Index(
            "ix_event_impact_hypotheses_case_scope_rank",
            "research_case_id",
            "scope_version_id",
            "rank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False
    )
    scope_version: Mapped[EventResearchScopeVersion] = relationship(
        foreign_keys=[scope_version_id]
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
        Index(
            "ix_company_impact_relations_hypothesis_company",
            "hypothesis_id",
            "affected_company_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_impact_hypotheses.id"), nullable=False
    )
    hypothesis: Mapped[EventImpactHypothesis] = relationship(
        foreign_keys=[hypothesis_id]
    )
    # Duplicated from the owning hypothesis to make scope-bound reads and
    # history checks direct; writers must set it to that hypothesis's scope.
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False
    )
    affected_company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=False
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
        Index(
            "ix_company_impact_relation_reviews_relation_created",
            "relation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_impact_relations.id"), nullable=False
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
        Index(
            "ix_company_impact_observations_relation_kind_status",
            "relation_id",
            "kind",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("company_impact_relations.id"), nullable=False
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


class EventImpactRefreshClaim(Base):
    """Immutable, unique claim for one scope-specific impact refresh key."""

    __tablename__ = "event_impact_refresh_claims"
    __table_args__ = (
        UniqueConstraint(
            "scope_version_id", "refresh_key", name="uq_event_impact_refresh_claim"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), nullable=True
    )
    refresh_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@event.listens_for(Session, "before_flush")
def _derive_relation_scopes_before_flush(session, _flush_context, _instances) -> None:
    """Bind pending relations to their parent's scope before inserts begin."""

    for hypothesis in session.new:
        if not isinstance(hypothesis, EventImpactHypothesis):
            continue
        scope_version = hypothesis.scope_version
        if scope_version is None and hypothesis.scope_version_id is not None:
            scope_version = session.get(
                EventResearchScopeVersion, hypothesis.scope_version_id
            )
        if scope_version is None:
            raise ValueError("scope_version_id must reference an existing scope version")
        if scope_version.research_case_id != hypothesis.research_case_id:
            raise ValueError("scope_version_id must belong to the research_case_id")

    pending_hypotheses = {
        hypothesis.id: hypothesis
        for hypothesis in session.new
        if isinstance(hypothesis, EventImpactHypothesis) and hypothesis.id is not None
    }
    for relation in session.new:
        if not isinstance(relation, CompanyImpactRelation):
            continue
        hypothesis = relation.hypothesis
        if hypothesis is None:
            hypothesis = pending_hypotheses.get(relation.hypothesis_id)
        if hypothesis is None and relation.hypothesis_id is not None:
            hypothesis = session.get(EventImpactHypothesis, relation.hypothesis_id)
        if hypothesis is None:
            raise ValueError("hypothesis_id must reference an existing impact hypothesis")
        if relation.scope_version_id is None:
            relation.scope_version_id = hypothesis.scope_version_id
        elif relation.scope_version_id != hypothesis.scope_version_id:
            raise ValueError("scope_version_id must match the owning hypothesis scope")
