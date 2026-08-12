from __future__ import annotations

import uuid
from datetime import datetime

from app.models.ledger import (
    AIAssessment,
    EvidenceSnapshot,
    ReviewDecision,
    Thesis,
    ValidationError,
)
from app.repositories.research import ResearchRepository
from app.services.event_research_scope_evidence import lock_event_scope_case
from app.services.research_protocol import ResearchProtocolService

_ASSESSMENT_STATUSES = frozenset(
    {"supported", "contradicted", "insufficient_evidence"}
)
_RESEARCH_PROTOCOL_STATUSES = frozenset(
    {"blocked", "single_metric_monitoring", "ready"}
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
        research_protocol_status: str | None = None,
        effective_binding_id: uuid.UUID | None = None,
        mechanism_template_version_id: uuid.UUID | None = None,
        verification_rule_ids: list[uuid.UUID | str] | None = None,
    ) -> AIAssessment:
        session = self._repo.session
        snapshot = session.get(EvidenceSnapshot, snapshot_id)
        if snapshot is None:
            raise ValidationError("evidence snapshot not found")
        thesis = session.get(Thesis, snapshot.thesis_id)
        if thesis is None:
            raise ValidationError("snapshot thesis not found")

        # Creation is an independently safe persistence boundary. Resolve the
        # immutable snapshot/thesis only to discover the stable root, then use
        # the same Case -> protocol row lock order as every protocol mutation.
        lock_event_scope_case(session, thesis.research_case_id)
        session.refresh(snapshot)
        session.refresh(thesis)
        if conclusion not in _ASSESSMENT_STATUSES:
            raise ValidationError(f"invalid conclusion: {conclusion}")
        if (
            research_protocol_status is not None
            and research_protocol_status not in _RESEARCH_PROTOCOL_STATUSES
        ):
            raise ValidationError(
                f"invalid research protocol status: {research_protocol_status}"
            )
        provenance_values = (
            research_protocol_status,
            effective_binding_id,
            mechanism_template_version_id,
            verification_rule_ids,
        )
        if not thesis.research_protocol_required:
            if any(value is not None for value in provenance_values):
                raise ValidationError(
                    "non-strict assessments cannot persist protocol provenance"
                )
            normalized_rule_ids = None
        else:
            if research_protocol_status is None:
                raise ValidationError(
                    "strict assessments require typed protocol provenance"
                )
            current = ResearchProtocolService(session).check_researchability(thesis.id)
            if current.status == "blocked":
                raise ValidationError(
                    "blocked research protocols cannot persist an AI assessment"
                )
            try:
                parsed_rule_ids = {
                    uuid.UUID(str(rule_id))
                    for rule_id in (verification_rule_ids or [])
                }
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError(
                    "verification_rule_ids must contain UUID values"
                ) from exc
            normalized_rule_uuids = sorted(parsed_rule_ids, key=str)
            current_rule_uuids = sorted(
                set(current.verification_rule_ids), key=str
            )
            if (
                research_protocol_status != current.status
                or effective_binding_id != current.effective_binding_id
                or mechanism_template_version_id
                != current.mechanism_template_version_id
                or normalized_rule_uuids != current_rule_uuids
            ):
                raise ValidationError(
                    "assessment provenance does not match the current research protocol"
                )
            if conclusion not in ResearchProtocolService.allowed_assessment_conclusions(
                current
            ):
                raise ValidationError(
                    "strict single-metric assessments require an insufficient_evidence conclusion"
                )
            normalized_rule_ids = [
                str(rule_id) for rule_id in current_rule_uuids
            ]
        return self._repo.insert_ai_assessment(
            snapshot_id=snapshot_id,
            conclusion=conclusion,
            rationale=rationale,
            gaps=gaps,
            research_protocol_status=research_protocol_status,
            effective_binding_id=effective_binding_id,
            mechanism_template_version_id=mechanism_template_version_id,
            verification_rule_ids=normalized_rule_ids,
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
        assessment = self._repo.get_ai_assessment(assessment_id)
        effective_conclusion = conclusion or (
            assessment.conclusion if assessment is not None else None
        )
        if (
            assessment is not None
            and effective_conclusion in {"supported", "contradicted"}
            and assessment.research_protocol_status
            == "single_metric_monitoring"
        ):
            raise ValidationError(
                "strict single-metric assessments require an insufficient_evidence review conclusion"
            )
        if (
            assessment is not None
            and effective_conclusion in {"supported", "contradicted"}
            and assessment.research_protocol_status == "blocked"
        ):
            raise ValidationError(
                "blocked protocol assessments require a non-directional review conclusion"
            )
        return self._repo.insert_review(
            ai_assessment_id=assessment_id,
            outcome=outcome,
            conclusion=conclusion,
            reason=reason,
            reviewer=reviewer,
        )

    def get(self, assessment_id: uuid.UUID) -> AIAssessment | None:
        return self._repo.get_ai_assessment(assessment_id)
