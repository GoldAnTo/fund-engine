"""Case snapshot-list and knowledge-layer v1 wire DTOs."""
from __future__ import annotations

from typing import Any

from app.schemas.v1.common import V1Model


class CaseSnapshotEventSummary(V1Model):
    """Incremental summary of what changed at this snapshot vs. the
    case-wide previous distinct cutoff (prototype 回放模式).

    - link_delta: number of new evidence links the previous cutoff did not have
    - removed_link_delta: number of evidence links that disappeared
    - conclusion_flips: thesis_id -> {from, to, statement}; one entry per
      conclusion that changed value between cutoffs
    - gaps_delta: {thesis_id: int} where positive = gaps grew, negative =
      gaps shrank (signals the case is converging)
    - reviewed_delta: number of new AssessmentReview records at this cutoff
    """

    link_delta: int
    removed_link_delta: int
    conclusion_flips: list[dict[str, str]]
    gaps_delta: dict[str, int]
    reviewed_delta: int


class CaseSnapshotDTO(V1Model):
    """One frozen snapshot row (prototype 版本比较 · 快照列表)."""

    snapshot_id: str
    thesis_id: str
    thesis_statement: str
    cutoff: str
    created_at: str
    link_count: int
    event_summary: CaseSnapshotEventSummary | None = None


class CaseSnapshotsResponse(V1Model):
    case_id: str
    snapshots: list[CaseSnapshotDTO]


class KnowledgeLinkDTO(V1Model):
    link_id: str
    thesis_id: str
    role: str
    reason: str
    scope: dict[str, Any]
    creator_type: str
    review_state: str
    latest_review_outcome: str | None
    latest_reviewer: str | None
    latest_reviewed_at: str | None


class KnowledgeItemDTO(V1Model):
    """已复核知识层一行: 规范化陈述 + 它的证据链接与人工审核。"""

    statement_id: str
    statement_text: str
    statement_kind: str
    observed_period: str | None
    span_id: str
    verbatim_text: str
    links: list[KnowledgeLinkDTO]


class KnowledgeResponse(V1Model):
    case_id: str | None
    items: list[KnowledgeItemDTO]
