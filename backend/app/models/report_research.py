"""Append-only claims and relationship assertions extracted from a report.

The tables in this module retain what the report asserted.  They deliberately
do *not* model the assertion as a verified company relationship: later
evidence collection is responsible for that distinction.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
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
    ChinaIndustryIndexMembership,
    ChinaIndustryIndexSnapshot,
    DocumentVersion,
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


class ReportResearchScopeVersion(Base):
    """Immutable ownership of the report document currently being researched.

    A document id is evidence provenance, not a research scope.  This ledger
    records the human/system decision that promoted an attached report version
    into a new research scope, keeping historical Wiki reads explicit and
    preventing a document revision from silently replacing prior results.
    """

    __tablename__ = "report_research_scope_versions"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id", "version", name="uq_report_scope_case_version"
        ),
        Index("ix_report_scope_case_version", "research_case_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    factor_selection: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_plan: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportResearchScopeClaim(Base):
    """One report claim deliberately included in an immutable scope."""

    __tablename__ = "report_research_scope_claims"
    __table_args__ = (
        UniqueConstraint(
            "scope_version_id", "report_claim_id", name="uq_report_scope_claim"
        ),
        Index("ix_report_scope_claim_claim", "report_claim_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_research_scope_versions.id"), nullable=False
    )
    report_claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_claims.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ReportResearchScopeRelation(Base):
    """One company-relation path deliberately included in a scope."""

    __tablename__ = "report_research_scope_relations"
    __table_args__ = (
        UniqueConstraint(
            "scope_version_id", "report_relation_id", name="uq_report_scope_relation"
        ),
        Index("ix_report_scope_relation_relation", "report_relation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_research_scope_versions.id"), nullable=False
    )
    report_relation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_relations.id"), nullable=False
    )
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


class ReportConfounderAssessment(Base):
    """Append-only causal assessment of one confounder on one relation path."""

    __tablename__ = "report_confounder_assessments"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('material', 'not_material', 'unresolved')",
            name="ck_report_confounder_assessment_outcome",
        ),
        Index(
            "ix_report_confounder_assessment_relation_created",
            "report_relation_id",
            "created_at",
        ),
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
    report_confounder_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_market_confounders.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
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


def _verified_industry_control_context(
    session: Session,
    observations: list[ReportMarketObservation],
    pending_claims: dict[uuid.UUID, ReportClaim],
) -> dict[uuid.UUID, tuple[ChinaIndustryIndexSnapshot | None, bool]]:
    """Batch-load provenance required by new verified industry controls.

    ``before_flush`` must preserve the same proof as the collection service,
    but it cannot issue one membership query for every report relation.  The
    complete candidate set is checked in memory.  The session cache expands
    by claim, relation company, and snapshot id, so the service's individual
    immutable inserts do not turn this validation into an N+1 query pattern.
    """
    candidates = [
        observation
        for observation in observations
        if observation.kind == "industry_control"
        and observation.status == "verified"
        and observation.industry_index_snapshot_id is not None
    ]
    if not candidates:
        return {}
    claim_ids = {observation.report_claim_id for observation in candidates}
    relation_ids = {
        observation.report_relation_id
        for observation in candidates
        if observation.report_relation_id is not None
    }
    snapshot_ids = {
        observation.industry_index_snapshot_id
        for observation in candidates
        if observation.industry_index_snapshot_id is not None
    }
    cache = session.info.setdefault(
        "report_industry_control_provenance",
        {
            "publications": {},
            "relations": {},
            "loaded_relation_claims": set(),
            "snapshots": {},
            "memberships": {},
        },
    )
    publication_by_claim: dict[uuid.UUID, datetime | None] = cache["publications"]
    missing_publications = claim_ids - set(publication_by_claim)
    if missing_publications:
        publication_by_claim.update(
            {
                claim_id: published_at
                for claim_id, published_at in session.execute(
                    select(ReportClaim.id, DocumentVersion.published_at)
                    .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
                    .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
                    .where(ReportClaim.id.in_(missing_publications))
                )
            }
        )
    relations: dict[uuid.UUID, ReportRelation] = cache["relations"]
    loaded_relation_claims: set[uuid.UUID] = cache["loaded_relation_claims"]
    missing_relation_claims = claim_ids - loaded_relation_claims
    if missing_relation_claims:
        relations.update(
            {
                relation.id: relation
                for relation in session.scalars(
                    select(ReportRelation).where(
                        ReportRelation.claim_id.in_(missing_relation_claims)
                    )
                )
            }
        )
        loaded_relation_claims.update(missing_relation_claims)
    relations.update(
        {
            relation.id: relation
            for relation in session.new
            if isinstance(relation, ReportRelation) and relation.id in relation_ids
        }
    )
    snapshots: dict[uuid.UUID, ChinaIndustryIndexSnapshot] = cache["snapshots"]
    missing_snapshots = snapshot_ids - set(snapshots)
    if missing_snapshots:
        snapshots.update(
            {
                snapshot.id: snapshot
                for snapshot in session.scalars(
                    select(ChinaIndustryIndexSnapshot).where(
                        ChinaIndustryIndexSnapshot.id.in_(missing_snapshots)
                    )
                )
            }
        )
    snapshots.update(
        {
            snapshot.id: snapshot
            for snapshot in session.new
            if isinstance(snapshot, ChinaIndustryIndexSnapshot)
            and snapshot.id in snapshot_ids
        }
    )
    company_ids = {
        company_id
        for relation in relations.values()
        for company_id in (relation.subject_company_id, relation.object_company_id)
        if company_id is not None
    }
    memberships_by_company: dict[uuid.UUID, list[ChinaIndustryIndexMembership]] = cache[
        "memberships"
    ]
    missing_companies = company_ids - set(memberships_by_company)
    if missing_companies:
        for membership in session.scalars(
            select(ChinaIndustryIndexMembership).where(
                ChinaIndustryIndexMembership.company_id.in_(missing_companies)
            )
        ):
            memberships_by_company.setdefault(membership.company_id, []).append(membership)
        for company_id in missing_companies:
            memberships_by_company.setdefault(company_id, [])
    for membership in session.new:
        if (
            isinstance(membership, ChinaIndustryIndexMembership)
            and membership.company_id in company_ids
            and membership not in memberships_by_company.setdefault(membership.company_id, [])
        ):
            memberships_by_company[membership.company_id].append(membership)
    result: dict[uuid.UUID, tuple[ChinaIndustryIndexSnapshot | None, bool]] = {}
    for observation in candidates:
        snapshot = snapshots.get(observation.industry_index_snapshot_id)
        relation = relations.get(observation.report_relation_id)
        published_at = publication_by_claim.get(observation.report_claim_id)
        if published_at is None and observation.report_claim_id in pending_claims:
            # Pending report documents cannot establish a historical public
            # time during the same flush; fail closed rather than guessing.
            published_at = None
        company_ids_for_relation = (
            {company_id for company_id in (relation.subject_company_id, relation.object_company_id) if company_id is not None}
            if relation is not None
            else set()
        )
        mapped = bool(snapshot and published_at and any(
            membership.company_id in company_ids_for_relation
            and membership.industry_index_id == snapshot.industry_index_id
            and membership.available_at <= published_at
            and (
                membership.applicable_from is None
                or membership.applicable_from <= published_at.date()
            )
            and (
                membership.applicable_to is None
                or membership.applicable_to >= published_at.date()
            )
            for company_id in company_ids_for_relation
            for membership in memberships_by_company.get(company_id, ())
        ))
        result[observation.id] = (snapshot, mapped)
    return result


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

    for scope in session.new:
        if not isinstance(scope, ReportResearchScopeVersion):
            continue
        attached = session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == scope.research_case_id,
                CaseDocumentVersion.document_version_id == scope.document_version_id,
            )
        )
        if attached is None and not any(
            isinstance(binding, CaseDocumentVersion)
            and binding.research_case_id == scope.research_case_id
            and binding.document_version_id == scope.document_version_id
            for binding in session.new
        ):
            raise ValueError("report scope document must be attached to its research case")
        selected_span = session.scalar(
            select(ReportCaseSourceSpan.id).where(
                ReportCaseSourceSpan.research_case_id == scope.research_case_id,
                ReportCaseSourceSpan.document_version_id == scope.document_version_id,
            )
        )
        if selected_span is None and not any(
            isinstance(binding, ReportCaseSourceSpan)
            and binding.research_case_id == scope.research_case_id
            and binding.document_version_id == scope.document_version_id
            for binding in session.new
        ):
            raise ValueError("report scope document must have report source spans")

    pending_scope_claims: dict[uuid.UUID, set[uuid.UUID]] = {}
    for selection in session.new:
        if not isinstance(selection, ReportResearchScopeClaim):
            continue
        scope = session.get(ReportResearchScopeVersion, selection.scope_version_id)
        claim = session.get(ReportClaim, selection.report_claim_id)
        if scope is None or claim is None:
            raise ValueError("report scope claim must reference an existing scope and claim")
        claim_document_id = session.scalar(
            select(SourceSpan.document_version_id).where(SourceSpan.id == claim.source_span_id)
        )
        if (
            claim.research_case_id != scope.research_case_id
            or claim_document_id != scope.document_version_id
        ):
            raise ValueError("report scope claim must belong to its scope case and document")
        pending_scope_claims.setdefault(selection.scope_version_id, set()).add(
            selection.report_claim_id
        )

    for selection in session.new:
        if not isinstance(selection, ReportResearchScopeRelation):
            continue
        scope = session.get(ReportResearchScopeVersion, selection.scope_version_id)
        relation = session.get(ReportRelation, selection.report_relation_id)
        if scope is None or relation is None:
            raise ValueError("report scope relation must reference an existing scope and relation")
        claim = session.get(ReportClaim, relation.claim_id)
        if claim is None:
            raise ValueError("report scope relation must reference a relation claim")
        selected_claim = relation.claim_id in pending_scope_claims.get(
            selection.scope_version_id, set()
        ) or session.scalar(
            select(ReportResearchScopeClaim.id)
            .where(ReportResearchScopeClaim.scope_version_id == selection.scope_version_id)
            .where(ReportResearchScopeClaim.report_claim_id == relation.claim_id)
        )
        claim_document_id = session.scalar(
            select(SourceSpan.document_version_id).where(SourceSpan.id == claim.source_span_id)
        )
        if (
            not selected_claim
            or relation.research_case_id != scope.research_case_id
            or claim.research_case_id != scope.research_case_id
            or claim_document_id != scope.document_version_id
        ):
            raise ValueError("report scope relation must belong to its selected claim, case and document")

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

    pending_observations = [
        observation
        for observation in session.new
        if isinstance(observation, ReportMarketObservation)
    ]
    industry_context = _verified_industry_control_context(
        session, pending_observations, pending_claims
    )

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

    for observation in pending_observations:
        claim = pending_claims.get(observation.report_claim_id) or session.get(
            ReportClaim, observation.report_claim_id
        )
        if claim is None or claim.research_case_id != observation.research_case_id:
            raise ValueError("report market observation must belong to its claim case")
        relation: ReportRelation | None = None
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
                context = industry_context.get(observation.id)
                snapshot = context[0] if context is not None else None
                if (
                    snapshot is None
                    or snapshot.as_of_date != observation.as_of_date
                    or snapshot.metric_name != observation.metric_name
                ):
                    raise ValueError(
                        "industry control must reference its exact industry index snapshot"
                    )
                window_end = datetime.combine(
                    observation.as_of_date, time.max, tzinfo=timezone.utc
                )
                snapshot_available_at = snapshot.available_at
                if snapshot_available_at.tzinfo is None:
                    # SQLite returns DateTime(timezone=True) as a naive UTC
                    # value; PostgreSQL retains the offset.  Ledger times
                    # are UTC, so compare the two representations uniformly.
                    snapshot_available_at = snapshot_available_at.replace(
                        tzinfo=timezone.utc
                    )
                if snapshot_available_at > window_end:
                    raise ValueError(
                        "industry control snapshot was not visible by the market window"
                    )
                if context is None or not context[1]:
                    raise ValueError(
                        "industry control has no point-in-time industry mapping "
                        "for either relation company"
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

    pending_confounders = {
        confounder.id: confounder
        for confounder in session.new
        if isinstance(confounder, ReportMarketConfounder)
    }
    pending_relations = {
        relation.id: relation
        for relation in session.new
        if isinstance(relation, ReportRelation)
    }
    for assessment in session.new:
        if not isinstance(assessment, ReportConfounderAssessment):
            continue
        claim = pending_claims.get(assessment.report_claim_id) or session.get(
            ReportClaim, assessment.report_claim_id
        )
        relation = pending_relations.get(assessment.report_relation_id) or session.get(
            ReportRelation, assessment.report_relation_id
        )
        confounder = pending_confounders.get(
            assessment.report_confounder_id
        ) or session.get(ReportMarketConfounder, assessment.report_confounder_id)
        if (
            claim is None
            or relation is None
            or confounder is None
            or assessment.research_case_id != claim.research_case_id
            or relation.claim_id != claim.id
            or confounder.report_claim_id != claim.id
        ):
            raise ValueError(
                "report confounder assessment must belong to one claim relation path"
            )

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
