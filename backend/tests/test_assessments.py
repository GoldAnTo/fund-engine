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
