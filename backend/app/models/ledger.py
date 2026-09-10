"""Immutable evidence ledger tables.

Every ledger entity is append-only: no UPDATE or DELETE path is exposed.
Corrections append a successor record carrying ``supersedes_id``.

Immutability is enforced at two layers:
1. Application layer: a SQLAlchemy ``before_execute`` guard rejects any
   UPDATE/DELETE targeting an immutable table, raising ImmutableLedgerError.
2. Database layer: PostgreSQL triggers (see Alembic migration 0001) raise on
   UPDATE/DELETE as defence-in-depth against connections bypassing the app.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import DateTime, Date, ForeignKey, JSON, Numeric, String, Text, Uuid, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.dml import Delete, Update, UpdateBase

AssessmentStatus = Literal["supported", "contradicted", "insufficient_evidence"]
EvidenceRole = Literal["supports", "contradicts", "contextualizes"]
SourceStatementKind = Literal[
    "disclosed_fact", "management_attribution", "forecast", "research_opinion"
]
ReviewOutcome = Literal["confirmed", "modified", "rejected"]
ReviewState = Literal["machine_generated", "reviewed", "rejected"]
AIRunKind = Literal["extract", "propose", "assess"]
AIRunStatus = Literal["success", "failed"]

IMMUTABLE_TABLES = frozenset(
    {
        "document_versions",
        "source_spans",
        "research_cases",
        "theses",
        "causal_steps",
        "causal_edges",
        "source_statements",
        "evidence_links",
        "evidence_snapshots",
        "ai_assessments",
        "review_decisions",
        "companies",
        "stocks",
        "fund_companies",
        "funds",
        "valuation_snapshots",
        "holding_disclosures",
        "theme_roles",
        "ai_runs",
    }
)


class ImmutableLedgerError(Exception):
    """Raised on any attempt to UPDATE or DELETE an append-only ledger table."""


class ValidationError(Exception):
    """Raised when a service-layer validation fails."""


class Base(DeclarativeBase):
    pass


def _target_table_name(stmt: UpdateBase) -> str | None:
    table = getattr(stmt, "table", None)
    if table is not None and hasattr(table, "name"):
        return table.name
    try:
        froms = list(stmt.get_final_froms())
    except Exception:
        froms = []
    for clause in froms:
        if hasattr(clause, "name"):
            return clause.name
    return None


@event.listens_for(Engine, "before_execute")
def _guard_immutable_tables(*args: Any, **kwargs: Any) -> None:
    statement = kwargs.get("statement")
    if statement is None and len(args) >= 2:
        statement = args[1]
    if isinstance(statement, (Update, Delete)):
        name = _target_table_name(statement)
        if name in IMMUTABLE_TABLES:
            raise ImmutableLedgerError(
                f"table '{name}' is append-only: UPDATE/DELETE is not allowed"
            )


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=True
    )


class SourceSpan(Base):
    __tablename__ = "source_spans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)


class ResearchCase(Base):
    __tablename__ = "research_cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    industry_topic: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class CausalStep(Base):
    __tablename__ = "causal_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theses.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CausalEdge(Base):
    __tablename__ = "causal_edges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("causal_steps.id"), nullable=False
    )
    target_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("causal_steps.id"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    creator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SourceStatement(Base):
    __tablename__ = "source_statements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_span_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_spans.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    observed_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class EvidenceLink(Base):
    __tablename__ = "evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theses.id"), nullable=False
    )
    source_statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    creator_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ai")
    review_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="machine_generated"
    )
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theses.id"), nullable=False
    )
    cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_link_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AIAssessment(Base):
    __tablename__ = "ai_assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_snapshots.id"), nullable=False
    )
    conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    gaps: Mapped[list] = mapped_column(JSON, nullable=False)
    displayed_as_provisional: Mapped[bool] = mapped_column(
        nullable=False, default=True
    )
    creator_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ai")
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    ai_assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_assessments.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FundCompany(Base):
    __tablename__ = "fund_companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Fund(Base):
    __tablename__ = "funds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    fund_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scale: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    establish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    management_company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("fund_companies.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ValuationSnapshot(Base):
    __tablename__ = "valuation_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    stock_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stocks.id"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HoldingDisclosure(Base):
    __tablename__ = "holding_disclosures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    fund_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funds.id"), nullable=False
    )
    stock_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stocks.id"), nullable=False
    )
    weight: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    report_period: Mapped[date] = mapped_column(Date, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ThemeRole(Base):
    __tablename__ = "theme_roles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=False
    )
    research_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    applicable_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("source_statements.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AIRun(Base):
    """Append-only audit record for every AI research-engine invocation.

    Each extract/propose/assess call writes exactly one row, capturing the
    model and prompt versions used, a summary of inputs/outputs, and the
    final status (success or failed).  Failed runs carry the error message.
    """

    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
