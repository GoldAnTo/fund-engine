from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy.orm import Session

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
_RESEARCH_PROTOCOL_STATUSES = frozenset({"single_metric_monitoring", "ready"})
_FACTOR_RELEVANCE = frozenset({"direct", "indirect", "unclear"})
_FACTOR_CAUSAL_IMPACT = frozenset({"high", "medium", "low", "unclear"})
_FACTOR_EVIDENCE_STRENGTH = frozenset({"strong", "moderate", "weak", "none"})
_FACTOR_COUNTER_EVIDENCE = frozenset({"none", "mixed", "material", "unknown"})


def normalize_factor_judgement(
    *,
    conclusion: str,
    raw_judgement: object,
    has_material_counter_evidence: bool = False,
) -> dict[str, str]:
    """Make factor status reproducible from the four required gates.

    The model may supply explanatory labels, but it cannot self-certify a
    factor as key: the classification is derived here and frozen alongside
    the assessment.
    """
    source = raw_judgement if isinstance(raw_judgement, Mapping) else {}

    def value(name: str, allowed: frozenset[str], fallback: str) -> str:
        candidate = source.get(name)
        return candidate if isinstance(candidate, str) and candidate in allowed else fallback

    relevance = value("relevance", _FACTOR_RELEVANCE, "unclear")
    causal_impact = value("causal_impact", _FACTOR_CAUSAL_IMPACT, "unclear")
    evidence_strength = value("evidence_strength", _FACTOR_EVIDENCE_STRENGTH, "none")
    counter_evidence = value("counter_evidence", _FACTOR_COUNTER_EVIDENCE, "unknown")
    if has_material_counter_evidence:
        counter_evidence = "material"

    if conclusion == "contradicted" or counter_evidence == "material":
        classification = "excluded"
    elif conclusion != "supported" or evidence_strength in {"weak", "none"}:
        classification = "pending"
    elif (
        relevance == "direct"
        and causal_impact == "high"
        and evidence_strength == "strong"
        and counter_evidence == "none"
    ):
        classification = "key"
    else:
        classification = "secondary"

    ranking_reason = source.get("ranking_reason")
    if not isinstance(ranking_reason, str) or not ranking_reason.strip():
        ranking_reason = (
            "该因素与研究命题直接相关，具有高因果影响，且当前冻结证据充分并未发现实质反证。"
            if classification == "key"
            else "该因素尚未同时满足直接相关、因果影响、证据强度和反证检验四项条件。"
        )
    return {
        "relevance": relevance,
        "causal_impact": causal_impact,
        "evidence_strength": evidence_strength,
        "counter_evidence": counter_evidence,
        "classification": classification,
        "ranking_reason": ranking_reason.strip(),
    }


class AssessmentService:
    """Freezes evidence snapshots and admits immutable AI assessments and reviews.

    The service never modifies an existing AIAssessment.  Human review appends a
    ReviewDecision that references the original assessment without overwriting it.
    """

    def __init__(self, repository: ResearchRepository, session: Session) -> None:
        self._repo = repository
        self._session = session

    def freeze_snapshot(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime,
        evidence_link_ids: list[uuid.UUID | str] | None = None,
    ) -> EvidenceSnapshot:
        if evidence_link_ids is None:
            links = self._repo.visible_links(thesis_id=thesis_id, cutoff=cutoff)
        else:
            try:
                captured_ids = [
                    uuid.UUID(str(link_id)) for link_id in evidence_link_ids
                ]
                links = self._repo.visible_links_by_ids(
                    thesis_id=thesis_id,
                    cutoff=cutoff,
                    evidence_link_ids=captured_ids,
                )
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError(str(exc)) from exc
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
        factor_judgement: object = None,
    ) -> AIAssessment:
        session = self._session
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
            factor_judgement=normalize_factor_judgement(
                conclusion=conclusion,
                raw_judgement=factor_judgement,
            ),
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
        if assessment is not None and effective_conclusion in {
            "supported",
            "contradicted",
        }:
            if assessment.research_protocol_status == "single_metric_monitoring":
                raise ValidationError(
                    "strict single-metric assessments require an "
                    "insufficient_evidence review conclusion"
                )
            if assessment.research_protocol_status == "blocked":
                raise ValidationError(
                    "blocked protocol assessments require a non-directional "
                    "review conclusion"
                )
        if (
            assessment is not None
            and effective_conclusion in {"supported", "contradicted"}
            and assessment.research_protocol_status is None
        ):
            thesis = self._repo.assessment_thesis(assessment_id)
            if thesis is not None and thesis.research_protocol_required:
                raise ValidationError(
                    "strict historical assessment is missing protocol provenance; "
                    "directional review conclusions are not allowed"
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
