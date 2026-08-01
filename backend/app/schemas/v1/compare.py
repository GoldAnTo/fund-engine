"""Snapshot-compare v1 wire DTOs (prototype 版本比较)."""
from __future__ import annotations

from app.schemas.v1.common import V1Model


class CompareLinkDTO(V1Model):
    link_id: str
    role: str
    reason: str
    statement_text: str | None
    review_state: str


class ThesisCompareDTO(V1Model):
    thesis_id: str
    statement: str
    snapshot_before_id: str | None
    snapshot_after_id: str | None
    conclusion_before: str | None
    conclusion_after: str | None
    conclusion_changed: bool
    added_links: list[CompareLinkDTO]
    removed_links: list[CompareLinkDTO]
    gaps_before: list[str]
    gaps_after: list[str]


class DocumentVersionAddedDTO(V1Model):
    document_version_id: str
    source_url: str
    published_at: str | None
    available_at: str


class CaseCompareResponse(V1Model):
    """冻结快照比较: what changed between two point-in-time views."""

    case_id: str
    base_cutoff: str
    compare_cutoff: str
    documents_added: list[DocumentVersionAddedDTO]
    theses: list[ThesisCompareDTO]
