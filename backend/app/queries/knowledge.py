"""Snapshot-list and knowledge-layer read models.

- ``snapshots``: every frozen EvidenceSnapshot of the case, newest first
  (prototype 版本比较 left rail).
- ``knowledge``: the human-reviewed knowledge layer (prototype 资料与知识):
  source statements with their evidence links, link review state, and the
  latest link-level EvidenceReview when present.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.queries.effective_state import effective_review_state
from app.repositories.research import ResearchRepository
from app.schemas.v1.knowledge import (
    CaseSnapshotDTO,
    CaseSnapshotsResponse,
    KnowledgeItemDTO,
    KnowledgeLinkDTO,
    KnowledgeResponse,
)


class SnapshotQueries:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ResearchRepository(db)

    def snapshots_for_case(self, *, case_id: uuid.UUID) -> CaseSnapshotsResponse:
        if self._repo.get_case(case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")
        rows = self._db.execute(
            select(EvidenceSnapshot, Thesis)
            .join(Thesis, EvidenceSnapshot.thesis_id == Thesis.id)
            .where(Thesis.research_case_id == case_id)
            .order_by(EvidenceSnapshot.created_at.desc())
        )
        snapshots = [
            CaseSnapshotDTO(
                snapshot_id=str(snap.id),
                thesis_id=str(thesis.id),
                thesis_statement=thesis.statement,
                cutoff=snap.cutoff.isoformat(),
                created_at=snap.created_at.isoformat(),
                link_count=len(snap.evidence_link_ids),
            )
            for snap, thesis in rows
        ]
        return CaseSnapshotsResponse(case_id=str(case_id), snapshots=snapshots)


class KnowledgeQueries:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = ResearchRepository(db)

    def knowledge_layer(
        self,
        *,
        case_id: uuid.UUID | None = None,
        review_state: str | None = None,
        limit: int = 100,
    ) -> KnowledgeResponse:
        if case_id is not None and self._repo.get_case(case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")

        query = (
            select(EvidenceLink, SourceStatement, SourceSpan, Thesis)
            .join(
                SourceStatement,
                EvidenceLink.source_statement_id == SourceStatement.id,
            )
            .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
            .join(Thesis, EvidenceLink.thesis_id == Thesis.id)
            .order_by(EvidenceLink.created_at.desc())
        )
        if case_id is not None:
            query = query.where(Thesis.research_case_id == case_id)
        # NOTE: no SQL filter on review_state — ledger rows are append-only,
        # so the effective state is derived from the latest EvidenceReview
        # below (a confirmed review makes a machine_generated link reviewed).
        # When no state filter is given we can safely cap the scan; with a
        # filter the cap must apply *after* derivation, so scan everything.
        if review_state is None:
            query = query.limit(limit * 4)  # statements collapse links per row

        items: dict[uuid.UUID, KnowledgeItemDTO] = {}
        for link, statement, span, thesis in self._db.execute(query):
            latest = self._latest_link_review(link.id)
            state = effective_review_state(
                link.review_state, latest.outcome if latest else None
            )
            if review_state is not None and state != review_state:
                continue
            item = items.get(statement.id)
            if item is None:
                if len(items) >= limit:
                    continue
                item = KnowledgeItemDTO(
                    statement_id=str(statement.id),
                    statement_text=statement.normalized_text,
                    statement_kind=statement.kind,
                    observed_period=(
                        statement.observed_period.isoformat()
                        if statement.observed_period
                        else None
                    ),
                    span_id=str(span.id),
                    verbatim_text=span.verbatim_text,
                    links=[],
                )
                items[statement.id] = item
            item.links.append(
                KnowledgeLinkDTO(
                    link_id=str(link.id),
                    thesis_id=str(thesis.id),
                    role=link.role,
                    reason=link.reason,
                    scope=link.scope,
                    creator_type=link.creator_type,
                    review_state=state,
                    latest_review_outcome=latest.outcome if latest else None,
                    latest_reviewer=latest.reviewer if latest else None,
                    latest_reviewed_at=(
                        latest.created_at.isoformat() if latest else None
                    ),
                )
            )
        return KnowledgeResponse(
            case_id=str(case_id) if case_id else None,
            items=list(items.values()),
        )

    def _latest_link_review(self, link_id: uuid.UUID) -> EvidenceReview | None:
        return self._db.scalar(
            select(EvidenceReview)
            .where(EvidenceReview.evidence_link_id == link_id)
            .order_by(EvidenceReview.created_at.desc())
            .limit(1)
        )
