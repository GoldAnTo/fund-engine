"""Command-side v1 wire DTOs (prototype 新建研究 / 审核工作区).

Request DTOs validate *shape* only (non-empty strings, literal enums);
business rules (window ordering, outcome/relation consistency) live in the
services so CLI and tests get the same guarantees.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.v1.common import V1Model

# ---------------------------------------------------------------------------
# 新建研究 (case + thesis commands)
# ---------------------------------------------------------------------------


class ThesisInput(V1Model):
    """One initial proposition in the 新建研究 flow (prototype step 2)."""

    statement: str = Field(min_length=1)
    title: str | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    support_condition: str | None = None
    falsification_condition: str | None = None
    next_verification_event: str | None = None
    creator_type: Literal["human", "ai"] = "human"


class CreateCaseRequest(V1Model):
    """Create a research case with its research question + initial theses."""

    title: str = Field(min_length=1)
    industry_topic: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    research_object: str | None = None
    phenomenon: str | None = None
    core_question: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    evidence_cutoff: date | None = None
    initial_theses: list[ThesisInput] = Field(default_factory=list)


class CreatedThesisDTO(V1Model):
    id: str
    statement: str
    title: str | None
    creator_type: str
    review_state: str


class CreateCaseResponse(V1Model):
    case_id: str
    theses: list[CreatedThesisDTO]


class CreateThesisRequest(ThesisInput):
    """Add one proposition to an existing case (AI 协助拆分 or human)."""

    created_by: str = Field(min_length=1)


class CreateThesisResponse(V1Model):
    thesis: CreatedThesisDTO


class CreateDocumentSupplementRequest(V1Model):
    case_id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    claimed_page_reference: str = Field(min_length=1, max_length=256)
    created_by: str = Field(min_length=1, max_length=128)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class CreateDocumentSupplementResponse(V1Model):
    document_version_id: str
    original_document_version_id: str
    claimed_page_reference: str
    extraction_allowed: bool


# ---------------------------------------------------------------------------
# 审核工作区 (review commands)
# ---------------------------------------------------------------------------

LinkReviewOutcome = Literal["confirmed", "rejected", "needs_more_evidence"]
LinkReviewRelation = Literal[
    "supports", "contradicts", "contextualizes", "evidence_gap"
]


class LinkReviewRequest(V1Model):
    """四要素关系级审核: 关系选择/因素角色/适用边界/审核理由 + 动作."""

    outcome: LinkReviewOutcome
    relation: LinkReviewRelation | None = None
    factor_role: str = Field(min_length=1)
    scope_boundary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)


class EvidenceReviewDTO(V1Model):
    id: str
    evidence_link_id: str
    outcome: str
    relation: str | None
    factor_role: str
    scope_boundary: str
    reason: str
    reviewer: str
    created_at: str


class LinkReviewResponse(V1Model):
    review: EvidenceReviewDTO


class AssessmentReviewRequest(V1Model):
    outcome: Literal["confirmed", "modified", "rejected"]
    conclusion: Literal["supported", "contradicted", "insufficient_evidence"] | None = None
    reason: str = Field(min_length=1)
    reviewer: str = Field(default="reviewer", min_length=1)


class AssessmentReviewResponse(V1Model):
    id: str
    ai_assessment_id: str
    outcome: str
    conclusion: str | None
    reason: str
    reviewer: str
    created_at: str


# ---------------------------------------------------------------------------
# 监测与更新 (AI rerun command, prototype 版本比较 · AI RERUN)
# ---------------------------------------------------------------------------


class RerunAssessmentDTO(V1Model):
    id: str
    snapshot_id: str
    conclusion: str
    rationale: str
    gaps: list[str]
    displayed_as_provisional: bool
    created_at: str


class RerunResponse(V1Model):
    """Result of re-running the assess step for one thesis.

    A rerun freezes a NEW snapshot and appends a NEW provisional assessment;
    prior snapshots/assessments are never touched, and the difference shows
    up in the snapshot-compare view.  ``mode`` is ``mock`` only when
    ``APP_ENV=test`` and no LLM key is configured; every non-test runtime
    requires a live provider.
    """

    thesis_id: str
    mode: str
    assessment: RerunAssessmentDTO


# ---------------------------------------------------------------------------
# 审核队列 (review queue read model, consumed by the commands router)
# ---------------------------------------------------------------------------


class ReviewQueueItemDTO(V1Model):
    """One pending link-level review: frozen source vs AI proposal."""

    link_id: str
    thesis_id: str
    case_id: str
    thesis_statement: str
    ai_role: str
    ai_reason: str
    ai_scope: dict[str, Any]
    statement_id: str
    statement_text: str
    statement_kind: str
    span_id: str
    verbatim_text: str
    locator: dict[str, Any]
    document_version_id: str
    document_source_url: str
    document_published_at: str | None
    available_at: str


class ReviewQueueResponse(V1Model):
    items: list[ReviewQueueItemDTO]


# ---------------------------------------------------------------------------
# 原子陈述审核（抽取候选只能经人工审核后发布为正式 SourceStatement）
# ---------------------------------------------------------------------------


class PublishedSourceStatementDTO(V1Model):
    id: str
    normalized_text: str
    kind: str
    observed_period: date | None
    created_at: datetime


class AtomicClaimReviewDTO(V1Model):
    id: str
    outcome: Literal["confirmed", "modified", "rejected"]
    reviewer: str
    reason: str
    published_source_statement: PublishedSourceStatementDTO | None
    created_at: datetime


class AtomicClaimCandidateDTO(V1Model):
    id: str
    source_span_id: str
    document_version_id: str
    document_source_url: str
    locator: dict[str, Any]
    quote: str
    quote_start: int
    quote_end: int
    quote_sha256: str
    normalized_text: str
    claim_type: str
    assertion_actor: str | None
    authority_level: str
    structured_fields: dict[str, Any]
    validation_result: dict[str, Any]
    created_at: datetime
    review_state: Literal["awaiting_review", "confirmed", "modified", "rejected"]
    review_history: list[AtomicClaimReviewDTO]
    published_source_statement: PublishedSourceStatementDTO | None


class AtomicClaimQueueResponse(V1Model):
    items: list[AtomicClaimCandidateDTO]
    has_more: bool = False
    next_cursor: str | None = None


class AtomicClaimReviewRequest(V1Model):
    outcome: Literal["confirmed", "modified", "rejected"]
    normalized_text: str | None = Field(default=None, min_length=1)
    observed_period: date | None = None
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class CreateAtomicClaimCandidateRequest(V1Model):
    """A researcher-proposed, review-gated claim from one frozen span.

    The server derives the quote and offsets from ``source_span_id`` so the
    browser cannot silently alter the cited wording or location.
    """

    source_span_id: UUID
    normalized_text: str = Field(min_length=1)
    claim_type: Literal[
        "disclosed_fact",
        "reported_claim",
        "management_attribution",
        "forecast",
        "research_opinion",
    ] = "reported_claim"
    assertion_actor: str | None = Field(default=None, max_length=512)
    subject: str | None = Field(default=None, max_length=512)
    predicate: str | None = Field(default=None, max_length=512)
    object_text: str | None = Field(default=None, max_length=2_000)
    numeric_value: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=128)
    observed_period: date | None = None
    scope: dict[str, str] = Field(default_factory=dict)
    actor: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# 抽取 / 提案 (extract / propose — AI engine steps as commands)
# ---------------------------------------------------------------------------


class ExtractCandidateDTO(V1Model):
    """One source-grounded candidate awaiting human review."""

    id: str
    claim_type: str
    normalized_text: str
    quote: str
    quote_start: int
    quote_end: int
    review_state: Literal["awaiting_review"] = "awaiting_review"


class ExtractResponse(V1Model):
    """Result of running review-gated extraction over one document version.

    The extractor never writes formal SourceStatements. Every returned item
    has a continuous source quote and stays in ``awaiting_review`` until a
    human confirms, modifies, or rejects it. ``reason`` explains a zero
    candidate result without pretending that extraction succeeded silently.
    """

    document_version_id: str
    mode: str
    candidate_count: int
    reason: str | None = None
    candidates: list[ExtractCandidateDTO]


class ProposedLinkDTO(V1Model):
    """One evidence_link Proposal created by the proposer (pending review)."""

    proposal_id: str
    source_statement_id: str
    role: str
    reason: str
    scope: dict[str, Any]


class ProposeResponse(V1Model):
    """Result of running evidence proposal for one thesis.

    Every proposed link lands as a ``Proposal(kind=evidence_link)`` in the
    review queue; nothing is auto-confirmed.  ``job_id`` lets the client track
    progress / cancellation.  ``mode`` is ``mock`` only when
    ``APP_ENV=test`` and no LLM key is configured; every non-test runtime
    requires a live provider.
    """

    thesis_id: str
    mode: str
    job_id: str
    link_count: int
    links: list[ProposedLinkDTO]


# ---------------------------------------------------------------------------
# 数据接入 (gildata ingest — first step of the engine loop)
# ---------------------------------------------------------------------------


class IngestRequest(V1Model):
    """Trigger a Gildata ingest run.

    Query fields are optional and fall back to the AI-compute defaults.
    ``case_id`` is required: an ingest may attach frozen provider material only
    to the current tenant's explicitly selected Case.
    """

    case_id: str
    research_queries: list[str] | None = None
    announcement_query: str | None = None
    news_query: str | None = None
    quote_query: str | None = None
    quote_stock_code: str | None = None
    # 宏观/行业时序查询（价格水平、环比增长率等）。每个查询会把返回的整段
    # 时序冷冻为一份 DocumentVersion + SourceSpan，自然键 (query+metric)
    # 去重，append-only 不重复入库。
    macro_queries: list[str] | None = None


class IngestResponse(V1Model):
    """Summary of one ingest run.

    Idempotent: documents dedupe by content hash and valuation snapshots
    by stock + date + metric + source, so re-runs report skips instead of
    duplicating rows.
    """

    research_reports: int
    announcements: int
    news: int
    macro_series: int = 0
    spans: int
    valuations_written: int
    valuations_skipped: int
    stock_id: str | None
    case_id: str | None
