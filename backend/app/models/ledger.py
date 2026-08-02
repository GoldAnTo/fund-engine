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

from sqlalchemy import DateTime, Date, ForeignKey, Integer, JSON, Numeric, String, Text, Uuid, event
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
# Link-level review (prototype 审核工作区): the human decision on one
# AI-proposed EvidenceLink.  ``relation`` is the 关系选择 dimension; the
# action itself is ``outcome``.
LinkReviewOutcome = Literal["confirmed", "rejected", "needs_more_evidence"]
LinkReviewRelation = Literal[
    "supports", "contradicts", "contextualizes", "evidence_gap"
]
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
        "evidence_reviews",
        "companies",
        "stocks",
        "fund_companies",
        "funds",
        "valuation_snapshots",
        "holding_disclosures",
        "theme_roles",
        "case_theme_tag_events",
        "ai_runs",
        "audit_logs",
    }
)


class ImmutableLedgerError(Exception):
    """Raised on any attempt to UPDATE or DELETE an append-only ledger table."""


class ValidationError(Exception):
    """Raised when a service-layer validation fails."""


class ConflictError(Exception):
    """Raised when a service-layer write collides with existing ledger state.

    Service layer raises this for uniqueness-style conflicts (duplicate
    company/stock/fund code, etc.). The route layer translates it into the
    HTTP-layer :class:`app.errors.ConflictError`, which is mapped to a 409
    v1 error envelope. Distinguishing 409 from 422 keeps the client contract
    clean: a 422 means the request body itself is malformed, a 409 means the
    request was well-formed but already exists.
    """


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
    # (source_prefix, normalized_title, published_at) 的 SHA256 前 32 字符，
    # 用于跨入口合并"同来源 + 同标题 + 同发布日期"的不同抓取版本，避免
    # 年报正文/摘要/港股版各自入库造成重复入队。
    natural_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=True
    )
    # Display + provenance fields (S4 of the Docling + locator-v1 spec,
    # docs/research/2026-08-02-docling-and-source-locator-v1-spec.md §3.5):
    # - title: source-side document title (separate from source_url; can be
    #   the broker's "报告标题" or a PDF's own metadata).
    # - byte_size: size of the original payload, used by the workbench to
    #   decide whether to fetch the whole blob or render a preview.
    # - language: ISO-639-1 hint (zh / en / mixed) used by the parser
    #   router in S5 to pick an OCR model when text layer is missing.
    # - parse_state: "success" / "partial" / "failed" / "pending" — written
    #   by the parser adapter so the workbench can warn on degraded reads.
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    parse_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success"
    )


class SourceSpan(Base):
    __tablename__ = "source_spans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id"), nullable=False
    )
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Round-trip + context hashes (S4):
    # - text_sha256: hash of normalised verbatim_text, used by the
    #   round-trip validator and by callers that need to know whether
    #   a re-extracted span is byte-identical to the stored one.
    # - context_hash: short hash of (page, prev_text, next_text) so
    #   neighbouring stability survives a non-breaking parser upgrade.
    # - locator_v1: the new v1 form of the locator (spec §3.1 / §3.5);
    #   coexists with the legacy ``locator`` JSON because the ledger is
    #   append-only.  S5 will switch read paths to prefer this column.
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locator_v1: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ResearchCase(Base):
    __tablename__ = "research_cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    industry_topic: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    # Research-question framing (prototype 新建研究 step 1).  All optional so
    # legacy/seeded cases stay valid; a fully framed case carries them.
    research_object: Mapped[str | None] = mapped_column(Text, nullable=True)
    phenomenon: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_cutoff: Mapped[date | None] = mapped_column(Date, nullable=True)


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
    # Falsifiable-proposition framing (prototype 新建研究 step 2): a thesis is
    # verifiable only when its observation window, support/falsification
    # conditions, and next verification event are written down.  Optional so
    # legacy/seeded theses stay valid.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    observation_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    support_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    falsification_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_verification_event: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-drafted theses start as ``draft`` until a human confirms them;
    # human-authored theses (including the gold seed) are ``confirmed``.
    creator_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="human"
    )
    review_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="confirmed"
    )


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


class EvidenceReview(Base):
    """Link-level human review of one AI-proposed EvidenceLink.

    Prototype 审核工作区: the reviewer must fill four fields — 关系选择
    (``relation``), 因素角色 (``factor_role``), 适用边界 (``scope_boundary``),
    审核理由 (``reason``) — and the action (``outcome``) is one of 确认写入 /
    驳回 / 要求补充证据.  Append-only: the reviewed link is never mutated;
    readers resolve the latest review per link.
    """

    __tablename__ = "evidence_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_links.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    relation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    factor_role: Mapped[str] = mapped_column(Text, nullable=False)
    scope_boundary: Mapped[str] = mapped_column(Text, nullable=False)
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


class CaseThemeTagEvent(Base):
    """Append-only theme-tag assignment event for a ResearchCase.

    A case's effective theme tags are derived by folding these events in
    ``created_at`` order: ``add`` inserts the tag, ``remove`` deletes it.
    Tags are classification metadata (横切主题), not research judgments, and
    never participate in effective-state derivation.

    Two-stage review (see SPEC §AI/人工边界): AI-initiated tag changes land
    as ``status='pending'`` events that do not change the effective tag set
    until a human PATCH with the same desired set promotes them to
    ``status='confirmed'``. All events from a single AI proposal share a
    ``proposal_id`` so a confirmation or rejection can target the whole
    batch atomically.
    """

    __tablename__ = "case_theme_tag_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    op: Mapped[str] = mapped_column(String(8), nullable=False)  # add | remove
    # 'human' | 'ai' — who initiated the change. Defaults to 'human' so
    # legacy rows (pre-migration 0008) read as confirmed human edits.
    proposed_by: Mapped[str] = mapped_column(String(16), nullable=False, default="human")
    # 'pending' | 'confirmed' | 'rejected' — only 'confirmed' events count
    # toward the effective tag set.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed")
    # UUID shared by all events from one AI proposal (NULL for human-
    # initiated events that didn't go through a proposal batch).
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
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


class AuditLog(Base):
    """Append-only who/when/what audit log for every write-path command.

    One row is written per command endpoint invocation, capturing the
    ``actor`` (e.g. ``"human:alice"`` or ``"ai:openai/gpt-4"``), the
    ``action`` (e.g. ``create_company``), the ``entity_type`` /
    ``entity_id`` targeted, the request ``payload`` (sanitized — secrets
    stripped), the ``result`` (``success`` / ``failed`` / ``conflict``),
    and any error message.

    Distinct from :class:`AIRun` (which audits AI model invocations) and
    from the per-entity ledger tables (which carry the data, not the
    write history). AuditLog exists so the *whole* write surface is
    reviewable from a single timeline.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
