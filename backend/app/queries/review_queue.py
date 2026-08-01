"""Review-queue read model (prototype 审核工作区 01·待办).

A link is pending review when it is machine-generated and carries no human
EvidenceReview yet.  Append-only semantics: a reviewed link never re-enters
the queue; a corrected judgment is a new review on a new link.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.schemas.v1.commands import ReviewQueueItemDTO, ReviewQueueResponse


class ReviewQueueQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_items(
        self,
        *,
        case_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> ReviewQueueResponse:
        reviewed = select(EvidenceReview.evidence_link_id)
        query = (
            select(EvidenceLink, Thesis, SourceStatement, SourceSpan, DocumentVersion)
            .join(Thesis, EvidenceLink.thesis_id == Thesis.id)
            .join(
                SourceStatement,
                EvidenceLink.source_statement_id == SourceStatement.id,
            )
            .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
            .join(
                DocumentVersion,
                SourceSpan.document_version_id == DocumentVersion.id,
            )
            .where(EvidenceLink.creator_type == "ai")
            .where(EvidenceLink.review_state == "machine_generated")
            .where(EvidenceLink.id.not_in(reviewed))
            .order_by(EvidenceLink.created_at)
            .limit(limit)
        )
        if case_id is not None:
            query = query.where(Thesis.research_case_id == case_id)

        items: list[ReviewQueueItemDTO] = []
        for link, thesis, statement, span, version in self._db.execute(query):
            items.append(
                ReviewQueueItemDTO(
                    link_id=str(link.id),
                    thesis_id=str(thesis.id),
                    case_id=str(thesis.research_case_id),
                    thesis_statement=thesis.statement,
                    ai_role=link.role,
                    ai_reason=link.reason,
                    ai_scope=link.scope,
                    statement_id=str(statement.id),
                    statement_text=statement.normalized_text,
                    statement_kind=statement.kind,
                    span_id=str(span.id),
                    verbatim_text=span.verbatim_text,
                    locator=span.locator,
                    document_version_id=str(version.id),
                    document_source_url=version.source_url,
                    document_published_at=(
                        version.published_at.isoformat()
                        if version.published_at
                        else None
                    ),
                    available_at=link.available_at.isoformat(),
                )
            )
        return ReviewQueueResponse(items=items)
