"""Honest research overview assembly for the v1 API.

Assembles a case dashboard from the append-only ledger: ledger counts, the
visible provisional assessment, recent evidence key changes and the causal
framework. task_queue/evidence_changes/activity are explicitly empty until
delivery 3 projections exist. Historical replay (design 10) filters every
entity by the cutoff basis.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.queries.basis import HistoricalBasis
from app.repositories.research import ResearchRepository
from app.schemas.v1.cases import AssessmentDTO, CaseSummaryDTO
from app.schemas.v1.overview import (
    KeyChangeDTO,
    OverviewResponse,
    OverviewTotalsDTO,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class OverviewQueries:
    """Read-only honest overview assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)

    def load(
        self, *, case_id: uuid.UUID, basis: HistoricalBasis
    ) -> OverviewResponse:
        case = self._research.get_case(case_id, cutoff=basis.cutoff)
        if case is None:
            raise NotFoundError("research case not found")

        thesis = self._research.latest_thesis_for_case(
            case_id, cutoff=basis.cutoff
        )
        thesis_dto: dict | None = None
        assessment_dto: AssessmentDTO | None = None
        evidence_total = 0
        pending_review = 0
        key_changes: list[KeyChangeDTO] = []
        framework: list[dict] = []

        if thesis is not None:
            thesis_dto = {
                "id": str(thesis.id),
                "statement": thesis.statement,
                "created_at": _iso(thesis.created_at),
            }
            assessment = self._research.latest_assessment_for_thesis(
                thesis.id, cutoff=basis.cutoff
            )
            if assessment is not None:
                review = self._research.latest_review_for_assessment(
                    assessment.id, cutoff=basis.cutoff
                )
                review_dto: dict | None = None
                if review is not None:
                    review_dto = {
                        "outcome": review.outcome,
                        "conclusion": review.conclusion,
                        "reason": review.reason,
                        "reviewer": review.reviewer,
                        "reviewed_at": _iso(review.created_at),
                    }
                    review_dto = {k: v for k, v in review_dto.items() if v is not None}
                assessment_dto = AssessmentDTO(
                    id=str(assessment.id),
                    thesis_id=str(thesis.id),
                    conclusion=assessment.conclusion,
                    rationale=assessment.rationale,
                    gaps=list(assessment.gaps),
                    provisional=bool(assessment.displayed_as_provisional),
                    review=review_dto,
                )
                if review is None:
                    pending_review += 1

            links = self._research.visible_links(
                thesis_id=thesis.id, cutoff=basis.cutoff
            )
            evidence_total = len(links)
            pending_review += sum(
                1 for link in links if link.review_state == "machine_generated"
            )
            for link in sorted(
                links, key=lambda l: l.available_at, reverse=True
            )[:5]:
                statement = self._research.get_statement(
                    link.source_statement_id
                )
                key_changes.append(
                    KeyChangeDTO(
                        id=str(link.id),
                        tag="新增",
                        text=statement.normalized_text if statement else "",
                        occurred_at=_iso(link.available_at) or "",
                        source_label=statement.kind if statement else "",
                    )
                )

            for step in self._research.causal_steps_for_thesis(
                thesis.id, cutoff=basis.cutoff
            ):
                framework.append(
                    {
                        "id": str(step.id),
                        "sequence": step.sequence,
                        "description": step.description,
                    }
                )

        major_gaps = len(assessment_dto.gaps) if assessment_dto else 0

        return OverviewResponse(
            basis=basis.to_dto(),
            case=CaseSummaryDTO(
                id=str(case.id),
                title=case.title,
                topic=case.industry_topic,
                created_by=case.created_by,
                created_at=_iso(case.created_at) or "",
                updated_at=_iso(case.created_at) or "",
            ),
            thesis=thesis_dto,
            assessment=assessment_dto,
            key_changes=key_changes,
            framework=framework,
            totals=OverviewTotalsDTO(
                evidence_total=evidence_total,
                pending_review=pending_review,
                major_gaps=major_gaps,
            ),
            task_queue=[],
            evidence_changes=[],
            activity=[],
        )
