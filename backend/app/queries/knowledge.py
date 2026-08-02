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
    AIAssessment,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.queries.effective_state import effective_review_state
from app.repositories.research import ResearchRepository
from app.schemas.v1.knowledge import (
    CaseSnapshotDTO,
    CaseSnapshotEventSummary,
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
        ).all()
        snapshot_rows = [
            (snap, thesis, set(snap.evidence_link_ids or []))
            for snap, thesis in rows
        ]
        # Case-wide link-id set per cutoff.
        per_cutoff_links: dict[str, set[str]] = {}
        for snap, _thesis, link_ids in snapshot_rows:
            key = snap.cutoff.isoformat()
            per_cutoff_links.setdefault(key, set()).update(link_ids)
        chronological_cutoffs = sorted(per_cutoff_links.keys())
        # Per-cutoff snapshot_id -> thesis_id -> (conclusion, gaps) using
        # the assessment table joined to its snapshot.
        assessments = self._db.execute(
            select(AIAssessment, EvidenceSnapshot, Thesis)
            .join(EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id)
            .join(Thesis, EvidenceSnapshot.thesis_id == Thesis.id)
            .where(Thesis.research_case_id == case_id)
            .order_by(AIAssessment.created_at.asc())
        ).all()
        # Index assessments by (thesis_id, cutoff_iso) so we can replay the
        # most recent conclusion at-or-before a given cutoff.
        # We store (cutoff_iso, AIAssessment, statement) so the event_summary
        # for each cutoff can cheaply look up the thesis statement at flip time.
        assessments_by_thesis: dict[uuid.UUID, list[tuple[str, AIAssessment, str]]] = {}
        for a, snap, thesis in assessments:
            cutoff_iso = snap.cutoff.isoformat() if snap is not None else "1970-01-01T00:00:00"
            assessments_by_thesis.setdefault(snap.thesis_id, []).append(
                (cutoff_iso, a, thesis.statement)
            )
        for thesis_id in assessments_by_thesis:
            assessments_by_thesis[thesis_id].sort()
        # Reviews keyed by assessment id.
        reviews = self._db.execute(
            select(ReviewDecision).where(
                ReviewDecision.ai_assessment_id.in_(
                    select(AIAssessment.id).join(
                        EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id
                    ).join(Thesis, EvidenceSnapshot.thesis_id == Thesis.id).where(
                        Thesis.research_case_id == case_id
                    )
                )
            ).order_by(ReviewDecision.created_at.asc())
        ).scalars().all()
        # Map: assessment_id -> list of review rows (1 row per case since
        # reviews are 1:1 with assessment).
        reviews_by_assessment: dict[uuid.UUID, list[ReviewDecision]] = {}
        for r in reviews:
            reviews_by_assessment.setdefault(r.ai_assessment_id, []).append(r)
        # Build per-cutoff event summaries in chronological order.
        prev_links: set[str] | None = None
        prev_conclusions: dict[uuid.UUID, str] = {}
        prev_gaps: dict[uuid.UUID, int] = {}
        prev_assessment_id: dict[uuid.UUID, uuid.UUID] = {}
        prev_review_count = 0
        summary_by_cutoff: dict[str, CaseSnapshotEventSummary] = {}
        for cutoff_iso in chronological_cutoffs:
            cur_links = per_cutoff_links[cutoff_iso]
            link_delta = len(cur_links - prev_links) if prev_links is not None else len(cur_links)
            removed_link_delta = (
                len(prev_links - cur_links) if prev_links is not None else 0
            )
            cur_conclusions: dict[uuid.UUID, str] = {}
            cur_gaps: dict[uuid.UUID, int] = {}
            cur_assessment_id: dict[uuid.UUID, uuid.UUID] = {}
            for thesis_id, hist in assessments_by_thesis.items():
                latest_a: AIAssessment | None = None
                for c, a, _stmt in hist:
                    if c <= cutoff_iso:
                        latest_a = a
                    else:
                        break
                if latest_a is not None:
                    cur_conclusions[thesis_id] = latest_a.conclusion
                    gaps = latest_a.gaps if isinstance(latest_a.gaps, list) else []
                    cur_gaps[thesis_id] = len(gaps)
                    cur_assessment_id[thesis_id] = latest_a.id
            flips: list[dict[str, str]] = []
            for thesis_id, new_conc in cur_conclusions.items():
                old_conc = prev_conclusions.get(thesis_id)
                if old_conc is not None and old_conc != new_conc:
                    stmt = ""
                    for c, _a, s in assessments_by_thesis[thesis_id]:
                        if c == cutoff_iso:
                            stmt = s
                            break
                    flips.append(
                        {
                            "thesis_id": str(thesis_id),
                            "from": old_conc,
                            "to": new_conc,
                            "statement": stmt,
                        }
                    )
            gaps_delta: dict[str, int] = {}
            for thesis_id, cur_gap in cur_gaps.items():
                prev_gap = prev_gaps.get(thesis_id)
                gaps_delta[str(thesis_id)] = (
                    cur_gap - prev_gap if prev_gap is not None else cur_gap
                )
            cur_review_count = sum(
                len(reviews_by_assessment.get(a_id, []))
                for a_id in cur_assessment_id.values()
            )
            reviewed_delta = max(0, cur_review_count - prev_review_count)
            prev_review_count = cur_review_count
            summary_by_cutoff[cutoff_iso] = CaseSnapshotEventSummary(
                link_delta=link_delta,
                removed_link_delta=removed_link_delta,
                conclusion_flips=flips,
                gaps_delta={k: v for k, v in gaps_delta.items() if v != 0},
                reviewed_delta=reviewed_delta,
            )
            prev_links = cur_links
            prev_conclusions = cur_conclusions
            prev_gaps = cur_gaps
        snapshots = [
            CaseSnapshotDTO(
                snapshot_id=str(snap.id),
                thesis_id=str(thesis.id),
                thesis_statement=thesis.statement,
                cutoff=snap.cutoff.isoformat(),
                created_at=snap.created_at.isoformat(),
                link_count=len(snap.evidence_link_ids or []),
                event_summary=(
                    summary_by_cutoff.get(snap.cutoff.isoformat())
                    if snap.cutoff.isoformat() != chronological_cutoffs[0]
                    else None
                ),
            )
            for snap, thesis, _ in snapshot_rows
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
