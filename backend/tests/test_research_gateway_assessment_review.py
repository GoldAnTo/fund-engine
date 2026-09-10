"""Assessment review is an authorized, read-only view of historical lineage."""

import json
from datetime import date
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, text

from app.api.v1.tenant_context import ResearchActor
from app.models.event_research import EventResearchConclusion
from app.models.ledger import AIAssessment, EvidenceSnapshot
from app.models.operational import ResearchTask
from app.schemas.v1.research_gateway_content import AssessmentReviewDTO
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.research_gateway_content import ResearchGatewayContent
from tests import test_research_gateway_content as gateway_content
from tests.test_research_gateway_artifact_authorization import (
    complete_authorized_evidence,
)
from tests.test_research_gateway_content import (
    BASE,
    _contract,
    _path,
    _pending,
)

gateway_client = gateway_content.gateway_client
ACTOR = ResearchActor("team-a", frozenset(), "alice")


def _content(session, spec):
    return ResearchGatewayContent(session).read_research(
        ACTOR, spec.conversation_id, spec.id
    )


def _review(session, spec, run, evidence, *, truncated=False):
    from app.services.research_gateway_assessment_review import read_assessment_review

    return read_assessment_review(session, spec, run, evidence, truncated=truncated)


def test_assessment_review_api_contract(gateway_client, cmd_session):
    client, _ = gateway_client
    spec, _, _ = complete_authorized_evidence(cmd_session)
    response = client.get(_path(spec))
    assert response.status_code == 200
    body = response.json()
    assert "assessment_review" in body
    review = body["assessment_review"]
    assert set(review) == {
        "method_version",
        "state",
        "reason_code",
        "items",
        "quality_flags",
    }
    assert review["method_version"] == "gateway-assessment-review/v1"
    assert review["state"] == "available"
    assert review["reason_code"] is None
    assert review["items"]
    assert set(review["items"][0]) == {
        "assessment_id",
        "snapshot_id",
        "task_id",
        "thesis_id",
        "thesis_statement",
        "conclusion",
        "rationale",
        "gaps",
        "evidence_link_ids",
        "quality_flags",
    }


def test_actual_snapshot_mapping_scope_order_and_read_only_history(cmd_session):
    spec, run, links = complete_authorized_evidence(cmd_session)
    scope = load_automatic_research_scope(cmd_session, run)
    # Decoy tasks from a previous round must never become assessment inputs.
    from tests.test_research_gateway_artifact_authorization import _copy_row

    task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id, ResearchTask.task_type == "result"
        )
    )
    cmd_session.add(_copy_row(task, round=0, result={"assessment_id": str(uuid4())}))
    cmd_session.commit()
    history = [row.text for row in cmd_session.scalars(select(EventResearchConclusion))]
    writes = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(cmd_session.bind, "before_cursor_execute", record)
    try:
        first = _content(cmd_session, spec)
        second = _content(cmd_session, spec)
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", record)
    assert writes == []
    assert first.model_dump() == second.model_dump()
    review = first.assessment_review
    assert review.state == "available"
    assert [item.thesis_id for item in review.items] == [
        str(value) for value in scope.factor_ids
    ]
    assert {value for item in review.items for value in item.evidence_link_ids} == {
        str(link.id) for link in links
    }
    for item in review.items:
        task = cmd_session.get(ResearchTask, UUID(item.task_id))
        assessment = cmd_session.get(AIAssessment, UUID(item.assessment_id))
        snapshot = cmd_session.get(EvidenceSnapshot, UUID(item.snapshot_id))
        assert task.round == run.round
        assert task.result["assessment_id"] == item.assessment_id
        assert assessment.snapshot_id == snapshot.id
        assert snapshot.thesis_id == UUID(item.thesis_id)
        assert item.evidence_link_ids == snapshot.evidence_link_ids
        assert (item.conclusion, item.rationale, item.gaps) == (
            assessment.conclusion,
            assessment.rationale,
            assessment.gaps,
        )
    assert history == [
        row.text for row in cmd_session.scalars(select(EventResearchConclusion))
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "old_round",
        "wrong_run",
        "wrong_case",
        "not_done",
        "not_completed",
        "wrong_thesis",
        "missing_assessment",
        "other_assessment",
        "missing_snapshot",
        "other_snapshot",
        "malformed_result",
        "malformed_snapshot",
        "duplicate_links",
        "unrelated_link",
        "missing_union",
    ],
)
def test_changed_lineage_has_no_partial_review(cmd_session, mutation):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    item, other = content.assessment_review.items[:2]
    task = cmd_session.get(ResearchTask, UUID(item.task_id))
    assessment = cmd_session.get(AIAssessment, UUID(item.assessment_id))
    snapshot = cmd_session.get(EvidenceSnapshot, UUID(item.snapshot_id))
    if mutation in {
        "old_round",
        "wrong_run",
        "wrong_case",
        "not_done",
        "not_completed",
        "wrong_thesis",
    }:
        field, value = {
            "old_round": ("round", 0),
            "wrong_run": ("run_id", uuid4()),
            "wrong_case": ("research_case_id", uuid4()),
            "not_done": ("status", "queued"),
            "not_completed": ("stage", "queued"),
            "wrong_thesis": ("thesis_id", UUID(other.thesis_id)),
        }[mutation]
        setattr(task, field, value)
    elif mutation in {"missing_assessment", "other_assessment", "malformed_result"}:
        task.result = (
            []
            if mutation == "malformed_result"
            else {
                "assessment_id": other.assessment_id
                if mutation == "other_assessment"
                else str(uuid4())
            }
        )
    elif mutation in {"missing_snapshot", "other_snapshot"}:
        cmd_session.execute(
            text("UPDATE ai_assessments SET snapshot_id = :value WHERE id = :id"),
            {
                "value": (
                    UUID(other.snapshot_id) if mutation == "other_snapshot" else uuid4()
                ).hex,
                "id": assessment.id.hex,
            },
        )
    else:
        values = {
            "malformed_snapshot": {},
            "duplicate_links": snapshot.evidence_link_ids * 2,
            "unrelated_link": [str(uuid4())],
            "missing_union": [],
        }[mutation]
        cmd_session.execute(
            text(
                "UPDATE evidence_snapshots SET evidence_link_ids = :value WHERE id = :id"
            ),
            {
                "value": json.dumps(values),
                "id": snapshot.id.hex,
            },
        )
    # Flush only into the isolated SQLite fixture; no live database is used.
    cmd_session.flush()
    cmd_session.expire_all()
    review = _review(cmd_session, spec, run, content.evidence)
    assert review.state == "unavailable"
    assert review.reason_code == "lineage_unavailable"
    assert review.items == review.quality_flags == []


def test_pending_and_revoked_report_have_null_review(gateway_client, cmd_session):
    client, _ = gateway_client
    pending = _pending(client)
    response = client.get(
        f"{BASE}/{pending['conversation_id']}/runs/{pending['run_spec_id']}/research"
    )
    assert response.json()["assessment_review"] is None
    spec, _, links = complete_authorized_evidence(cmd_session)
    assert _content(cmd_session, spec).assessment_review.state == "available"
    contract = _contract(cmd_session, links[0])
    cmd_session.execute(
        text("UPDATE source_contracts SET allow_display = false WHERE id = :id"),
        {"id": contract.id.hex},
    )
    cmd_session.commit()
    content = _content(cmd_session, spec)
    assert content.result is None
    assert content.assessment_review is None


def test_truncated_evidence_never_claims_complete_review(cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    review = _review(cmd_session, spec, run, content.evidence, truncated=True)
    assert review.state == "unavailable"
    assert review.reason_code == "evidence_list_truncated"
    assert review.items == review.quality_flags == []


@pytest.mark.parametrize(
    "authorities,periods,expected",
    [
        (["primary_disclosure"], [date(2026, 1, 31)], []),
        (["official_disclosure"], [None], ["unknown_data_period"]),
        (
            ["user_supplied", "user_material", "intake_material"],
            [date(2025, 1, 31), date(2026, 1, 31), None],
            [
                "mixed_data_periods",
                "unknown_data_period",
                "user_material_only",
                "no_primary_disclosure",
            ],
        ),
        (
            ["user_supplied", "industry_research"],
            [date(2026, 1, 31)] * 2,
            ["no_primary_disclosure"],
        ),
    ],
)
def test_quality_flags_use_actual_metadata(cmd_session, authorities, periods, expected):
    from app.services.research_gateway_assessment_review import quality_flags

    spec, _, _ = complete_authorized_evidence(cmd_session)
    row = _content(cmd_session, spec).evidence[0]
    evidence = [
        row.model_copy(
            update={"source_authority": authority, "observed_period": period}
        )
        for authority, period in zip(authorities, periods)
    ]
    assert quality_flags(evidence) == expected + [
        "source_independence_unverified",
        "retrieval_direction_unverified",
    ]


def test_global_quality_periods_include_union_across_separate_assessments(cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    evidence = [
        row.model_copy(update={"observed_period": date(2025 + index, 1, 31)})
        for index, row in enumerate(content.evidence)
    ]
    review = _review(cmd_session, spec, run, evidence)
    assert review.state == "available"
    assert "mixed_data_periods" in review.quality_flags
    assert all("mixed_data_periods" not in item.quality_flags for item in review.items)


def test_schema_rejects_duplicate_evidence_across_items(cmd_session):
    spec, _, _ = complete_authorized_evidence(cmd_session)
    review = _content(cmd_session, spec).assessment_review.model_dump()
    review["items"][1]["evidence_link_ids"] = review["items"][0]["evidence_link_ids"]
    with pytest.raises(ValidationError):
        AssessmentReviewDTO.model_validate(review)


def test_review_only_invalidity_keeps_original_report(gateway_client, cmd_session):
    client, _ = gateway_client
    spec, _, _ = complete_authorized_evidence(cmd_session)
    original = client.get(_path(spec)).json()
    item = original["assessment_review"]["items"][0]
    # Empty gaps do not alter the existing report builder's text, but an
    # oversized historical array cannot fit the bounded review contract.
    gaps = item["gaps"] + [""] * 201
    cmd_session.execute(
        text("UPDATE ai_assessments SET gaps = :value WHERE id = :id"),
        {
            "value": json.dumps(gaps),
            "id": UUID(item["assessment_id"]).hex,
        },
    )
    cmd_session.commit()
    body = client.get(_path(spec)).json()
    assert body["state"] == "available"
    assert body["result"] == original["result"]
    assert body["assessment_review"] == {
        "method_version": "gateway-assessment-review/v1",
        "state": "unavailable",
        "reason_code": "lineage_unavailable",
        "items": [],
        "quality_flags": [],
    }


def test_api_truncation_keeps_original_report(gateway_client, cmd_session, monkeypatch):
    client, _ = gateway_client
    spec, _, _ = complete_authorized_evidence(cmd_session)
    original = client.get(_path(spec)).json()
    monkeypatch.setattr("app.services.research_gateway_content._EVIDENCE_LIMIT", 1)
    body = client.get(_path(spec)).json()
    assert body["result"] == original["result"]
    assert body["truncated"] is True
    assert body["assessment_review"]["reason_code"] == "evidence_list_truncated"
    assert body["assessment_review"]["items"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_id",
        "empty_items",
        "empty_links",
        "many_items",
        "many_gaps",
        "many_links",
        "bad_flag",
        "many_flags",
    ],
)
def test_review_contract_bounds(cmd_session, mutation):
    spec, _, _ = complete_authorized_evidence(cmd_session)
    payload = _content(cmd_session, spec).assessment_review.model_dump()
    item = payload["items"][0]
    if mutation == "empty_id":
        item["assessment_id"] = ""
    elif mutation == "empty_items":
        payload["items"] = []
    elif mutation == "empty_links":
        item["evidence_link_ids"] = []
    elif mutation == "many_items":
        payload["items"] *= 201
    elif mutation == "many_gaps":
        item["gaps"] = ["gap"] * 201
    elif mutation == "many_links":
        item["evidence_link_ids"] = [str(uuid4()) for _ in range(201)]
    elif mutation == "bad_flag":
        payload["quality_flags"] = ["verified_truth"]
    else:
        item["quality_flags"] *= 7
    with pytest.raises(ValidationError):
        AssessmentReviewDTO.model_validate(payload)


def test_empty_snapshot_is_unavailable_even_when_remaining_union_is_complete(
    cmd_session,
):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    item = content.assessment_review.items[0]
    cmd_session.execute(
        text("UPDATE evidence_snapshots SET evidence_link_ids = '[]' WHERE id = :id"),
        {"id": UUID(item.snapshot_id).hex},
    )
    cmd_session.flush()
    cmd_session.expire_all()
    # Model an authorized report whose complete evidence inventory contains
    # only the other factors. A complete union cannot repair an empty item.
    evidence = [row for row in content.evidence if row.thesis_id != item.thesis_id]
    review = _review(cmd_session, spec, run, evidence)
    assert review.state == "unavailable"
    assert review.reason_code == "lineage_unavailable"
    assert review.items == review.quality_flags == []


def test_item_user_material_flags_remain_contextual_in_mixed_official_union(
    cmd_session,
):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    user_thesis = content.assessment_review.items[0].thesis_id
    evidence = [
        row.model_copy(
            update={
                "source_authority": "user_supplied"
                if row.thesis_id == user_thesis
                else "official_disclosure"
            }
        )
        for row in content.evidence
    ]
    review = _review(cmd_session, spec, run, evidence)
    assert review.state == "available"
    assert "user_material_only" not in review.quality_flags
    assert "no_primary_disclosure" not in review.quality_flags
    user_item = next(item for item in review.items if item.thesis_id == user_thesis)
    assert "user_material_only" in user_item.quality_flags
    assert "no_primary_disclosure" in user_item.quality_flags
    assert all(
        "user_material_only" not in item.quality_flags
        for item in review.items
        if item.thesis_id != user_thesis
    )


def test_review_result_task_query_is_bounded_with_excess_detection(cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    content = _content(cmd_session, spec)
    captured = []

    def record(_conn, _cursor, statement, parameters, _context, _executemany):
        if (
            "FROM research_tasks" in statement
            and "research_tasks.task_type =" in statement
        ):
            captured.append((statement, parameters))

    event.listen(cmd_session.bind, "before_cursor_execute", record)
    try:
        review = _review(cmd_session, spec, run, content.evidence)
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", record)
    assert review.state == "available"
    assert len(captured) == 1
    statement, parameters = captured[0]
    assert "LIMIT" in statement
    assert parameters[-2] == len(review.items) + 1

    from tests.test_research_gateway_artifact_authorization import _copy_row

    task = cmd_session.get(ResearchTask, UUID(review.items[0].task_id))
    cmd_session.add(_copy_row(task))
    cmd_session.flush()
    invalid = _review(cmd_session, spec, run, content.evidence)
    assert invalid.state == "unavailable"
    assert invalid.items == invalid.quality_flags == []
