"""Append-only claims and relationship assertions extracted from a report.

The tables in this module retain what the report asserted.  They deliberately
do *not* model the assertion as a verified company relationship: later
evidence collection is responsible for that distinction.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.models.ledger import (
    Base,
    CaseDocumentVersion,
    ChinaIndustryIndexSnapshot,
    SourceSpan,
    SourceStatement,
    _uuid,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReportCaseSourceSpan(Base):
    """Immutable selection of the exact source spans owned by one report case.

    A DocumentVersion can be replayed by multiple cases.  This companion
    ledger prevents one case's extractor from scanning spans appended by a
    later replay or by another case that happened to reuse the same bytes.
    """

    __tablename__ = "report_case_source_spans"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "source_span_id", name="uq_report_case_source_span"
        ),
        Index(
            "ix_report_case_source_spans_case_document",
            "research_case_id",
            "document_version_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    source_span_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_spans.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportExtractionClaim(Base):
    """Durable, immutable exactly-once claim for one report extraction input."""

    __tablename__ = "report_extraction_claims"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id",
            "document_version_id",
            "extractor_version",
            "input_fingerprint",
            name="uq_report_extraction_claim",
        ),
        Index(
            "ix_report_extraction_claims_case_document",
            "research_case_id",
            "document_version_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportClaim(Base):
    """One source-backed opinion, forecast, assumption, or risk in a report."""

    __tablename__ = "report_claims"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('report_opinion', 'report_forecast', 'report_assumption', 'report_risk')",
            name="ck_report_claims_kind",
        ),
        Index("ix_report_claims_case_span", "research_case_id", "source_span_id"),
        Index("ix_report_claims_statement", "source_statement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    # The duplicated span key enables graph queries to recover a page or
    # paragraph locator without relying on a mutable extraction projection.
    source_span_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_spans.id"), nullable=False
    )
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    relations: Mapped[list["ReportRelation"]] = relationship(
        back_populates="claim", foreign_keys="ReportRelation.claim_id"
    )


class ReportRelation(Base):
    """A company or named-node relationship asserted by a report claim.

    A relation may point at a known Company.  If extraction cannot resolve a
    name safely, the corresponding ``*_company_id`` remains null and the
    normalized name is kept as an unresolved named node.  This is essential
    for unlisted suppliers/customers/competitors and avoids fabricated assets.
    """

    __tablename__ = "report_relations"
    __table_args__ = (
        CheckConstraint(
            "status = 'report_claim'", name="ck_report_relations_status"
        ),
        CheckConstraint(
            "subject_company_id IS NOT NULL OR subject_name IS NOT NULL",
            name="ck_report_relations_subject_node",
        ),
        CheckConstraint(
            "object_company_id IS NOT NULL OR object_name IS NOT NULL",
            name="ck_report_relations_object_node",
        ),
        Index("ix_report_relations_case_claim", "research_case_id", "claim_id"),
        Index(
            "ix_report_relations_subject_company", "research_case_id", "subject_company_id"
        ),
        Index(
            "ix_report_relations_object_company", "research_case_id", "object_company_id"
        ),
        Index("ix_report_relations_statement", "source_statement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_claims.id"), nullable=False
    )
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    source_span_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_spans.id"), nullable=False
    )
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    subject_company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=True
    )
    object_company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=True
    )
    subject_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    relation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    mechanism: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="report_claim")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    claim: Mapped[ReportClaim] = relationship(
        back_populates="relations", foreign_keys=[claim_id]
    )


class ReportMarketObservation(Base):
    """An immutable, ledger-backed observation in a report market window.

    This stores the *fact observed* rather than a causal verdict.  The
    referenced valuation snapshot remains the numerical source of truth;
    rows with ``status='insufficient'`` deliberately have no snapshot so a
    missing data point can never be mistaken for a zero return.
    """

    __tablename__ = "report_market_observations"
    __table_args__ = (
        CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_market_window"),
        CheckConstraint(
            "kind IN ('target_market', 'peer_control', 'industry_control')",
            name="ck_report_market_observation_kind",
        ),
        CheckConstraint(
            "status IN ('verified', 'insufficient')",
            name="ck_report_market_observation_status",
        ),
        CheckConstraint(
            "(kind = 'industry_control' AND status = 'verified' "
            "AND industry_index_snapshot_id IS NOT NULL "
            "AND stock_id IS NULL AND valuation_snapshot_id IS NULL) "
            "OR (kind = 'industry_control' AND status != 'verified' "
            "AND industry_index_snapshot_id IS NULL) "
            "OR (kind != 'industry_control' AND industry_index_snapshot_id IS NULL)",
            name="ck_report_market_industry_snapshot_source",
        ),
        UniqueConstraint("collection_key", name="uq_report_market_observation_key"),
        Index(
            "ix_report_market_observations_claim_window",
            "report_claim_id",
            "window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    report_claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_claims.id"), nullable=False
    )
    report_relation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("report_relations.id"), nullable=True
    )
    stock_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stocks.id"), nullable=True
    )
    valuation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("valuation_snapshots.id"), nullable=True
    )
    # ``industry_control`` never borrows a target stock metric.  When it is
    # verified this is the immutable ChinaIndustryIndexSnapshot actually used
    # as the control; target/peer observations leave it NULL.
    industry_index_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("china_industry_index_snapshots.id"),
        nullable=True,
    )
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(nullable=True)
    metric_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    collection_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportMarketConfounder(Base):
    """One same-window announcement, earnings, policy, or news statement."""

    __tablename__ = "report_market_confounders"
    __table_args__ = (
        CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_confounder_window"),
        CheckConstraint(
            "kind IN ('announcement', 'earnings', 'policy', 'news')",
            name="ck_report_confounder_kind",
        ),
        UniqueConstraint("collection_key", name="uq_report_market_confounder_key"),
        Index("ix_report_market_confounders_claim_window", "report_claim_id", "window"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    report_claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_claims.id"), nullable=False
    )
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    as_of_date: Mapped[date] = mapped_column(nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    collection_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportFundExposure(Base):
    """Point-in-time China public-fund holding mapped to a report relation."""

    __tablename__ = "report_fund_exposures"
    __table_args__ = (
        CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_fund_window"),
        CheckConstraint(
            "status IN ('verified', 'insufficient')",
            name="ck_report_fund_exposure_status",
        ),
        UniqueConstraint("collection_key", name="uq_report_fund_exposure_key"),
        Index("ix_report_fund_exposures_claim_window", "report_claim_id", "window"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    report_claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_claims.id"), nullable=False
    )
    report_relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_relations.id"), nullable=False
    )
    stock_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stocks.id"), nullable=False)
    fund_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("funds.id"), nullable=True)
    holding_disclosure_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("holding_disclosures.id"), nullable=True
    )
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    collection_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


@event.listens_for(Session, "before_flush")
def _validate_report_ledger_provenance(session, _flush_context, _instances) -> None:
    """Keep denormalized graph keys bound to their immutable source ledger.

    Extraction writes these values in one place, but the ORM is also used by
    imports and administrative scripts.  Rejecting mismatches here prevents a
    report relation from borrowing another case's page/paragraph evidence.
    """

    pending_bindings = [
        binding
        for binding in session.new
        if isinstance(binding, ReportCaseSourceSpan)
    ]
    for binding in pending_bindings:
        span_document_id = session.scalar(
            select(SourceSpan.document_version_id).where(
                SourceSpan.id == binding.source_span_id
            )
        )
        if span_document_id != binding.document_version_id:
            raise ValueError("report case span must belong to its document version")
        attached = session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == binding.research_case_id,
                CaseDocumentVersion.document_version_id == binding.document_version_id,
            )
        )
        if attached is None:
            raise ValueError("report case span document is not attached to its research case")

    pending_claims = {
        claim.id: claim
        for claim in session.new
        if isinstance(claim, ReportClaim)
    }
    for claim in pending_claims.values():
        source_statement = session.get(SourceStatement, claim.source_statement_id)
        if (
            source_statement is None
            or source_statement.source_span_id != claim.source_span_id
        ):
            raise ValueError("report claim statement must belong to its source span")
        selected = session.scalar(
            select(ReportCaseSourceSpan.id).where(
                ReportCaseSourceSpan.research_case_id == claim.research_case_id,
                ReportCaseSourceSpan.source_span_id == claim.source_span_id,
            )
        )
        if selected is None:
            raise ValueError("report claim source span is not selected for its research case")

    for extraction in session.new:
        if not isinstance(extraction, ReportExtractionClaim):
            continue
        selected = session.scalar(
            select(ReportCaseSourceSpan.id).where(
                ReportCaseSourceSpan.research_case_id == extraction.research_case_id,
                ReportCaseSourceSpan.document_version_id
                == extraction.document_version_id,
            )
        )
        if selected is None:
            raise ValueError("report extraction document has no selected source spans")

    for relation in session.new:
        if not isinstance(relation, ReportRelation):
            continue
        claim = pending_claims.get(relation.claim_id) or session.get(
            ReportClaim, relation.claim_id
        )
        if claim is None:
            # Foreign-key enforcement supplies the database-level error; this
            # clearer exception protects the service boundary on SQLite too.
            raise ValueError("report relation references unknown claim")
        if (
            relation.research_case_id != claim.research_case_id
            or relation.source_span_id != claim.source_span_id
            or relation.source_statement_id != claim.source_statement_id
        ):
            raise ValueError("report relation provenance must match its claim")

    for observation in session.new:
        if not isinstance(observation, ReportMarketObservation):
            continue
        claim = pending_claims.get(observation.report_claim_id) or session.get(
            ReportClaim, observation.report_claim_id
        )
        if claim is None or claim.research_case_id != observation.research_case_id:
            raise ValueError("report market observation must belong to its claim case")
        if observation.report_relation_id is not None:
            relation = session.get(ReportRelation, observation.report_relation_id)
            if relation is None or relation.claim_id != claim.id:
                raise ValueError("report market observation relation must belong to its claim")
        if observation.kind == "industry_control":
            if observation.status == "verified":
                if (
                    observation.industry_index_snapshot_id is None
                    or observation.stock_id is not None
                    or observation.valuation_snapshot_id is not None
                ):
                    raise ValueError(
                        "verified industry control must use only an industry index snapshot"
                    )
                snapshot = session.get(
                    ChinaIndustryIndexSnapshot,
                    observation.industry_index_snapshot_id,
                )
                if (
                    snapshot is None
                    or snapshot.as_of_date != observation.as_of_date
                    or snapshot.metric_name != observation.metric_name
                ):
                    raise ValueError(
                        "industry control must reference its exact industry index snapshot"
                    )
            elif observation.industry_index_snapshot_id is not None:
                raise ValueError("insufficient industry control cannot cite an index snapshot")
        elif observation.industry_index_snapshot_id is not None:
            raise ValueError("only industry control can cite an industry index snapshot")

    for confounder in session.new:
        if not isinstance(confounder, ReportMarketConfounder):
            continue
        claim = pending_claims.get(confounder.report_claim_id) or session.get(
            ReportClaim, confounder.report_claim_id
        )
        if claim is None or claim.research_case_id != confounder.research_case_id:
            raise ValueError("report market confounder must belong to its claim case")
        document_id = session.scalar(
            select(SourceSpan.document_version_id)
            .join(SourceStatement, SourceStatement.source_span_id == SourceSpan.id)
            .where(SourceStatement.id == confounder.source_statement_id)
        )
        attached = (
            session.scalar(
                select(CaseDocumentVersion.id)
                .where(CaseDocumentVersion.research_case_id == claim.research_case_id)
                .where(CaseDocumentVersion.document_version_id == document_id)
            )
            if document_id is not None
            else None
        )
        if attached is None:
            raise ValueError("report market confounder source must be attached to its claim case")

    for exposure in session.new:
        if not isinstance(exposure, ReportFundExposure):
            continue
        claim = pending_claims.get(exposure.report_claim_id) or session.get(
            ReportClaim, exposure.report_claim_id
        )
        relation = session.get(ReportRelation, exposure.report_relation_id)
        if (
            claim is None
            or relation is None
            or claim.research_case_id != exposure.research_case_id
            or relation.claim_id != claim.id
        ):
            raise ValueError("report fund exposure must belong to its claim relation")
