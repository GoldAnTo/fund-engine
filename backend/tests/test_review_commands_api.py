"""Command-side v1 API tests (prototype 新建研究 / 审核工作区).

Uses the private-engine ``cmd_*`` fixtures from conftest: command endpoints
COMMIT, so they never share the session-scoped engine.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from app.models.ledger import Thesis


def _error_code(response) -> str:
    return response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# 新建研究: POST /api/v1/research-cases
# ---------------------------------------------------------------------------


def test_retired_case_creation_requires_auth_and_never_writes(cmd_client, cmd_session):
    from sqlalchemy import func
    from app.models.ledger import ResearchCase, Thesis, DocumentVersion

    payload = {"title": "旧无来源创建", "industry_topic": "ai_compute", "created_by": "u"}
    anonymous = cmd_client.post(
        "/api/v1/research-cases", json=payload, headers={"Authorization": ""}
    )
    assert anonymous.status_code == 401
    authenticated = cmd_client.post("/api/v1/research-cases", json=payload)
    assert authenticated.status_code == 409
    assert _error_code(authenticated) == "conflict"
    assert "/api/v1/event-research" in authenticated.json()["error"]["message"]
    operation = cmd_client.get("/openapi.json").json()["paths"]["/api/v1/research-cases"]["post"]
    assert operation["deprecated"] is True
    assert "409" in operation["responses"]
    assert "201" not in operation["responses"]
    for model in (ResearchCase, Thesis, DocumentVersion):
        assert cmd_session.scalar(select(func.count()).select_from(model)) == 0


def test_event_creation_can_open_legacy_workbench_and_add_theses(cmd_client, cmd_session):
    from tests.event_case_factory import create_event_case
    from app.models.ledger import Thesis

    case_id = create_event_case(cmd_client)
    workbench = cmd_client.get(f"/api/research-cases/{case_id}/workbench")
    assert workbench.status_code == 200, workbench.text
    for creator, expected_state in (("human", "confirmed"), ("ai", "draft")):
        response = cmd_client.post(
            f"/api/v1/research-cases/{case_id}/theses",
            json={
                "statement": f"{creator} 资本开支形成持续算力需求",
                "created_by": "analyst-test",
                "creator_type": creator,
                "title": "新增论点",
                "observation_start": "2026-01-01",
                "observation_end": "2027-12-31",
                "support_condition": "主要云厂商给出扩张指引",
                "falsification_condition": "主要云厂商下调资本开支",
                "next_verification_event": "核对季度财报",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["thesis"]["review_state"] == expected_state
        thesis = cmd_session.get(Thesis, uuid.UUID(response.json()["thesis"]["id"]))
        assert thesis.falsification_condition == "主要云厂商下调资本开支"


def test_retired_case_creation_does_not_resume_legacy_period_validation(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "x",
            "industry_topic": "t",
            "created_by": "u",
            "period_start": "2027-01-01",
            "period_end": "2026-01-01",
        },
    )
    assert response.status_code == 409
    assert _error_code(response) == "conflict"


def test_add_thesis_to_missing_case_is_404(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/theses",
        json={"statement": "x", "created_by": "u"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_add_thesis_rejects_inverted_observation_window(cmd_client, cmd_session):
    from tests.event_case_factory import create_event_case

    case_id = create_event_case(cmd_client)

    response = cmd_client.post(
        f"/api/v1/research-cases/{case_id}/theses",
        json={
            "statement": "s",
            "created_by": "u",
            "observation_start": "2027-01-01",
            "observation_end": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


# ---------------------------------------------------------------------------
# 审核队列: GET /api/v1/review-queue
# ---------------------------------------------------------------------------


def test_review_queue_lists_pending_machine_links(cmd_client, cmd_seeded):
    response = cmd_client.get("/api/v1/review-queue")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 15  # all seeded links are machine-generated

    item = items[0]
    assert item["ai_role"] in {"supports", "contradicts", "contextualizes"}
    assert item["verbatim_text"], "queue item must carry the frozen span text"
    assert item["thesis_statement"]
    assert item["document_source_url"]


def test_review_queue_empty_when_nothing_seeded(cmd_client, cmd_session):
    response = cmd_client.get("/api/v1/review-queue")
    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# 关系级审核: POST /api/v1/evidence-links/{id}/reviews
# ---------------------------------------------------------------------------


def _first_queue_item_id(cmd_client) -> str:
    return cmd_client.get("/api/v1/review-queue").json()["items"][0]["link_id"]


def test_confirmed_link_review_leaves_queue(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)

    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "supports",
            "factor_role": "需求驱动因素",
            "scope_boundary": "仅适用于当前截止日与该分部口径",
            "reason": "原文披露与 AI 提议一致",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text
    review = response.json()["review"]
    assert review["outcome"] == "confirmed"
    assert review["relation"] == "supports"

    remaining = cmd_client.get("/api/v1/review-queue").json()["items"]
    assert len(remaining) == 14
    assert all(i["link_id"] != link_id for i in remaining)


def test_confirmed_link_review_transitions_link_to_reviewed(cmd_client, cmd_seeded):
    """Confirmed reviews must make the link visible as reviewed knowledge."""
    link_id = _first_queue_item_id(cmd_client)
    assert (
        cmd_client.get("/api/v1/knowledge?review_state=reviewed").json()["items"]
        == []
    )

    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "contradicts",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text

    items = cmd_client.get("/api/v1/knowledge?review_state=reviewed").json()[
        "items"
    ]
    reviewed_links = [l for i in items for l in i["links"]]
    assert any(l["link_id"] == link_id for l in reviewed_links)
    # The derived-state filter must apply before the scan cap (limit=1 used
    # to hide reviewed links behind newer machine_generated rows).
    limited = cmd_client.get(
        "/api/v1/knowledge?review_state=reviewed&limit=1"
    ).json()["items"]
    assert any(
        l["link_id"] == link_id for i in limited for l in i["links"]
    )
    # Append-only ledger: role stays the AI proposal; the human decision is
    # carried by the latest review on the link.
    hit = next(l for l in reviewed_links if l["link_id"] == link_id)
    assert hit["review_state"] == "reviewed"
    assert hit["latest_review_outcome"] == "confirmed"
    assert hit["latest_reviewer"] == "reviewer-test"


def test_rejected_link_review_transitions_link_to_rejected(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "rejected",
            "relation": "evidence_gap",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text

    rejected = cmd_client.get("/api/v1/knowledge?review_state=rejected").json()[
        "items"
    ]
    assert any(
        l["link_id"] == link_id for i in rejected for l in i["links"]
    )
    reviewed = cmd_client.get("/api/v1/knowledge?review_state=reviewed").json()[
        "items"
    ]
    assert all(
        l["link_id"] != link_id for i in reviewed for l in i["links"]
    )


def test_confirmed_review_requires_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_confirmed_review_rejects_evidence_gap_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "evidence_gap",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 422


def test_rejected_review_needs_no_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "rejected",
            "factor_role": "不适用",
            "scope_boundary": "不适用",
            "reason": "AI 误把公司整体口径当作分部证据",
            "reviewer": "r",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["review"]["relation"] is None


def test_review_missing_link_is_404(cmd_client, cmd_seeded):
    response = cmd_client.post(
        "/api/v1/evidence-links/00000000-0000-0000-0000-000000000000/reviews",
        json={
            "outcome": "rejected",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


# ---------------------------------------------------------------------------
# 评估级审核: POST /api/v1/assessments/{id}/reviews
# ---------------------------------------------------------------------------


def test_assessment_review_roundtrip(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment
    from app.repositories.operational import TaskRepository

    assessment = cmd_seeded.scalar(select(AIAssessment))
    task = TaskRepository(cmd_seeded).add_task(
        title="Review assessment",
        task_type="review_assessment",
        status="in_progress",
        ref_type="ai_assessment",
        ref_id=assessment.id,
        research_case_id=cmd_seeded.scalar(select(Thesis.research_case_id)),
    )
    cmd_seeded.commit()
    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "confirmed"
    cmd_seeded.refresh(task)
    assert task.status == "done"

    # Idempotent: re-closing a done task (or a missing one) is a no-op.
    task_repo = TaskRepository(cmd_seeded)
    closed = task_repo.close_review_task(
        "review_assessment", "ai_assessment", assessment.id
    )
    assert closed is not None and closed.status == "done"
    assert (
        task_repo.close_review_task(
            "review_assessment", "ai_assessment", uuid.uuid4()
        )
        is None
    )


def test_assessment_review_missing_is_404(cmd_client, cmd_seeded):
    response = cmd_client.post(
        "/api/v1/assessments/00000000-0000-0000-0000-000000000000/reviews",
        json={"outcome": "confirmed", "reason": "x", "reviewer": "r"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_strict_single_metric_assessment_review_rejects_directional_conclusion(
    cmd_client, cmd_seeded
):
    from app.models.ledger import ResearchCase
    from app.repositories.research import ResearchRepository
    from app.services.assessment import AssessmentService
    from app.services.research import ResearchService
    from tests.protocol_provenance import seed_protocol_footprint

    case = cmd_seeded.scalar(select(ResearchCase))
    assert case is not None
    repo = ResearchRepository(cmd_seeded)
    thesis = ResearchService(repo).add_thesis(
        case.id,
        statement="Only one primary metric is available",
        created_by="tester",
        research_protocol_required=True,
    )
    service = AssessmentService(repo, cmd_seeded)
    snapshot = service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )
    footprint = seed_protocol_footprint(cmd_seeded, thesis)
    assessment = service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Protocol limits the conclusion.",
        gaps=["insufficient_primary_metrics"],
        research_protocol_status="single_metric_monitoring",
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=[rule.id for rule in footprint.rules],
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "rejected",
            "conclusion": "contradicted",
            "reason": "attempted override",
            "reviewer": "human:researcher",
        },
    )

    assert response.status_code == 422, response.text
    assert _error_code(response) == "validation_failed"

    # The rejected+directional decision was not persisted: the read model,
    # which consumes review.conclusion regardless of outcome, remains safely
    # non-directional.
    from app.queries.basis import HistoricalBasis
    from app.queries.conclusion import ConclusionQueries

    cmd_seeded.expire_all()
    conclusion = ConclusionQueries(cmd_seeded).load(
        case_id=case.id,
        basis=HistoricalBasis.from_cutoff(
            datetime(2027, 1, 1, tzinfo=timezone.utc)
        ),
    )
    assert conclusion.header.conclusion_status == "insufficient_evidence"


def test_assessment_review_closes_open_task(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment
    from app.repositories.operational import TaskRepository

    assessment = cmd_seeded.scalar(select(AIAssessment))
    task = TaskRepository(cmd_seeded).add_task(
        title="Confirm provisional assessment",
        task_type="review_assessment",
        status="open",
        ref_type="ai_assessment",
        ref_id=assessment.id,
        research_case_id=cmd_seeded.scalar(select(Thesis.research_case_id)),
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text
    cmd_seeded.refresh(task)
    assert task.status == "done"


def test_assessment_review_completes_final_run_gate(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment, ResearchCase
    from app.models.operational import ResearchRun, ResearchTask
    from app.models.research_monitor import ResearchRunEvent
    from app.repositories.operational import TaskRepository

    now = datetime.now(timezone.utc)
    case = cmd_seeded.scalar(select(ResearchCase))
    assessment = cmd_seeded.scalar(select(AIAssessment))
    assert case is not None and assessment is not None
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=now,
        updated_at=now,
    )
    cmd_seeded.add(run)
    cmd_seeded.flush()
    cmd_seeded.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=case.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="生成临时评估",
            result={"assessment_id": str(assessment.id)},
            created_at=now,
            updated_at=now,
        )
    )
    TaskRepository(cmd_seeded).add_task(
        title="确认临时 AI 评估",
        task_type="review_assessment",
        ref_type="ai_assessment",
        ref_id=assessment.id,
        research_case_id=case.id,
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认资料不足",
            "reviewer": "human:researcher",
        },
    )

    assert response.status_code == 201, response.text
    cmd_seeded.refresh(run)
    assert run.status == "succeeded"
    assert run.stage == "complete"
    assert run.stop_reason == "max_rounds_reached"
    assert any(
        event.stage == "review_complete"
        and event.payload_json["trigger_ref"] == f"assessment:{assessment.id}"
        for event in cmd_seeded.scalars(
            select(ResearchRunEvent).where(ResearchRunEvent.run_id == run.id)
        )
    )


def test_assessment_review_keeps_run_waiting_when_other_review_remains(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment, ResearchCase
    from app.models.operational import ResearchRun, ResearchTask
    from app.repositories.operational import TaskRepository

    now = datetime.now(timezone.utc)
    case = cmd_seeded.scalar(select(ResearchCase))
    first_assessment = cmd_seeded.scalar(select(AIAssessment))
    assert case is not None and first_assessment is not None
    second_assessment = AIAssessment(
        snapshot_id=first_assessment.snapshot_id,
        conclusion="insufficient_evidence",
        rationale="仍缺少第二项资料",
        gaps=["补充第二项资料"],
        created_at=now,
    )
    cmd_seeded.add(second_assessment)
    cmd_seeded.flush()
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=now,
        updated_at=now,
    )
    cmd_seeded.add(run)
    cmd_seeded.flush()
    for assessment in (first_assessment, second_assessment):
        cmd_seeded.add(
            ResearchTask(
                run_id=run.id,
                research_case_id=case.id,
                status="done",
                stage="completed",
                round=1,
                task_type="result",
                query="生成临时评估",
                result={"assessment_id": str(assessment.id)},
                created_at=now,
                updated_at=now,
            )
        )
        TaskRepository(cmd_seeded).add_task(
            title="确认临时 AI 评估",
            task_type="review_assessment",
            ref_type="ai_assessment",
            ref_id=assessment.id,
            research_case_id=case.id,
        )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/assessments/{first_assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": first_assessment.conclusion,
            "reason": "只确认第一项评估",
            "reviewer": "human:researcher",
        },
    )

    assert response.status_code == 201, response.text
    cmd_seeded.refresh(run)
    assert run.status == "waiting_for_review"
