import pytest

from app.models.ledger import ValidationError


def test_research_opinion_is_not_stored_as_disclosed_fact(research_service, span):
    statement = research_service.add_statement(
        span.id, "预计需求增长", kind="research_opinion"
    )
    assert statement.kind == "research_opinion"


def test_evidence_link_requires_reason_scope_and_available_time(
    research_service, thesis, statement
):
    with pytest.raises(ValidationError):
        research_service.link_evidence(
            thesis.id, statement.id, role="supports", reason="", scope={}
        )


def test_evidence_link_is_machine_generated_until_reviewed(
    research_service, thesis, statement
):
    link = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    assert link.creator_type == "ai"
    assert link.review_state == "machine_generated"
