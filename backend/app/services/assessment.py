from __future__ import annotations

import uuid
from datetime import datetime

from app.models.ledger import (
    AIAssessment,
    EvidenceSnapshot,
    ReviewDecision,
    ValidationError,
)
from app.repositories.research import ResearchRepository

_ASSESSMENT_STATUSES = frozenset(
    {"supported", "contradicted", "insufficient_evidence"}
)


class AssessmentService:
    """Freezes evidence snapshots and admits immutable AI assessments and reviews.

    The service never modifies an existing AIAssessment.  Human review appends a
    ReviewDecision that references the original assessment without overwriting it.
    """

    def __init__(self, repository: ResearchRepository) -> None:
        self._repo = repository

    def freeze_snapshot(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime,
    ) -> EvidenceSnapshot:
        links = self._repo.visible_links(thesis_id=thesis_id, cutoff=cutoff)
        return self._repo.insert_snapshot(
            thesis_id=thesis_id,
            cutoff=cutoff,
            evidence_link_ids=[str(link.id) for link in links],
        )

    def create_ai_assessment(
        self,
        snapshot_id: uuid.UUID,
        *,
        conclusion: str,
        rationale: str,
        gaps: list[str],
    ) -> AIAssessment:
        if conclusion not in _ASSESSMENT_STATUSES:
            raise ValidationError(f"invalid conclusion: {conclusion}")
        return self._repo.insert_ai_assessment(
            snapshot_id=snapshot_id,
            conclusion=conclusion,
            rationale=rationale,
            gaps=gaps,
            displayed_as_provisional=True,
        )

    def review(
        self,
        assessment_id: uuid.UUID,
        *,
        outcome: str,
        conclusion: str | None,
        reason: str,
        reviewer: str = "reviewer",
    ) -> ReviewDecision:
        return self._repo.insert_review(
            ai_assessment_id=assessment_id,
            outcome=outcome,
            conclusion=conclusion,
            reason=reason,
            reviewer=reviewer,
        )

    def get(self, assessment_id: uuid.UUID) -> AIAssessment | None:
        return self._repo.get_ai_assessment(assessment_id)
