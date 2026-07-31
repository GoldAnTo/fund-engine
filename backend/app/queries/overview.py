"""Honest research overview assembly for the v1 API.

Reuses CaseReadQueries.dossier for case/thesis/assessment/causal/evidence
assembly so the AI/human boundary and historical replay rules cannot drift
between the dossier and the overview. task_queue/evidence_changes/activity
stay empty until delivery 3 projections exist.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.repositories.research import ResearchRepository
from app.schemas.v1.overview import (
    KeyChangeDTO,
    OverviewResponse,
    OverviewTotalsDTO,
)


class OverviewQueries:
    """Read-only honest overview assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)

    def load(
        self, *, case_id: uuid.UUID, basis: HistoricalBasis
    ) -> OverviewResponse:
        # Reuse the dossier assembly: it already enforces the case cutoff
        # filter (NotFoundError), the review-state gate (rejected never,
        # machine only under research_mode) and the statement cutoff filter.
        # research_mode=True so the dashboard can count and label pending
        # machine proposals (review_state is carried on every key change).
        dossier = CaseReadQueries(self._session).dossier(
            case_id=case_id,
            thesis_id=None,
            basis=basis,
            research_mode=True,
        )

        all_evidence = [e for hits in dossier.evidence.values() for e in hits]
        machine_count = sum(
            1 for e in all_evidence if e.review_state == "machine_generated"
        )

        # pending_review: visible machine-generated evidence plus assessments
        # without a cutoff-visible review. Count ALL visible assessments, not
        # just the latest, so older unreviewed assessments are not missed.
        pending_review = machine_count
        if dossier.focus_thesis_id:
            thesis_id = uuid.UUID(dossier.focus_thesis_id)
            for assessment in self._research.assessments_for_thesis(
                thesis_id, cutoff=basis.cutoff
            ):
                review = self._research.latest_review_for_assessment(
                    assessment.id, cutoff=basis.cutoff
                )
                if review is None:
                    pending_review += 1

        key_changes: list[KeyChangeDTO] = []
        for e in sorted(all_evidence, key=lambda x: x.available_at, reverse=True)[:5]:
            key_changes.append(
                KeyChangeDTO(
                    id=e.link_id,
                    tag="新增",
                    text=e.statement_text or "",
                    occurred_at=e.available_at or "",
                    source_label=e.statement_kind or "",
                    review_state=e.review_state,
                )
            )

        framework = [
            {"id": s.id, "sequence": s.sequence, "description": s.description}
            for s in dossier.causal_chain
        ]
        major_gaps = len(dossier.assessment.gaps) if dossier.assessment else 0

        thesis_dto: dict | None = None
        if dossier.focus_thesis_id:
            for t in dossier.theses:
                if t.id == dossier.focus_thesis_id:
                    thesis_dto = {
                        "id": t.id,
                        "statement": t.statement,
                        "created_by": t.created_by,
                        "created_at": t.created_at,
                    }
                    break

        return OverviewResponse(
            basis=basis.to_dto(),
            case=dossier.case,
            thesis=thesis_dto,
            assessment=dossier.assessment,
            key_changes=key_changes,
            framework=framework,
            totals=OverviewTotalsDTO(
                evidence_total=len(all_evidence),
                pending_review=pending_review,
                major_gaps=major_gaps,
            ),
            task_queue=[],
            evidence_changes=[],
            activity=[],
        )
