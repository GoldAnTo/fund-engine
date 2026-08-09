from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.ledger import CaseDocumentVersion, DocumentVersion, ResearchCase
from app.models.event_research import EventResearchBrief
from app.models.operational import EventResearchLifecycle, ResearchRun


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _event_payload() -> dict[str, object]:
    return {
        "raw_input": "某公司披露新的订单节奏，后续收入兑现仍需要核验。",
        "source_type": "pasted_snapshot",
        "source_metadata": {"original_url": "https://example.test/tenant-event"},
        "event_title": "订单节奏更新",
        "company_name": "示例公司",
        "ticker": "000001",
        "research_question": "订单更新是否会改变收入兑现预期？",
        "candidate_factors": ["订单确认节奏", "产能交付能力", "客户需求持续性"],
        "created_by": "human:researcher",
    }


def test_event_routes_reject_missing_or_unknown_tenant_credentials(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"token-a":"team-a"}')

    missing = cmd_client.get("/api/v1/event-research", headers={"Authorization": ""})
    unknown = cmd_client.get(
        "/api/v1/event-research", headers={"Authorization": "Bearer unknown"}
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "permission_denied"


def test_research_session_exposes_only_the_authenticated_tenant_and_roles(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"admin-token":{"tenant_id":"team-a","roles":["case_administrator"]}}',
    )
    response = cmd_client.get("/api/v1/research-session", headers=_auth("admin-token"))
    assert response.status_code == 200
    assert response.json() == {"tenant_id": "team-a", "roles": ["case_administrator"]}


def test_foreign_tenant_cannot_read_or_attach_to_an_event_case(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}'
    )
    created = cmd_client.post(
        "/api/v1/event-research", json=_event_payload(), headers=_auth("token-a")
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    owner_list = cmd_client.get("/api/v1/event-research", headers=_auth("token-a"))
    foreign_list = cmd_client.get("/api/v1/event-research", headers=_auth("token-b"))
    assert [item["case_id"] for item in owner_list.json()["items"]] == [case_id]
    assert foreign_list.json()["items"] == []

    foreign_read = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workbench", headers=_auth("token-b")
    )
    foreign_write = cmd_client.post(
        f"/api/v1/event-research/{case_id}/materials",
        headers=_auth("token-b"),
        json={
            "raw_input": "外部团队不应写入的材料。",
            "source_type": "pasted_snapshot",
            "source_metadata": {},
            "actor": "human:foreign",
        },
    )

    assert foreign_read.status_code == 404
    assert foreign_write.status_code == 404


def test_event_case_documents_are_not_visible_to_a_foreign_tenant(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}'
    )
    created = cmd_client.post(
        "/api/v1/event-research", json=_event_payload(), headers=_auth("token-a")
    )
    case_id = created.json()["case_id"]
    legacy_list = cmd_client.get(f"/api/v1/documents?case_id={case_id}")
    document_id = legacy_list.json()["items"][0]["id"]

    owner_list = cmd_client.get(
        f"/api/v1/event-research/{case_id}/documents", headers=_auth("token-a")
    )
    foreign_list = cmd_client.get(
        f"/api/v1/event-research/{case_id}/documents", headers=_auth("token-b")
    )
    owner_detail = cmd_client.get(
        f"/api/v1/event-research/{case_id}/documents/{document_id}",
        headers=_auth("token-a"),
    )
    foreign_detail = cmd_client.get(
        f"/api/v1/event-research/{case_id}/documents/{document_id}",
        headers=_auth("token-b"),
    )

    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()["items"]] == [document_id]
    assert foreign_list.status_code == 404
    assert owner_detail.status_code == 200
    assert owner_detail.json()["document"]["id"] == document_id
    assert foreign_detail.status_code == 404


def test_research_runs_and_monitoring_never_cross_the_case_tenant_boundary(
    cmd_client, cmd_session, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}'
    )
    created = cmd_client.post(
        "/api/v1/event-research", json=_event_payload(), headers=_auth("token-a")
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        research_case_id=uuid.UUID(case_id),
        status="queued",
        stage="planning",
        round=0,
        max_rounds=1,
        budget=1,
        budget_used=0,
        scope_thesis_ids=[],
        monitor_version_id=None,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(run)
    cmd_session.commit()
    run_id = str(run.id)

    owner_active = cmd_client.get("/api/v1/research-runs/active", headers=_auth("token-a"))
    foreign_active = cmd_client.get("/api/v1/research-runs/active", headers=_auth("token-b"))
    foreign_detail = cmd_client.get(
        f"/api/v1/research-runs/{run_id}", headers=_auth("token-b")
    )
    foreign_events = cmd_client.get(
        f"/api/v1/research-runs/{run_id}/events", headers=_auth("token-b")
    )
    foreign_monitor = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/monitor", headers=_auth("token-b")
    )

    assert [item["run_id"] for item in owner_active.json()["items"]] == [run_id]
    assert foreign_active.json()["items"] == []
    assert foreign_detail.status_code == 404
    assert foreign_events.status_code == 404
    assert foreign_monitor.status_code == 404


def test_market_expression_side_pages_cannot_bypass_case_tenant_admission(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}'
    )
    created = cmd_client.post(
        "/api/v1/event-research", json=_event_payload(), headers=_auth("token-a")
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    foreign_expression = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/market-expression", headers=_auth("token-b")
    )
    foreign_sources = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/source-statements", headers=_auth("token-b")
    )
    owner_expression = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/market-expression", headers=_auth("token-a")
    )

    assert foreign_expression.status_code == 404
    assert foreign_sources.status_code == 404
    assert owner_expression.status_code == 200


def test_legacy_case_requires_explicit_admin_admission_before_it_is_visible(
    cmd_client, cmd_session, monkeypatch
) -> None:
    now = datetime.now(timezone.utc)
    legacy_case = ResearchCase(
        title="迁移前的事件研究",
        industry_topic="事件研究",
        created_by="legacy-import",
        created_at=now,
    )
    legacy_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://example.test/legacy-source",
        available_at=now,
        acquired_at=now,
        parser_version="legacy",
    )
    cmd_session.add_all([legacy_case, legacy_document])
    cmd_session.flush()
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=legacy_case.id,
            document_version_id=legacy_document.id,
            linked_at=now,
        )
    )
    cmd_session.add_all(
        [
            EventResearchBrief(
                research_case_id=legacy_case.id,
                raw_input="迁移前的事件原始材料",
                source_url=legacy_document.source_url,
                source_type="pasted_snapshot",
                source_metadata={},
                event_title=legacy_case.title,
                company_name=None,
                ticker=None,
                event_at=None,
                market_reaction=None,
                research_question="历史事件仍需由证据验证",
                extraction_state="confirmed",
                created_at=now,
            ),
            EventResearchLifecycle(
                research_case_id=legacy_case.id,
                status="awaiting_scope",
                active_run_id=None,
                current_round=0,
                status_summary="历史 Case 等待继续研究",
                current_gap=None,
                next_human_action="补充证据",
                updated_at=now,
            ),
        ]
    )
    cmd_session.commit()

    invisible = cmd_client.get("/api/v1/event-research")
    no_role = cmd_client.post(
        f"/api/v1/event-research/{legacy_case.id}/tenant-admission",
        json={
            "tenant_id": "test-team",
            "initial_document_version_id": str(legacy_document.id),
            "admitted_by": "human:ops",
            "reason": "迁移清单与原始材料归属已人工核验",
        },
    )
    assert invisible.json()["items"] == []
    assert no_role.status_code == 403

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":{"tenant_id":"test-team","roles":["case_administrator"]}}',
    )
    queue = cmd_client.get("/api/v1/event-research/legacy-admission-queue")
    assert queue.status_code == 200, queue.text
    assert len(queue.json()["items"]) == 1
    queued = queue.json()["items"][0]
    assert queued["case_id"] == str(legacy_case.id)
    assert queued["event_title"] == legacy_case.title
    assert queued["documents"] == [
        {
            "document_version_id": str(legacy_document.id),
            "title": None,
            "source_url": legacy_document.source_url,
            "available_at": queued["documents"][0]["available_at"],
        }
    ]
    admitted = cmd_client.post(
        f"/api/v1/event-research/{legacy_case.id}/tenant-admission",
        json={
            "tenant_id": "test-team",
            "initial_document_version_id": str(legacy_document.id),
            "admitted_by": "human:ops",
            "reason": "迁移清单与原始材料归属已人工核验",
        },
    )
    assert admitted.status_code == 201, admitted.text
    assert admitted.json()["reason"] == "迁移清单与原始材料归属已人工核验"

    visible = cmd_client.get("/api/v1/event-research")
    assert [item["case_id"] for item in visible.json()["items"]] == [str(legacy_case.id)]
