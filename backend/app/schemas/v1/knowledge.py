"""Case snapshot-list and knowledge-layer v1 wire DTOs."""
from __future__ import annotations

from typing import Any

from app.schemas.v1.common import V1Model


class CaseSnapshotDTO(V1Model):
    """One frozen snapshot row (prototype 版本比较 · 快照列表)."""

    snapshot_id: str
    thesis_id: str
    thesis_statement: str
    cutoff: str
    created_at: str
    link_count: int


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
