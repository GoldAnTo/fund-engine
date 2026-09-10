"""Command-side v1 API tests (prototype 新建研究 / 审核工作区).

Uses the private-engine ``cmd_*`` fixtures from conftest: command endpoints
COMMIT, so they never share the session-scoped engine.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select


def _seed_foreign_proposal(cmd_session):
    import hashlib

    from app.models.ledger import (
        CaseDocumentVersion,
        CaseTenantAdmission,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        SourceStatement,
        Thesis,
    )
    from app.models.proposals import Proposal
    from app.models.identity import CaseAccessGrant, ResearchUser

    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="foreign review queue case",
        industry_topic="tenant-isolation",
        created_by="foreign-user",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="foreign tenant factor",
        created_by="foreign-user",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"foreign review source").hexdigest(),
        source_url="https://foreign.example.test/private-source",
        available_at=now,
        acquired_at=now,
        parser_version="test-v1",
        parse_state="success",
    )
    cmd_session.add_all([thesis, document])
    cmd_session.flush()
    cmd_session.add_all(
        [
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=document.id,
                linked_at=now,
            ),
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id="other-team",
                initial_document_version_id=document.id,
                admitted_by="test-fixture",
                admitted_at=now,
            ),
        ]
    )
    user_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "test-research-principal:other-team:foreign-user",
    )
    cmd_session.add(
        ResearchUser(
            id=user_id,
            issuer="https://test-identity.invalid/realms/research",
            subject="foreign-user",
            tenant_id="other-team",
            display_name="foreign-user",
            normalized_email=None,
            active=True,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    cmd_session.flush()
    cmd_session.add(
        CaseAccessGrant(
            research_case_id=case.id,
            user_id=user_id,
            role="owner",
            granted_by_principal_id="test:fixture",
            reason="foreign authorization fixture",
            created_at=now,
            updated_at=now,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="FOREIGN PRIVATE VERBATIM",
        locator={"page": 7},
    )
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="foreign private statement",
        created_at=now,
    )
    cmd_session.add(statement)
    cmd_session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": "foreign private reason",
            "scope": {},
        },
        target_context={"thesis_id": str(thesis.id)},
        proposed_by_type="ai",
        proposed_by_ref="foreign-worker",
        proposed_at=now,
        research_case_id=case.id,
        status="pending",
    )
    cmd_session.add(proposal)
    cmd_session.commit()
    return case, proposal


def _error_code(response) -> str:
    return response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# 新建研究: POST /api/v1/research-cases
# ---------------------------------------------------------------------------


def test_create_case_with_framing_and_initial_theses(cmd_client, cmd_session):
    from app.models.ledger import ResearchCase, Thesis

    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "AI 算力产业链",
            "industry_topic": "ai_compute",
            "research_object": "从云厂商资本开支到芯片收入的传导",
            "phenomenon": "AI 资本开支持续扩张但订单收入确认节奏分化",
            "core_question": "截至 2026-06-30 算力资本开支能否通过已披露订单验证？",
            "period_start": "2026-01-01",
            "period_end": "2027-12-31",
            "evidence_cutoff": "2026-06-30",
            "initial_theses": [
                {
                    "statement": "云厂商资本开支形成持续算力需求",
                    "title": "命题 1",
                    "observation_start": "2026-01-01",
                    "observation_end": "2027-12-31",
                    "support_condition": "至少两家主要云厂商给出资本开支扩张指引",
                    "falsification_condition": "主要云厂商下调资本开支",
                    "next_verification_event": "核对 2026Q2 云厂商财报",
                },
                {
                    "statement": "第二条人工命题",
                },
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["theses"][0]["review_state"] == "confirmed"
    assert body["theses"][1]["review_state"] == "confirmed"

    case = cmd_session.scalar(select(ResearchCase))
    assert case.core_question.startswith("截至 2026-06-30")
    assert str(case.evidence_cutoff) == "2026-06-30"

    theses = cmd_session.scalars(select(Thesis)).all()
    assert len(theses) == 2
    assert theses[0].falsification_condition == "主要云厂商下调资本开支"
    assert theses[1].creator_type == "human"


def test_create_case_rejects_inverted_period(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "x",
            "industry_topic": "t",
            "period_start": "2027-01-01",
            "period_end": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_add_thesis_to_missing_case_is_422(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/theses",
        json={"statement": "x"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_add_thesis_rejects_inverted_observation_window(cmd_client, cmd_session):
    created = cmd_client.post(
        "/api/v1/research-cases",
        json={"title": "c", "industry_topic": "t"},
    )
    case_id = created.json()["case_id"]

    response = cmd_client.post(
        f"/api/v1/research-cases/{case_id}/theses",
        json={
            "statement": "s",
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


def test_review_queue_requires_authentication(cmd_client):
    response = cmd_client.get(
        "/api/v1/review-queue",
        headers={"Authorization": ""},
    )

    assert response.status_code == 401


def test_global_review_queue_filters_proposals_and_legacy_links_by_tenant(
    cmd_client, cmd_seeded, monkeypatch
):
    foreign_case, foreign_proposal = _seed_foreign_proposal(cmd_seeded)

    owner_response = cmd_client.get("/api/v1/review-queue")
    assert owner_response.status_code == 200, owner_response.text
    owner_payload = owner_response.text
    assert str(foreign_proposal.id) not in owner_payload
    assert "FOREIGN PRIVATE VERBATIM" not in owner_payload
    assert "https://foreign.example.test/private-source" not in owner_payload

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"other-token":{"tenant_id":"other-team","actor_id":"foreign-user"}}',
    )
    foreign_response = cmd_client.get(
        "/api/v1/review-queue",
        headers={"Authorization": "Bearer other-token"},
    )
    assert foreign_response.status_code == 200, foreign_response.text
    foreign_items = foreign_response.json()["items"]
    assert [item["link_id"] for item in foreign_items] == [str(foreign_proposal.id)]
    assert foreign_items[0]["case_id"] == str(foreign_case.id)
    assert foreign_items[0]["verbatim_text"] == "FOREIGN PRIVATE VERBATIM"


def test_case_review_queue_hides_a_foreign_case(
    cmd_client, cmd_seeded, monkeypatch
):
    from app.models.ledger import ResearchCase

    case_id = cmd_seeded.scalar(select(ResearchCase.id))
    assert case_id is not None
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"other-token":{"tenant_id":"other-team","actor_id":"intruder"}}',
    )

    response = cmd_client.get(
        "/api/v1/review-queue",
        params={"case_id": str(case_id)},
        headers={"Authorization": "Bearer other-token"},
    )

    assert response.status_code == 404
    assert "document_source_url" not in response.text
    assert "verbatim_text" not in response.text


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
        },
    )
    assert response.status_code == 201, response.text
    review = response.json()["review"]
    assert review["outcome"] == "confirmed"
    assert review["relation"] == "supports"
    assert review["reviewer"] == "user:test-team"

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
        },
    )
    assert response.status_code == 201, response.text

    items = cmd_client.get("/api/v1/knowledge?review_state=reviewed").json()[
        "items"
    ]
    reviewed_links = [link for item in items for link in item["links"]]
    assert any(link["link_id"] == link_id for link in reviewed_links)
    # The derived-state filter must apply before the scan cap (limit=1 used
    # to hide reviewed links behind newer machine_generated rows).
    limited = cmd_client.get(
        "/api/v1/knowledge?review_state=reviewed&limit=1"
    ).json()["items"]
    assert any(
        link["link_id"] == link_id
        for item in limited
        for link in item["links"]
    )
    # Append-only ledger: role stays the AI proposal; the human decision is
    # carried by the latest review on the link.
    hit = next(link for link in reviewed_links if link["link_id"] == link_id)
    assert hit["review_state"] == "reviewed"
    assert hit["latest_review_outcome"] == "confirmed"
    assert hit["latest_reviewer"] == "user:test-team"


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
        },
    )
    assert response.status_code == 201, response.text

    rejected = cmd_client.get("/api/v1/knowledge?review_state=rejected").json()[
        "items"
    ]
    assert any(
        link["link_id"] == link_id
        for item in rejected
        for link in item["links"]
    )
    reviewed = cmd_client.get("/api/v1/knowledge?review_state=reviewed").json()[
        "items"
    ]
    assert all(
        link["link_id"] != link_id
        for item in reviewed
        for link in item["links"]
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
        },
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_link_review_rejects_a_different_tenant(cmd_client, cmd_seeded, monkeypatch):
    link_id = _first_queue_item_id(cmd_client)
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"other-token":{"tenant_id":"other-team","actor_id":"intruder"}}',
    )

    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        headers={"Authorization": "Bearer other-token"},
        json={
            "outcome": "rejected",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
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
    )
    cmd_seeded.commit()
    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "confirmed"
    assert response.json()["reviewer"] == "user:test-team"
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
        json={"outcome": "confirmed", "reason": "x"},
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
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认",
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
        },
    )

    assert response.status_code == 201, response.text
    cmd_seeded.refresh(run)
    assert run.status == "waiting_for_review"
