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
        if conclusion not in _ASSESSMENT_STATUSES:
            raise ValidationError(f"invalid conclusion: {conclusion}")
        if (
            research_protocol_status is not None
            and research_protocol_status not in _RESEARCH_PROTOCOL_STATUSES
        ):
            raise ValidationError(
                f"invalid research protocol status: {research_protocol_status}"
            )
        if (
            research_protocol_status == "single_metric_monitoring"
            and conclusion in {"supported", "contradicted"}
        ):
            raise ValidationError(
                "strict single-metric assessments require an insufficient_evidence conclusion"
            )
        if research_protocol_status == "blocked":
            raise ValidationError(
                "blocked research protocols cannot persist an AI assessment"
            )
        provenance_values = (
            effective_binding_id,
            mechanism_template_version_id,
            verification_rule_ids,
        )
        if research_protocol_status is None:
            if any(value is not None for value in provenance_values):
                raise ValidationError(
                    "legacy assessments cannot persist partial protocol provenance"
                )
            normalized_rule_ids = None
        else:
            if (
                effective_binding_id is None
                or mechanism_template_version_id is None
                or not verification_rule_ids
            ):
                raise ValidationError(
                    "strict assessments require a complete protocol provenance footprint"
                )
            try:
                parsed_rule_ids = {
                    uuid.UUID(str(rule_id)) for rule_id in verification_rule_ids
                }
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError(
                    "verification_rule_ids must contain UUID values"
                ) from exc
            normalized_rule_uuids = sorted(parsed_rule_ids, key=str)
            if not self._repo.assessment_protocol_footprint_is_consistent(
                snapshot_id=snapshot_id,
                effective_binding_id=effective_binding_id,
                mechanism_template_version_id=mechanism_template_version_id,
                verification_rule_ids=normalized_rule_uuids,
            ):
                raise ValidationError(
                    "assessment protocol provenance does not match the snapshot thesis"
                )
            normalized_rule_ids = [
                str(rule_id) for rule_id in normalized_rule_uuids
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
            and assessment.research_protocol_status
            == "single_metric_monitoring"
            and effective_conclusion in {"supported", "contradicted"}
        ):
            raise ValidationError(
                "strict single-metric assessments require an insufficient_evidence review conclusion"
            )

        # Compatibility for strict assessments written before typed protocol
        # provenance existed. New assessments never infer protocol state from
        # model-controlled gap prose.
        thesis = (
            self._repo.assessment_thesis(assessment_id)
            if assessment is not None
            and assessment.research_protocol_status is None
            else None
        )
        if (
            outcome in {"confirmed", "modified"}
            and effective_conclusion in {"supported", "contradicted"}
            and assessment is not None
            and thesis is not None
            and thesis.research_protocol_required
            and "insufficient_primary_metrics" in (assessment.gaps or [])
        ):
            raise ValidationError(
                "strict single-metric assessments require an insufficient_evidence review conclusion"
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
