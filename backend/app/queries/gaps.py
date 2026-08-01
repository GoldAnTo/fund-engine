"""Case-level evidence-gap aggregation (prototype 研究计划 · 证据缺口).

Gaps live on provisional AIAssessments (append-only JSON list).  This read
model collects the gaps of each thesis's latest assessment at a cutoff —
the 「当前缺口」 view the research-plan page renders.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.repositories.research import ResearchRepository
from app.schemas.v1.gaps import CaseGapDTO, CaseGapsResponse


class CaseGapQueries:
    def __init__(self, db: Session) -> None:
        self._repo = ResearchRepository(db)

    def list_gaps(
        self, *, case_id: uuid.UUID, cutoff: datetime | None = None
    ) -> CaseGapsResponse:
        cutoff = cutoff or datetime.now(timezone.utc)
        if self._repo.get_case(case_id, cutoff=cutoff) is None:
            raise NotFoundError(f"research case {case_id} not found")

        gaps: list[CaseGapDTO] = []
        for thesis in self._repo.theses_for_case(case_id, cutoff=cutoff):
            assessment = self._repo.latest_assessment_for_thesis(
                thesis.id, cutoff=cutoff
            )
            if assessment is None:
                continue
            for gap in assessment.gaps:
                gaps.append(
                    CaseGapDTO(
                        thesis_id=str(thesis.id),
                        thesis_statement=thesis.statement,
                        conclusion=assessment.conclusion,
                        gap=str(gap),
                        assessment_id=str(assessment.id),
                    )
                )
        return CaseGapsResponse(
            case_id=str(case_id), cutoff=cutoff.isoformat(), gaps=gaps
        )
