"""Authorization-aware source backlink projection helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import CaseDocumentVersion, SourceSpan, SourceStatement


def statement_is_attached_to_case(
    session: Session,
    *,
    statement_id: uuid.UUID | None,
    research_case_id: uuid.UUID | None,
) -> bool:
    """Return whether a statement's document is attached to the exact Case."""
    if statement_id is None or research_case_id is None:
        return False
    return (
        session.scalar(
            select(SourceStatement.id)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id
                == SourceSpan.document_version_id,
            )
            .where(
                SourceStatement.id == statement_id,
                CaseDocumentVersion.research_case_id == research_case_id,
            )
            .limit(1)
        )
        is not None
    )
