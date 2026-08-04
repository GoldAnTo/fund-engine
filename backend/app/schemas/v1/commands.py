"""Command-side v1 wire DTOs (prototype 新建研究 / 审核工作区).

Request DTOs validate *shape* only (non-empty strings, literal enums);
business rules (window ordering, outcome/relation consistency) live in the
services so CLI and tests get the same guarantees.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

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
    up in the snapshot-compare view.  ``mode`` is ``mock`` without an LLM key
    (non-production only — production fails closed per provider discipline).
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
# 抽取 / 提案 (extract / propose — AI engine steps as commands)
# ---------------------------------------------------------------------------


class ExtractStatementDTO(V1Model):
    """One statement produced by the extraction step."""

    id: str
    kind: str
    normalized_text: str
    observed_period: str | None


class ExtractResponse(V1Model):
    """Result of running statement extraction over one document version.

    Append-only: re-running extraction on a version that already has
    statements will append duplicates; the engine script only feeds
    pending versions (spans present, no statements yet).  ``mode`` is
    ``mock`` without an LLM key (non-production only).  ``reason`` is the
    honest explanation when ``statement_count`` is 0 (无片段 / 表格无可提
    事实 / LLM 拒答).
    """

    document_version_id: str
    mode: str
    statement_count: int
    reason: str | None = None
    statements: list[ExtractStatementDTO]


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
    progress / cancellation.  ``mode`` is ``mock`` without an LLM key.
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

    All fields optional: omitted queries fall back to the AI-compute
    defaults.  ``case_id`` tags ingested span locators against a case;
    when omitted the first existing case is used (or none).
    """

    case_id: str | None = None
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
