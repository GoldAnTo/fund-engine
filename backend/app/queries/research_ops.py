"""Research-operations KPI queries (研究效能度量).

Three management KPIs over the append-only ledger, all derived from ledger
records (no persisted scores, no self-reported numbers):

1. **审核吞吐 (review throughput)** — human review output (link-level
   EvidenceReviews + assessment-level ReviewDecisions) against the pending
   machine-generated queue.  The pending queue uses *effective* review state
   (latest append-only review folded over the frozen column), never the
   frozen ``review_state`` alone.

2. **人机一致率 (human-AI agreement)** — how often human decisions confirm
   the machine draft, at assessment level (ReviewDecision outcome) and at
   link level (EvidenceReview outcome + relation match).

3. **判断时滞 (judgment latency, days)** — evidence → AI judgment
   (latest evidence ``available_at`` in the frozen snapshot to assessment
   creation) and AI judgment → human review (assessment creation to first
   ReviewDecision).

``as_of`` gives point-in-time replay: records created after it do not exist
for the computation, consistent with the ledger's HistoricalBasis semantics.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    AIAssessment,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    Thesis,
)
from app.queries.effective_state import latest_review_outcomes
from app.schemas.v1.research_ops import (
    HumanAiAgreementDTO,
    JudgmentLatencyDTO,
    ResearchOpsResponse,
    ReviewThroughputDTO,
)


def _naive(dt: datetime) -> datetime:
    # Ledger datetimes may mix naive/aware; compare as naive UTC.
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _days(later: datetime, earlier: datetime) -> float:
    return (_naive(later) - _naive(earlier)).total_seconds() / 86400.0


def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(sum(values) / len(values), 2), round(max(values), 2)


class ResearchOpsQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def kpis(
        self,
        *,
        case_id: uuid.UUID | None = None,
        as_of: datetime | None = None,
    ) -> ResearchOpsResponse:
        as_of = as_of or datetime.now(UTC)
        if case_id is not None and self._db.get(ResearchCase, case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")

        thesis_query = select(Thesis.id)
        if case_id is not None:
            thesis_query = thesis_query.where(Thesis.research_case_id == case_id)
        thesis_ids = set(self._db.scalars(thesis_query).all())

        links = [
            row
            for row in self._db.scalars(
                select(EvidenceLink).where(EvidenceLink.thesis_id.in_(thesis_ids))
            ).all()
            if _naive(row.created_at) <= _naive(as_of)
        ] if thesis_ids else []
        link_by_id = {link.id: link for link in links}

        assessments = [
            row
            for row in self._db.execute(
                select(AIAssessment, EvidenceSnapshot)
                .join(
                    EvidenceSnapshot,
                    AIAssessment.snapshot_id == EvidenceSnapshot.id,
                )
                .where(EvidenceSnapshot.thesis_id.in_(thesis_ids))
            ).all()
            if _naive(row[0].created_at) <= _naive(as_of)
        ] if thesis_ids else []

        link_reviews = [
            row
            for row in self._db.scalars(
                select(EvidenceReview).where(
                    EvidenceReview.evidence_link_id.in_(link_by_id.keys())
                )
            ).all()
            if _naive(row.created_at) <= _naive(as_of)
        ] if link_by_id else []

        assessment_ids = {a.id for a, _s in assessments}
        decisions = [
            row
            for row in self._db.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.ai_assessment_id.in_(assessment_ids)
                )
            ).all()
            if _naive(row.created_at) <= _naive(as_of)
        ] if assessment_ids else []

        return ResearchOpsResponse(
            as_of=as_of.isoformat(),
            case_id=str(case_id) if case_id is not None else None,
            throughput=self._throughput(
                links, link_reviews, assessments, decisions, as_of
            ),
            agreement=self._agreement(assessments, decisions, link_reviews, link_by_id),
            latency=self._latency(assessments, decisions, link_by_id),
        )

    # ------------------------------------------------------------------ KPIs

    def _throughput(
        self,
        links: list[EvidenceLink],
        link_reviews: list[EvidenceReview],
        assessments: list[tuple[AIAssessment, EvidenceSnapshot]],
        decisions: list[ReviewDecision],
        as_of: datetime,
    ) -> ReviewThroughputDTO:
        window_start = _naive(as_of) - timedelta(days=7)

        by_reviewer: dict[str, int] = {}
        for review in [*link_reviews, *decisions]:
            by_reviewer[review.reviewer] = by_reviewer.get(review.reviewer, 0) + 1

        latest = latest_review_outcomes(
            self._db, [link.id for link in links], cutoff=as_of
        )
        pending_links = sum(
            1
            for link in links
            if link.review_state == "machine_generated"
            and latest.get(link.id) in (None, "needs_more_evidence")
        )
        reviewed_assessment_ids = {d.ai_assessment_id for d in decisions}
        pending_assessments = sum(
            1 for a, _s in assessments if a.id not in reviewed_assessment_ids
        )

        return ReviewThroughputDTO(
            link_reviews_total=len(link_reviews),
            link_reviews_last_7d=sum(
                1 for r in link_reviews if _naive(r.created_at) >= window_start
            ),
            assessment_reviews_total=len(decisions),
            assessment_reviews_last_7d=sum(
                1 for d in decisions if _naive(d.created_at) >= window_start
            ),
            reviews_by_reviewer=by_reviewer,
            pending_link_reviews=pending_links,
            pending_assessment_reviews=pending_assessments,
        )

    def _agreement(
        self,
        assessments: list[tuple[AIAssessment, EvidenceSnapshot]],
        decisions: list[ReviewDecision],
        link_reviews: list[EvidenceReview],
        link_by_id: dict[uuid.UUID, EvidenceLink],
    ) -> HumanAiAgreementDTO:
        assessment_by_id = {a.id: a for a, _s in assessments}

        outcomes: dict[str, int] = {}
        conclusion_changed = 0
        for d in decisions:
            outcomes[d.outcome] = outcomes.get(d.outcome, 0) + 1
            original = assessment_by_id.get(d.ai_assessment_id)
            if (
                original is not None
                and d.conclusion is not None
                and d.conclusion != original.conclusion
            ):
                conclusion_changed += 1
        assessment_rate = (
            round(outcomes.get("confirmed", 0) / len(decisions), 4)
            if decisions
            else None
        )

        link_outcomes: dict[str, int] = {}
        agree = 0
        modified = 0
        for r in link_reviews:
            link_outcomes[r.outcome] = link_outcomes.get(r.outcome, 0) + 1
            if r.outcome != "confirmed":
                continue
            link = link_by_id.get(r.evidence_link_id)
            if link is not None and r.relation is not None and r.relation != link.role:
                modified += 1
            else:
                agree += 1
        link_rate = (
            round(agree / len(link_reviews), 4) if link_reviews else None
        )

        return HumanAiAgreementDTO(
            assessment_outcomes=outcomes,
            assessment_agreement_rate=assessment_rate,
            conclusion_changed=conclusion_changed,
            link_outcomes=link_outcomes,
            link_agreement_rate=link_rate,
            link_modified=modified,
        )

    def _latency(
        self,
        assessments: list[tuple[AIAssessment, EvidenceSnapshot]],
        decisions: list[ReviewDecision],
        link_by_id: dict[uuid.UUID, EvidenceLink],
    ) -> JudgmentLatencyDTO:
        evidence_to_assessment: list[float] = []
        for assessment, snapshot in assessments:
            available = [
                link_by_id[link_id].available_at
                for link_id in (
                    uuid.UUID(raw) if isinstance(raw, str) else raw
                    for raw in snapshot.evidence_link_ids
                )
                if link_id in link_by_id
            ]
            if available:
                evidence_to_assessment.append(
                    _days(assessment.created_at, max(available, key=_naive))
                )

        first_review: dict[uuid.UUID, datetime] = {}
        for d in decisions:
            prev = first_review.get(d.ai_assessment_id)
            if prev is None or _naive(d.created_at) < _naive(prev):
                first_review[d.ai_assessment_id] = d.created_at
        assessment_to_review = [
            _days(first_review[a.id], a.created_at)
            for a, _s in assessments
            if a.id in first_review
        ]

        ev_avg, ev_max = _stats(evidence_to_assessment)
        rv_avg, rv_max = _stats(assessment_to_review)
        return JudgmentLatencyDTO(
            evidence_to_assessment_avg_days=ev_avg,
            evidence_to_assessment_max_days=ev_max,
            assessment_to_review_avg_days=rv_avg,
            assessment_to_review_max_days=rv_max,
        )
