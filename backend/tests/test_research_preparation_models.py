from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.research_preparation import preparation_input_fingerprint
from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
    ResearchPreparationEvent,
)


def _case(session, *, title: str = "Preparation case") -> ResearchCase:
    case = ResearchCase(
        title=title,
        industry_topic="test",
        created_by="tester",
        created_at=datetime.now(UTC),
    )
    session.add(case)
    session.flush()
    return case


def _run(session, case: ResearchCase) -> ResearchRun:
    now = datetime.now(UTC)
    run = ResearchRun(
        research_case_id=case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    return run


def _preparation(case: ResearchCase, **overrides) -> ResearchPreparation:
    now = datetime.now(UTC)
    values = {
        "research_case_id": case.id,
        "input_fingerprint": "a" * 64,
        "status": "preparing",
        "parse_claims_state": "queued",
        "draft_protocol_state": "queued",
        "draft_evidence_plan_state": "queued",
        "claim_review_state": "locked",
        "protocol_review_state": "locked",
        "plan_review_state": "locked",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return ResearchPreparation(**values)


def test_fingerprint_is_stable_and_changes_with_either_input() -> None:
    document_version_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    scope_version_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    fingerprint = preparation_input_fingerprint(document_version_id, scope_version_id)

    assert fingerprint == "683816ac6665f450fb180a7f73cfee4502c1f39725612713404d46c0c2497da9"
    assert preparation_input_fingerprint(document_version_id, scope_version_id) == fingerprint
    assert preparation_input_fingerprint(uuid.uuid4(), scope_version_id) != fingerprint
    assert preparation_input_fingerprint(document_version_id, uuid.uuid4()) != fingerprint


def test_preparation_has_one_current_row_and_no_run_before_authorization(session) -> None:
    case = _case(session)
    session.add(_preparation(case))
    session.commit()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(_preparation(case))
        session.flush()

    run = _run(session, case)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(_preparation(_case(session, title="Other case"), research_run_id=run.id))
        session.flush()


@pytest.mark.parametrize(
    ("field", "legal_values", "invalid_value"),
    [
        (
            "status",
            (
                "preparing",
                "awaiting_claim_review",
                "awaiting_protocol_confirmation",
                "awaiting_plan_authorization",
                "recoverable_failure",
                "authorized",
            ),
            "invalid_status",
        ),
        (
            "parse_claims_state",
            ("queued", "running", "succeeded", "retrying", "failed", "stale"),
            "invalid_step",
        ),
        (
            "claim_review_state",
            ("locked", "awaiting_review", "confirmed", "stale"),
            "invalid_review",
        ),
    ],
)
def test_preparation_state_values_are_constrained(session, field, legal_values, invalid_value) -> None:
    for number, value in enumerate(legal_values):
        case = _case(session, title=f"Legal {field} {number}")
        overrides = {field: value}
        if value == "authorized":
            overrides["research_run_id"] = _run(session, case).id
        session.add(_preparation(case, **overrides))
    session.commit()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(_preparation(_case(session, title=f"Invalid {field}"), **{field: invalid_value}))
        session.flush()


def test_preparation_artifacts_and_events_are_append_only_sequences(session) -> None:
    case = _case(session)
    preparation = _preparation(case)
    session.add(preparation)
    session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            ResearchPreparationArtifact(
                research_preparation_id=preparation.id,
                kind="atomic_claim_candidates",
                sequence=1,
                input_fingerprint=preparation.input_fingerprint,
                payload={"claims": []},
                state="current",
                created_at=now,
            ),
            ResearchPreparationEvent(
                research_preparation_id=preparation.id,
                seq=1,
                type="claims_parsed",
                step="parse_claims",
                message="Claims parsed",
                detail={},
                created_at=now,
            ),
        ]
    )
    session.commit()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ResearchPreparationArtifact(
                research_preparation_id=preparation.id,
                kind="atomic_claim_candidates",
                sequence=1,
                input_fingerprint=preparation.input_fingerprint,
                payload={"claims": []},
                state="current",
                created_at=now,
            )
        )
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ResearchPreparationEvent(
                research_preparation_id=preparation.id,
                seq=1,
                type="claims_parsed",
                step="parse_claims",
                message="Claims parsed again",
                detail={},
                created_at=now,
            )
        )
        session.flush()
