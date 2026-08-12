from datetime import datetime, timezone

import pytest

from app.models.ledger import Thesis, ValidationError
from tests.protocol_provenance import seed_protocol_footprint


def _protocol_kwargs(session, snapshot):
    thesis = session.get(Thesis, snapshot.thesis_id)
    assert thesis is not None
    footprint = seed_protocol_footprint(session, thesis)
    return footprint, {
        "effective_binding_id": footprint.binding.id,
        "mechanism_template_version_id": footprint.template.id,
        "verification_rule_ids": [rule.id for rule in footprint.rules],
    }


def test_create_ai_assessment_rejects_invalid_conclusion(assessment_service, snapshot):
    with pytest.raises(ValidationError):
        assessment_service.create_ai_assessment(
            snapshot.id, conclusion="invalid", rationale="x", gaps=[]
        )


def test_ai_assessment_is_displayed_as_provisional(assessment_service, snapshot):
    assessment = assessment_service.create_ai_assessment(
        snapshot.id, conclusion="supported", rationale="x", gaps=["gap1"]
    )
    assert assessment.displayed_as_provisional is True


def test_ai_assessment_freezes_typed_research_protocol_provenance(
    assessment_service, snapshot, session
):
    footprint, _ = _protocol_kwargs(session, snapshot)
    rule_ids = [footprint.rules[1].id, footprint.rules[0].id, footprint.rules[1].id]

    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="Protocol-complete evidence supports the thesis.",
        gaps=[],
        research_protocol_status="ready",
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=rule_ids,
    )

    assert assessment.research_protocol_status == "ready"
    assert assessment.effective_binding_id == footprint.binding.id
    assert assessment.mechanism_template_version_id == footprint.template.id
    assert assessment.verification_rule_ids == sorted(
        {str(rule_id) for rule_id in rule_ids}
    )


def test_ai_assessment_rejects_an_incomplete_or_blocked_protocol_footprint(
    assessment_service, snapshot
):
    with pytest.raises(ValidationError, match="complete"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="insufficient_evidence",
            rationale="Incomplete provenance is not auditable.",
            gaps=[],
            research_protocol_status="single_metric_monitoring",
        )
    with pytest.raises(ValidationError, match="blocked"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="insufficient_evidence",
            rationale="Blocked protocols cannot persist assessments.",
            gaps=[],
            research_protocol_status="blocked",
        )


def test_single_metric_protocol_provenance_rejects_directional_ai_conclusion(
    assessment_service, snapshot, session
):
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="supported",
            rationale="A directional result is unsafe for monitoring-only protocol.",
            gaps=[],
            research_protocol_status="single_metric_monitoring",
            **protocol_kwargs,
        )


def test_human_review_does_not_change_ai_assessment(assessment_service, ai_assessment):
    review = assessment_service.review(
        ai_assessment.id,
        outcome="modified",
        conclusion="insufficient_evidence",
        reason="scope mismatch",
    )
    assert assessment_service.get(ai_assessment.id).conclusion == "supported"
    assert review.ai_assessment_id == ai_assessment.id


@pytest.mark.parametrize("outcome", ["confirmed", "modified"])
def test_strict_single_metric_assessment_review_cannot_publish_directional_conclusion(
    assessment_service, research_service, research_case, outcome
):
    strict_thesis = research_service.add_thesis(
        research_case.id,
        statement="A strict single-metric thesis",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        strict_thesis.id,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Only one primary metric is available.",
        gaps=["insufficient_primary_metrics"],
    )

    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.review(
            assessment.id,
            outcome=outcome,
            conclusion="supported",
            reason="human override",
        )


def test_non_strict_review_preserves_legacy_directional_conclusion(
    assessment_service, ai_assessment
):
    review = assessment_service.review(
        ai_assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="legacy workflow remains valid",
    )

    assert review.conclusion == "supported"


def test_strict_ready_assessment_review_preserves_directional_conclusion(
    assessment_service, research_service, research_case, session
):
    strict_thesis = research_service.add_thesis(
        research_case.id,
        statement="A strict thesis with complete protocol provenance",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        strict_thesis.id,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="Multiple primary metrics satisfy the protocol.",
        gaps=["insufficient_primary_metrics"],
        research_protocol_status="ready",
        **protocol_kwargs,
    )

    review = assessment_service.review(
        assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="protocol-ready evidence confirmed",
    )

    assert review.conclusion == "supported"


def test_typed_single_metric_review_rejects_directional_conclusion_for_any_outcome(
    assessment_service, snapshot, session
):
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Monitoring only.",
        gaps=[],
        research_protocol_status="single_metric_monitoring",
        **protocol_kwargs,
    )

    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.review(
            assessment.id,
            outcome="rejected",
            conclusion="supported",
            reason="A rejected review conclusion is still consumed by read models.",
        )


def test_typed_single_metric_review_rejects_null_that_falls_back_to_directional_ai_result(
    assessment_service, snapshot
):
    # Simulate a malformed imported row so null review semantics cannot reopen
    # a directional conclusion. The public creation service rejects this row.
    assessment = assessment_service._repo.insert_ai_assessment(
        snapshot_id=snapshot.id,
        conclusion="contradicted",
        rationale="Historical malformed directional draft.",
        gaps=[],
        research_protocol_status="single_metric_monitoring",
    )

    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.review(
            assessment.id,
            outcome="rejected",
            conclusion=None,
            reason="Null would expose the directional AI fallback.",
        )


def test_typed_single_metric_review_permits_insufficient_evidence(
    assessment_service, snapshot, session
):
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Monitoring only.",
        gaps=[],
        research_protocol_status="single_metric_monitoring",
        **protocol_kwargs,
    )

    review = assessment_service.review(
        assessment.id,
        outcome="rejected",
        conclusion="insufficient_evidence",
        reason="The non-directional conclusion remains safe.",
    )

    assert review.conclusion == "insufficient_evidence"
