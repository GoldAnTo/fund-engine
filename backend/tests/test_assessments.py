from datetime import datetime, timezone

import pytest

from app.models.ledger import ValidationError


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
    assessment_service, research_service, research_case
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
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="Multiple primary metrics satisfy the protocol.",
        gaps=[],
    )

    review = assessment_service.review(
        assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="protocol-ready evidence confirmed",
    )

    assert review.conclusion == "supported"
