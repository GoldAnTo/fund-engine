"""Append-only claims and relationship assertions extracted from a report.

The tables in this module retain what the report asserted.  They deliberately
do *not* model the assertion as a verified company relationship: later
evidence collection is responsible for that distinction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.models.ledger import Base, CaseDocumentVersion, SourceSpan, SourceStatement, _uuid


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
