from __future__ import annotations

import uuid
import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from app.models.acquisition import (
    AcquisitionException,
    AcquisitionAttempt,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseTenantAdmission,
    EvidenceLink,
    ResearchCase,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, Job
from app.models.operational import ResearchWorkerHeartbeat
from app.models.research_monitor import CaseMonitorVersion
from app.models.research_orchestration import (
    AcquisitionGoalCoverage,
    AcquisitionQueryPlan,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.domain.research_workflow import classify_user_decision
from app.models.acquisition import AcquisitionJob
from app.services.research_orchestration import (
    AcquisitionProgressSnapshot,
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
from app.services.acquisition import AcquisitionModule
from app.services.auto_research import AutoResearchService
from app.repositories.research_orchestration import ResearchOrchestrationRepository


EXPECTED_STAGES = [
    ("event_intake", "建立事件"),
    ("scope_confirmation", "确认命题"),
    ("source_acquisition", "主动补证"),
    ("evidence_synthesis", "证据归并"),
    ("thesis_adjudication", "命题判定"),
    ("report_monitoring", "报告与监测"),
]


def _event_payload() -> dict:
    return {
        "raw_input": "公司上调资本开支指引，市场重新评估需求。",
        "event_title": "资本开支指引更新",
        "research_question": "资本开支上调能否被后续经营数据验证？",
        "candidate_factors": ["订单增长", "收入兑现", "替代解释"],
    }


def _create_case(cmd_client, cmd_session) -> tuple[uuid.UUID, EventResearchScopeVersion]:
    response = cmd_client.post("/api/v1/event-research", json=_event_payload())
    assert response.status_code == 201, response.text
    case_id = uuid.UUID(response.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    return case_id, scope


def _confirm(cmd_client, case_id: uuid.UUID, scope_id: uuid.UUID) -> dict:
    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope_id),
            "idempotency_key": f"workflow-api:{scope_id}",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_workflow_projection_is_server_owned_and_has_six_stable_stages(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event"] == {
        "case_id": str(case_id),
        "title": "资本开支指引更新",
        "research_question": "资本开支上调能否被后续经营数据验证？",
    }
    assert body["scope"]["id"] == str(scope.id)
    assert body["scope"]["version"] == 1
    assert [
        (item["code"], item["display_name"]) for item in body["stages"]
    ] == EXPECTED_STAGES
    assert [item["status"] for item in body["stages"]] == [
        "completed",
        "completed",
        "active",
        "pending",
        "pending",
        "pending",
    ]
    assert body["state"] == "planning_acquisition"
    assert body["system_action"]["label"] == confirmed["current_system_action"]
    assert body["system_action"]["reason"] == confirmed["system_action_reason"]
    assert body["user_action"] is None
    assert body["user_action_summary"] == "当前无需操作"
    assert body["acquisition"]["series_count"] == 0
    assert body["source_ledger"]["counts"] == {
        "total": 0,
        "reviewed": 0,
        "automatically_admitted": 0,
        "deduplicated": 0,
        "quarantined": 0,
        "by_status": {
            "search_candidate": 0,
            "fetching": 0,
            "frozen": 0,
            "deduplicated": 0,
            "conflicted": 0,
            "admitted": 0,
            "quarantined": 0,
            "skipped": 0,
        },
    }
    assert body["coverage"] == []
    assert body["conclusion"] is None
    assert body["monitor"] is None
    assert body["events"]
    assert len(body["events"]) <= 12


def test_workflow_missing_returns_typed_error_with_safe_initialize_action(
    cmd_client, cmd_session
):
    case_id, _scope = _create_case(cmd_client, cmd_session)
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status_summary = "旧 Case 显示正在顺利运行"
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "workflow_not_initialized"
    safe_action = error["details"]["safe_action"]
    assert safe_action == {
        "kind": "initialize_workflow",
        "label": "确认命题并开始研究",
        "method": "POST",
        "href": f"/api/v1/event-research/{case_id}/workflow/confirm",
        "payload": {
            "scope_version_id": str(_scope.id),
            "scope_version": _scope.version,
            "idempotency_key": f"workflow:initialize:{_scope.id}:v{_scope.version}",
        },
    }
    assert "旧 Case 显示" not in response.text

    initialized = cmd_client.request(
        safe_action["method"],
        safe_action["href"],
        json=safe_action["payload"],
    )
    assert initialized.status_code == 202, initialized.text
    assert initialized.json()["scope_version_id"] == str(_scope.id)
    assert cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    ).status_code == 200


def test_workflow_missing_scope_returns_distinct_safe_action_without_fake_id(
    cmd_client, cmd_session
):
    existing_case_id, _scope = _create_case(cmd_client, cmd_session)
    admission = cmd_session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == existing_case_id
        )
    )
    assert admission is not None
    now = datetime.now(UTC)
    research_case = ResearchCase(
        title="尚未建立范围的事件",
        industry_topic="event-research",
        created_at=now,
        created_by="tenant:test-team",
    )
    cmd_session.add(research_case)
    cmd_session.flush()
    cmd_session.add_all(
        [
            CaseTenantAdmission(
                research_case_id=research_case.id,
                tenant_id="test-team",
                initial_document_version_id=admission.initial_document_version_id,
                admitted_by="tenant:test-team",
                admitted_at=now,
            ),
            EventResearchBrief(
                research_case_id=research_case.id,
                raw_input="待确认事件",
                source_type="pasted_snapshot",
                event_title="尚未建立范围的事件",
                research_question="下一步研究什么？",
                extraction_state="confirmed",
                created_at=now,
            ),
        ]
    )
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/event-research/{research_case.id}/workflow"
    )
    assert response.status_code == 409, response.text
    action = response.json()["error"]["details"]["safe_action"]
    assert action == {
        "kind": "establish_scope",
        "label": "建立并确认研究范围",
        "method": "GET",
        "href": f"/api/v1/event-research/{research_case.id}/workbench",
        "payload": {},
    }
    assert "scope_version_id" not in response.text


def test_workflow_requires_authentication_and_hides_cross_tenant_case(
    cmd_client, cmd_session, monkeypatch
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)

    unauthenticated = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow",
        headers={"Authorization": ""},
    )
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}',
    )
    foreign = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow",
        headers={"Authorization": "Bearer foreign-token"},
    )

    assert unauthenticated.status_code == 401
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"


def test_workflow_events_use_stable_case_isolated_cursor_pagination(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration_id = uuid.UUID(confirmed["orchestration_id"])
    now = datetime.now(UTC)
    current_max = int(
        cmd_session.scalar(
            select(ResearchOrchestrationEvent.sequence)
            .where(ResearchOrchestrationEvent.orchestration_id == orchestration_id)
            .order_by(ResearchOrchestrationEvent.sequence.desc())
            .limit(1)
        )
        or 0
    )
    for offset in range(1, 16):
        cmd_session.add(
            ResearchOrchestrationEvent(
                orchestration_id=orchestration_id,
                sequence=current_max + offset,
                transition=f"test_transition_{offset:02d}",
                actor="system:test",
                message=f"过程事件 {offset:02d}",
                payload_json={"offset": offset},
                idempotency_key=f"workflow-page:{offset}",
                created_at=now + timedelta(seconds=offset),
            )
        )
    cmd_session.commit()

    first = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events?limit=5"
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    second = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events",
        params={"limit": 5, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    sequences = [item["sequence"] for item in first_body["items"] + second.json()["items"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences)) == 10

    projection = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
    assert len(projection["events"]) == 12
    assert projection["events"][0]["sequence"] > projection["events"][-1]["sequence"]


def test_event_cursor_continues_after_an_exact_page_boundary_and_concurrent_append(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration_id = uuid.UUID(confirmed["orchestration_id"])
    first = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events",
        params={"limit": 1},
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 1
    assert page["has_more"] is False
    assert page["next_cursor"] == page["items"][-1]["sequence"]

    appended_sequence = page["next_cursor"] + 1
    cmd_session.add(
        ResearchOrchestrationEvent(
            orchestration_id=orchestration_id,
            sequence=appended_sequence,
            transition="concurrent_append",
            actor="system:test",
            message="首个页面读取后追加",
            payload_json={},
            idempotency_key="concurrent-append-after-exact-page",
            created_at=datetime(2030, 1, 2, 3, 4, 5),
        )
    )
    cmd_session.commit()

    continued = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events",
        params={"limit": 1, "after": page["next_cursor"]},
    )
    legacy = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events",
        params={"limit": 1, "cursor": page["next_cursor"]},
    )
    ambiguous = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events",
        params={
            "limit": 1,
            "after": page["next_cursor"],
            "cursor": page["next_cursor"],
        },
    )

    assert continued.status_code == 200, continued.text
    assert [item["sequence"] for item in continued.json()["items"]] == [
        appended_sequence
    ]
    assert continued.json()["next_cursor"] == appended_sequence
    assert continued.json()["has_more"] is False
    assert legacy.json() == continued.json()
    assert ambiguous.status_code == 422


def test_stale_heartbeat_and_retry_wait_are_reported_truthfully(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    stale_at = datetime.now(UTC) - timedelta(minutes=30)
    retry_at = datetime.now(UTC) + timedelta(minutes=5)
    orchestration.state = "retry_wait"
    orchestration.user_stage = "evidence_synthesis"
    orchestration.last_heartbeat_at = stale_at
    orchestration.recovery_status = "stale"
    orchestration.checkpoint_json = {
        **orchestration.checkpoint_json,
        "next_retry_at": retry_at.isoformat(),
        "recovery": {"reason": "worker_lease_expired", "attempt": 2},
    }
    job = cmd_session.scalar(
        select(Job).where(
            Job.kind == "research_run",
            Job.target_id == orchestration.current_research_run_id,
        )
    )
    if job is not None:
        job.status = "retry_wait"
        job.next_retry_at = retry_at
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()

    assert body["state"] == "retry_wait"
    assert body["stages"][3]["status"] == "recovering"
    assert body["system_action"]["recovery_status"] == "stale"
    assert body["system_action"]["heartbeat_at"] == stale_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert body["system_action"]["retry_at"] == retry_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert body["recovery"]["status"] == "stale"
    assert body["recovery"]["reason"] == "worker_lease_expired"
    assert body["user_action"] is None


def test_expired_running_heartbeat_is_projected_as_recovering_without_lifecycle_guess(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert orchestration is not None and lifecycle is not None
    orchestration.state = "acquiring"
    orchestration.user_stage = "acquisition"
    orchestration.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    orchestration.recovery_status = "healthy"
    lifecycle.status_summary = "旧投影声称 worker 正常运行"
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()

    assert body["state"] == "recovering"
    assert body["stages"][2]["status"] == "recovering"
    assert body["system_action"]["recovery_status"] == "stale"
    assert body["recovery"]["reason"] == "orchestration_heartbeat_stale"
    assert "旧投影声称" not in str(body)


def test_canonical_worker_heartbeat_threshold_keeps_three_minutes_healthy(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    observed_at = datetime.now(UTC)
    orchestration.state = "acquiring"
    orchestration.user_stage = "acquisition"
    orchestration.last_heartbeat_at = observed_at - timedelta(minutes=3)
    cmd_session.add(
            ResearchWorkerHeartbeat(
                worker_id="research-worker:workflow-threshold-worker",
            mode="loop",
            state="running",
            started_at=observed_at - timedelta(hours=1),
            last_seen_at=observed_at - timedelta(minutes=3),
        )
    )
    cmd_session.commit()

    healthy = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    ).json()
    assert healthy["state"] == "acquiring"
    assert healthy["recovery"]["status"] != "stale"

    orchestration.last_heartbeat_at = observed_at - timedelta(minutes=6)
    worker = cmd_session.get(
        ResearchWorkerHeartbeat,
        "research-worker:workflow-threshold-worker",
    )
    assert worker is not None
    worker.last_seen_at = observed_at - timedelta(minutes=6)
    cmd_session.commit()

    stale = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    ).json()
    assert stale["state"] == "recovering"
    assert stale["recovery"]["status"] == "stale"
    assert stale["recovery"]["reason"] == "worker_heartbeat_stale"
    assert stale["recovery"]["source"] == "worker"
    assert stale["recovery"]["worker_heartbeat_at"] == (
        worker.last_seen_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    )


def test_each_primary_internal_state_maps_to_one_server_owned_stage(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    cases = [
        ("planning_acquisition", "acquisition", 2, "active"),
        ("assessing_coverage", "acquisition", 2, "active"),
        ("synthesizing_evidence", "evidence_synthesis", 3, "active"),
        ("adjudicating_thesis", "thesis_adjudication", 4, "active"),
        ("generating_report", "report_monitoring", 5, "active"),
        ("monitoring", "report_monitoring", 5, "active"),
        ("recovering", "evidence_synthesis", 3, "recovering"),
        ("failed", "evidence_synthesis", 3, "failed"),
        ("cancelled", "acquisition", 2, "cancelled"),
    ]
    for state, user_stage, index, expected_status in cases:
        orchestration.state = state
        orchestration.user_stage = user_stage
        orchestration.last_heartbeat_at = None
        orchestration.recovery_status = "recovering" if state == "recovering" else None
        cmd_session.commit()
        body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
        assert body["state"] == state
        assert body["stages"][index]["status"] == expected_status
        assert sum(item["status"] in {"active", "recovering", "failed", "cancelled"} for item in body["stages"]) == 1


def test_needs_scope_decision_exposes_exactly_one_typed_action(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    run_id = orchestration.current_research_run_id
    assert run_id is not None
    goal_id = "support:orders"
    cmd_session.add(
        AcquisitionGoalCoverage(
            research_run_id=run_id,
            scope_version_id=scope.id,
            thesis_id=None,
            goal_id=goal_id,
            objective="验证订单增长",
            status="exhausted",
            required_authority_count=1,
            observed_authority_count=0,
            required_independent_source_count=2,
            observed_independent_source_count=1,
            contrary_search_completed=True,
            reason_codes_json=["authority_source_missing"],
            evidence_link_ids_json=[],
            independent_source_identities_json=["source:a"],
            conflict_details_json=[],
            unknown_details_json=["缺少权威来源"],
            evaluation_round=2,
            zero_new_independent_source_rounds=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    orchestration.state = "needs_scope_decision"
    orchestration.next_action_kind = "decide_scope_boundary"
    orchestration.next_action_label = "决定研究边界"
    orchestration.next_action_payload = {
        "action": "revise_frozen_research_boundary",
        "attempted_rounds": 2,
        "boundary_codes": ["source_policy"],
        "affected_goals": [
            {"goal_id": goal_id, "reason_codes": ["authority_requirement_unmet"]}
        ],
        "missing": [
            {"goal_id": goal_id, "reason_codes": ["authority_requirement_unmet"]}
        ],
        "attempted": [{"goal_id": goal_id, "search_completed": True}],
        "cannot_continue_reason": "继续补证需要改变已冻结的研究边界。",
        "recommendation": {
            "kind": "revise_scope",
            "label": "调整研究边界",
            "impact": "创建新范围版本后才能继续补证。",
        },
        "alternatives": [
            {
                "kind": "keep_scope",
                "label": "保持当前研究范围",
                "impact": "未解决目标继续保留为未知。",
            },
            {
                "kind": "stop",
                "label": "停止本次研究",
                "impact": "停止后续自动补证并保留记录。",
            },
        ],
    }
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()

    assert body["user_action"] == {
        "kind": "decide_scope_boundary",
        "label": "决定研究边界",
        "reason": "继续补证需要改变已冻结的研究边界。",
        "recommendation": orchestration.next_action_payload["recommendation"],
        "alternatives": orchestration.next_action_payload["alternatives"],
        "impact": "创建新范围版本后才能继续补证。",
        "payload": orchestration.next_action_payload,
    }
    assert body["user_action_summary"] == "决定研究边界"
    assert body["coverage"][0]["status"] == "exhausted"
    assert body["coverage"][0]["unknown"] == ["缺少权威来源"]


def test_legacy_protocol_decision_is_diagnostic_not_an_executable_user_action(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    legacy_payload = {
        "reason": "新增命题缺少可执行研究协议",
        "thesis": "订单增长能否持续",
        "blocked_thesis_ids": [str(uuid.uuid4())],
    }
    orchestration.state = "needs_scope_decision"
    orchestration.user_stage = "scope_confirmation"
    orchestration.current_system_action = "等待补全研究协议"
    orchestration.system_action_reason = legacy_payload["reason"]
    orchestration.next_action_kind = "complete_research_protocol"
    orchestration.next_action_label = "完成研究协议"
    orchestration.next_action_payload = legacy_payload
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "needs_scope_decision"
    assert body["recovery"]["status"] == "incomplete_decision_context"
    assert body["recovery"]["reason"] == "incomplete_decision_context"
    assert body["recovery"]["decision_diagnostic"] == legacy_payload
    assert body["system_action"]["recovery_status"] == (
        "incomplete_decision_context"
    )
    assert body["system_action"]["label"] == "正在恢复不完整的决策上下文"
    assert "服务端" in body["system_action"]["reason"]
    assert body["user_action"] is None
    assert body["user_action_summary"] == "当前无需操作"


def test_protocol_decision_classifier_rejects_almost_complete_context():
    thesis_id = uuid.uuid4()
    payload = {
        "action": "complete_research_protocol",
        "scope_version_id": str(uuid.uuid4()),
        "scope_version": True,
        "blocked_thesis_ids": [str(thesis_id)],
        "reason_codes": {str(thesis_id): ["missing_authoritative_source"]},
        "blocked_theses": [
            {
                "thesis_id": str(thesis_id),
                "reason_codes": ["missing_authoritative_source"],
            }
        ],
        "missing": [{"thesis_id": str(thesis_id), "items": ["primary_source"]}],
        "attempted": [{"thesis_id": str(thesis_id), "queries": ["filing"]}],
        "cannot_continue_reason": "缺少可验证的一手资料。",
        "recommendation": {
            "kind": "narrow_scope",
            "label": "缩小研究范围",
            "impact": "仅保留已有证据可验证的命题。",
        },
        "alternatives": [
            {
                "kind": "keep_unknown",
                "label": "保留未知结论",
                "impact": "报告会明确披露证据缺口。",
            },
            {
                "kind": "stop_research",
                "label": "停止研究",
                "impact": "不生成当前范围的最终结论。",
            },
        ],
    }

    assert classify_user_decision("complete_research_protocol", payload) is None

    payload["scope_version"] = 1
    payload["alternatives"] = payload["alternatives"][:1]
    assert classify_user_decision("complete_research_protocol", payload) is None


def test_projection_exposes_frozen_acquisition_rounds_without_replanning(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)

    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
    assert body["state"] == "acquiring"
    assert body["research_run_id"] == confirmed["research_run_id"]
    assert body["research_execution"] is None
    assert body["acquisition"]["series_count"] == 7
    assert len(body["acquisition"]["rounds"]) == 7
    assert {item["round"] for item in body["acquisition"]["rounds"]} == {1}
    persisted_plan_ids = {
        str(value)
        for value in cmd_session.scalars(select(AcquisitionQueryPlan.id))
    }
    assert {
        item["query_plan_id"] for item in body["acquisition"]["rounds"]
    } == persisted_plan_ids
    assert {item["planner_version"] for item in body["acquisition"]["rounds"]} == {
        "goal-query-v1"
    }
    assert all(
        item["ordered_query_count"] > 0
        for item in body["acquisition"]["rounds"]
    )
    assert all(
        item["frozen_inputs"]["scope_version_id"] == str(scope.id)
        for item in body["acquisition"]["rounds"]
    )


def test_projection_exposes_recoverable_research_execution(cmd_client, cmd_session):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    job = AutoResearchService(cmd_session).enqueue_prepared_run(
        uuid.UUID(confirmed["research_run_id"]),
        commit=False,
    )
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()

    assert body["research_execution"] == {
        "job_id": str(job.id),
        "status": "queued",
        "step": None,
        "attempt": 1,
        "failure_count": 0,
        "started_at": None,
        "finished_at": None,
        "recovery_count": 0,
        "last_recovered_at": None,
    }


def test_acquisition_round_reports_an_expired_lease_as_lost(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    job = cmd_session.scalar(
        select(AcquisitionJob)
        .where(AcquisitionJob.research_case_id == case_id)
        .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        .limit(1)
    )
    assert job is not None
    job.status = "running"
    job.stage = "fetching"
    job.lease_owner = "dead-worker"
    job.lease_token = "expired-token"
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    cmd_session.commit()

    rounds = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()[
        "acquisition"
    ]["rounds"]
    projected = next(item for item in rounds if item["job_id"] == str(job.id))
    assert projected["status"] == "running"
    assert projected["recovery_status"] == "lost_lease"
    projection = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    ).json()
    assert projection["state"] == "recovering"
    assert projection["stages"][2]["status"] == "recovering"
    assert projection["recovery"]["status"] == "recovering"
    assert projection["recovery"]["reason"] == "lost_lease"
    assert projection["recovery"]["source"] == "acquisition_job"
    expected_lease = job.lease_expires_at
    if expected_lease.tzinfo is None:
        expected_lease = expected_lease.replace(tzinfo=UTC)
    assert projection["system_action"]["lease_expires_at"] == (
        expected_lease.isoformat().replace("+00:00", "Z")
    )
    assert projection["recovery"]["lease_expires_at"] == (
        expected_lease.isoformat().replace("+00:00", "Z")
    )

    retry_at = datetime.now(UTC) + timedelta(minutes=7)
    job.status = "retry_wait"
    job.stage = "fetching"
    job.retry_at = retry_at
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires_at = None
    cmd_session.commit()
    waiting = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    ).json()
    assert waiting["state"] == "retry_wait"
    assert waiting["recovery"]["source"] == "acquisition_job"
    assert waiting["recovery"]["retry_at"] == retry_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert waiting["system_action"]["retry_at"] == waiting["recovery"][
        "retry_at"
    ]


def test_system_action_started_at_does_not_move_with_heartbeat(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    started_at = orchestration.state_started_at
    assert started_at is not None
    heartbeat_at = datetime.now(UTC) + timedelta(minutes=1)
    ResearchOrchestrationRepository(cmd_session).record_heartbeat(
        orchestration,
        orchestration.version,
        heartbeat_at,
    )
    cmd_session.commit()
    cmd_session.refresh(orchestration)
    assert orchestration.state_started_at == started_at

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
    assert body["system_action"]["started_at"] == started_at.replace(
        tzinfo=UTC
    ).isoformat().replace("+00:00", "Z")

    ResearchOrchestrationService(cmd_session).record_acquisition_progress(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
        AcquisitionProgressSnapshot(
            target_state="acquiring",
            user_stage="acquisition",
            action="正在获取资料",
            reason="获取计划已冻结",
            checkpoint=orchestration.checkpoint_json,
        ),
        "workflow-started-at-transition",
    )
    cmd_session.commit()
    cmd_session.refresh(orchestration)
    assert orchestration.state_started_at != started_at


def _seed_source_ledger(cmd_session, case_id, scope, run_id, *, now=None):
    thesis = cmd_session.scalar(
        select(Thesis)
        .where(Thesis.research_case_id == case_id)
        .order_by(Thesis.created_at, Thesis.id)
        .limit(1)
    )
    job = cmd_session.scalar(
        select(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run_id)
        .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        .limit(1)
    )
    assert thesis is not None and job is not None
    now = now or datetime.now(UTC)
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key="sse",
        operation="fetch",
        attempt_no=1,
        started_at=now,
        finished_at=now,
        outcome="succeeded",
        retryable=False,
        safe_metadata={},
    )
    reference = SourceReference(
        job_id=job.id,
        adapter_key="sse",
        external_record_id="announcement-1",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/announcement-1.pdf",
        title="交易所公告",
        published_at=now,
        source_role="company_disclosure",
        metadata_json={},
        created_at=now,
    )
    cmd_session.add_all([attempt, reference])
    cmd_session.flush()
    raw = b"frozen disclosure"
    digest = hashlib.sha256(raw).hexdigest()
    artifact = RetrievalArtifact(
        source_reference_id=reference.id,
        attempt_id=attempt.id,
        content_sha256=digest,
        raw_bytes=raw,
        mime_type="application/pdf",
        byte_size=len(raw),
        final_url="https://cdn.sse.com.cn/final.pdf",
        retrieved_at=now,
    )
    cmd_session.add(artifact)
    cmd_session.flush()

    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    documents = DocumentService(DocumentRepository(cmd_session))
    document = documents.freeze(raw=raw, source_url=reference.canonical_url)
    documents.attach_to_case(
        research_case_id=case_id,
        document_version_id=document.id,
    )
    span = documents.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="公司订单同比增长 20%",
    )
    binding = RetrievalArtifactDocument(
        retrieval_artifact_id=artifact.id,
        document_version_id=document.id,
        relation="content_duplicate",
        publication_key="publication-1",
        created_at=now,
    )
    candidate = AtomicClaimCandidate(
        source_span_id=span.id,
        canonical_key=uuid.uuid4().hex,
        quote="公司订单同比增长 20%",
        quote_start=0,
        quote_end=13,
        quote_sha256=hashlib.sha256("公司订单同比增长 20%".encode()).hexdigest(),
        normalized_text="公司订单同比增长 20%",
        claim_type="reported_claim",
        authority_level="primary",
        structured_fields={},
        validation_result={},
        created_at=now,
    )
    cmd_session.add_all([binding, candidate])
    cmd_session.flush()
    decision = AutomaticAdmissionDecision(
        job_id=job.id,
        candidate_id=candidate.id,
        retrieval_artifact_id=artifact.id,
        outcome="admitted",
        gate_version="gate-v1",
        policy_version="b-scope-v2",
        gate_results={"accepted": True},
        created_at=now,
    )
    cmd_session.add(decision)
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="公司订单同比增长 20%",
        observed_period=None,
        atomic_claim_candidate_id=candidate.id,
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    cmd_session.add(statement)
    cmd_session.flush()
    automatic_link = EvidenceLink(
        thesis_id=thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="订单增长",
        scope={},
        available_at=now,
        creator_type="ai",
        review_state="automatically_admitted",
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    cmd_session.add(automatic_link)
    cmd_session.flush()
    cmd_session.add(
        EventResearchScopeEvidenceAssignment(
            scope_version_id=scope.id,
            evidence_link_id=automatic_link.id,
            factor_statement=thesis.statement,
            disposition="mapped",
            assignment_kind="automatic",
            research_run_id=run_id,
            acquisition_goal_id=job.request_snapshot["goal_id"],
            automatic_admission_decision_id=decision.id,
            automatic_provenance_json={"job_id": str(job.id)},
            created_at=now,
        )
    )
    return automatic_link, digest


def test_workflow_datetime_contract_interprets_naive_sqlite_values_as_utc(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    run_id = uuid.UUID(confirmed["research_run_id"])
    known = datetime(2030, 1, 2, 3, 4, 5)
    link, _digest = _seed_source_ledger(
        cmd_session,
        case_id,
        scope,
        run_id,
        now=known + timedelta(seconds=5),
    )
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    orchestration.state_started_at = known
    orchestration.last_heartbeat_at = known + timedelta(seconds=1)
    orchestration.updated_at = known + timedelta(seconds=2)
    last_sequence = cmd_session.scalar(
        select(ResearchOrchestrationEvent.sequence)
        .where(ResearchOrchestrationEvent.orchestration_id == orchestration.id)
        .order_by(ResearchOrchestrationEvent.sequence.desc())
        .limit(1)
    )
    cmd_session.add(
        ResearchOrchestrationEvent(
            orchestration_id=orchestration.id,
            sequence=int(last_sequence or 0) + 1,
            transition="utc_contract_event",
            actor="system:test",
            message="UTC contract event",
            payload_json={},
            idempotency_key="utc-contract-event",
            created_at=known + timedelta(seconds=3),
        )
    )
    job = cmd_session.scalar(
        select(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run_id)
        .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        .limit(1)
    )
    assert job is not None
    job.status = "running"
    job.stage = "fetching"
    job.lease_owner = "utc-test-worker"
    job.lease_token = "utc-test-token"
    job.lease_expires_at = known + timedelta(seconds=4)
    cmd_session.add_all(
        [
            EventResearchConclusion(
                research_case_id=case_id,
                scope_version_id=scope.id,
                state="ai_draft",
                text="UTC contract conclusion",
                primary_factor=None,
                evidence_link_ids=[str(link.id)],
                reviewer=None,
                created_at=known + timedelta(seconds=6),
            ),
            CaseMonitorVersion(
                research_case_id=case_id,
                version=1,
                status="active",
                frequency="daily",
                factor_ids=[],
                allowed_source_types=["company_disclosure"],
                next_verification_event="UTC contract monitor",
                budget=10,
                changed_by="system:test",
                change_reason="UTC contract",
                created_at=known + timedelta(seconds=7),
            ),
        ]
    )
    cmd_session.commit()

    workflow = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")
    events_page = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/events"
    )

    assert workflow.status_code == 200, workflow.text
    assert events_page.status_code == 200, events_page.text
    body = workflow.json()
    assert body["system_action"]["started_at"] == "2030-01-02T03:04:05Z"
    assert body["system_action"]["heartbeat_at"] == "2030-01-02T03:04:06Z"
    assert body["updated_at"] == "2030-01-02T03:04:07Z"
    assert body["events"][0]["created_at"] == "2030-01-02T03:04:08Z"
    utc_event = next(
        item
        for item in events_page.json()["items"]
        if item["transition"] == "utc_contract_event"
    )
    assert utc_event["created_at"] == "2030-01-02T03:04:08Z"
    projected_round = next(
        item for item in body["acquisition"]["rounds"] if item["job_id"] == str(job.id)
    )
    assert projected_round["lease_expires_at"] == "2030-01-02T03:04:09Z"
    retrieved = {
        item["retrieved_at"]
        for item in body["source_ledger"]["items"]
        if item["retrieved_at"] is not None
    }
    assert retrieved == {"2030-01-02T03:04:10Z"}
    assert body["conclusion"]["created_at"] == "2030-01-02T03:04:11Z"
    assert body["monitor"]["created_at"] == "2030-01-02T03:04:12Z"


def test_source_ledger_separates_review_state_and_exposes_frozen_provenance(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    link, digest = _seed_source_ledger(
        cmd_session, case_id, scope, uuid.UUID(confirmed["research_run_id"])
    )
    cmd_session.add(
        EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=scope.id,
            state="ai_draft",
            text="订单增长证据已进入系统草稿。",
            primary_factor="订单增长",
            evidence_link_ids=[str(link.id)],
            reviewer=None,
            created_at=datetime.now(UTC),
        )
    )
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
    ledger = body["source_ledger"]
    assert ledger["counts"]["reviewed"] == 0
    assert ledger["counts"]["automatically_admitted"] == 1
    admitted = next(item for item in ledger["items"] if item["status"] == "admitted")
    attempt = cmd_session.scalar(
        select(AcquisitionAttempt)
        .where(AcquisitionAttempt.job_id == uuid.UUID(admitted["drilldown"]["href"].split("/")[4]))
        .limit(1)
    )
    assert attempt is not None
    assert admitted["attempt_id"] == str(attempt.id)
    assert admitted["evidence_link_id"] == str(link.id)
    assert admitted["review_state"] == "automatically_admitted"
    assert admitted["role"] == "supports"
    assert admitted["source_role"] == "company_disclosure"
    assert admitted["adapter_key"] == "sse"
    assert admitted["final_url"] == "https://cdn.sse.com.cn/final.pdf"
    assert admitted["content_sha256"] == digest
    assert admitted["admission_outcome"] == "admitted"
    assert admitted["mapping_disposition"] == "mapped"
    assert admitted["mapping_kind"] == "automatic"
    assert admitted["drilldown"] == {
        "kind": "acquisition_evidence",
        "href": admitted["drilldown"]["href"],
    }
    assert admitted["drilldown"]["href"].endswith("/evidence")
    assert body["conclusion"]["evidence_link_ids"] == [str(link.id)]
    assert body["conclusion"]["citations"] == [admitted]


def test_conclusion_resolves_cited_evidence_outside_bounded_ledger_summary(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    link, digest = _seed_source_ledger(
        cmd_session, case_id, scope, uuid.UUID(confirmed["research_run_id"])
    )
    decision = cmd_session.get(
        AutomaticAdmissionDecision, link.automatic_admission_decision_id
    )
    assert decision is not None
    job = cmd_session.get(AcquisitionJob, decision.job_id)
    assert job is not None
    old = datetime(2000, 1, 1, tzinfo=UTC)
    cmd_session.add_all(
        [
            SourceReference(
                job_id=job.id,
                adapter_key="older-history",
                external_record_id=f"older-{index:02d}",
                external_version="v1",
                canonical_url=f"https://example.test/older/{index:02d}",
                title=f"早期候选资料 {index:02d}",
                published_at=old + timedelta(seconds=index),
                source_role="company_disclosure",
                metadata_json={},
                created_at=old + timedelta(seconds=index),
            )
            for index in range(25)
        ]
    )
    cmd_session.add(
        EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=scope.id,
            state="ai_draft",
            text="引用不在摘要窗口内的冻结证据。",
            primary_factor=None,
            evidence_link_ids=[str(link.id)],
            reviewer=None,
            created_at=datetime.now(UTC),
        )
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")

    assert response.status_code == 200, response.text
    body = response.json()
    assert all(
        item["evidence_link_id"] != str(link.id)
        for item in body["source_ledger"]["items"]
    )
    assert len(body["conclusion"]["citations"]) == 1
    citation = body["conclusion"]["citations"][0]
    assert citation["evidence_link_id"] == str(link.id)
    assert citation["final_url"] == "https://cdn.sse.com.cn/final.pdf"
    assert citation["content_sha256"] == digest


def test_workflow_preserves_legacy_non_uuid_conclusion_evidence_ids(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)
    cmd_session.add(
        EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=scope.id,
            state="ai_draft",
            text="旧结论仍可读取。",
            primary_factor=None,
            evidence_link_ids=["evidence-1"],
            reviewer=None,
            created_at=datetime.now(UTC),
        )
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")

    assert response.status_code == 200, response.text
    assert response.json()["conclusion"]["evidence_link_ids"] == ["evidence-1"]
    assert response.json()["conclusion"]["citations"] == []


def test_source_ledger_has_exactly_eight_truthful_statuses_and_drilldowns(
    cmd_client, cmd_session, monkeypatch
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    link, _digest = _seed_source_ledger(
        cmd_session, case_id, scope, uuid.UUID(confirmed["research_run_id"])
    )
    decision = cmd_session.get(
        AutomaticAdmissionDecision, link.automatic_admission_decision_id
    )
    assert decision is not None
    artifact = cmd_session.get(RetrievalArtifact, decision.retrieval_artifact_id)
    assert artifact is not None
    reference = cmd_session.get(SourceReference, artifact.source_reference_id)
    assert reference is not None
    job = cmd_session.get(AcquisitionJob, decision.job_id)
    assert job is not None
    now = datetime.now(UTC)
    cmd_session.add(
        AcquisitionAttempt(
            job_id=job.id,
            adapter_key="sse",
            operation="fetch",
            attempt_no=2,
            started_at=now,
            finished_at=None,
            outcome="running",
            retryable=False,
            safe_metadata={"source_reference_id": str(reference.id)},
        )
    )
    cmd_session.add_all(
        [
            AcquisitionException(
                job_id=job.id,
                source_reference_id=reference.id,
                retrieval_artifact_id=artifact.id,
                reason_code="publication_variant_conflict",
                detail_json={"reason": "同一发布标识存在不同内容哈希"},
                created_at=now,
            ),
            AcquisitionException(
                job_id=job.id,
                source_reference_id=reference.id,
                reason_code="source_policy_skipped",
                detail_json={"reason": "来源角色不在冻结策略内"},
                created_at=now + timedelta(seconds=1),
            ),
            AcquisitionException(
                job_id=job.id,
                source_reference_id=reference.id,
                retrieval_artifact_id=artifact.id,
                candidate_id=decision.candidate_id,
                reason_code="automatic_admission_quarantined",
                detail_json={"reason": "自动准入门禁未通过"},
                created_at=now + timedelta(seconds=2),
            ),
        ]
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")
    assert response.status_code == 200, response.text
    ledger = response.json()["source_ledger"]
    expected = {
        "search_candidate",
        "fetching",
        "frozen",
        "deduplicated",
        "conflicted",
        "admitted",
        "quarantined",
        "skipped",
    }
    assert set(ledger["counts"]["by_status"]) == expected
    assert set(item["status"] for item in ledger["items"]) == expected
    assert ledger["counts"]["total"] == len(ledger["items"])
    assert sum(ledger["counts"]["by_status"].values()) == len(ledger["items"])
    for item in ledger["items"]:
        assert item["record_id"]
        assert item["record_type"]
        assert item["reason"]
        assert item["reason_code"]
        assert item["drilldown"]["kind"]
        drilldown = cmd_client.get(item["drilldown"]["href"])
        assert drilldown.status_code == 200, (item, drilldown.text)
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}',
    )
    foreign = cmd_client.get(
        ledger["items"][0]["drilldown"]["href"],
        headers={"Authorization": "Bearer foreign-token"},
    )
    assert foreign.status_code == 404


def test_main_ledger_is_bounded_and_ledger_cursor_holds_a_concurrent_snapshot(
    cmd_client, cmd_session, monkeypatch
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    job = cmd_session.scalar(
        select(AcquisitionJob)
        .where(AcquisitionJob.research_case_id == case_id)
        .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        .limit(1)
    )
    assert job is not None
    base = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    for index in range(25):
        cmd_session.add(
            SourceReference(
                job_id=job.id,
                adapter_key="linear",
                external_record_id=f"candidate-{index:02d}",
                external_version="v1",
                canonical_url=f"https://example.test/candidate-{index:02d}",
                title=f"候选资料 {index:02d}",
                published_at=base + timedelta(seconds=index),
                source_role="company_disclosure",
                metadata_json={"index": index},
                created_at=base + timedelta(seconds=index),
            )
        )
    cmd_session.commit()

    main = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")
    assert main.status_code == 200, main.text
    summary = main.json()["source_ledger"]
    assert summary["counts"]["total"] == 25
    assert summary["total"] == 25
    assert len(summary["items"]) == 20
    assert summary["has_more"] is True

    first = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        params={"limit": 7, "status": "search_candidate"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["total"] == 25
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]
    assert len(first_body["items"]) == 7
    assert {item["status"] for item in first_body["items"]} == {
        "search_candidate"
    }
    assert all(
        item["recorded_at"].endswith("Z")
        or item["recorded_at"].endswith("+00:00")
        for item in first_body["items"]
    )

    concurrent = SourceReference(
        job_id=job.id,
        adapter_key="linear",
        external_record_id="candidate-concurrent",
        external_version="v1",
        canonical_url="https://example.test/candidate-concurrent",
        title="分页开始后新增的候选资料",
        published_at=base + timedelta(hours=1),
        source_role="company_disclosure",
        metadata_json={},
        created_at=base + timedelta(hours=1),
    )
    cmd_session.add(concurrent)
    cmd_session.commit()

    seen = list(first_body["items"])
    cursor = first_body["next_cursor"]
    while cursor is not None:
        page = cmd_client.get(
            f"/api/v1/event-research/{case_id}/workflow/ledger",
            params={
                "limit": 7,
                "status": "search_candidate",
                "cursor": cursor,
            },
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["total"] == 25
        seen.extend(body["items"])
        cursor = body["next_cursor"]
    ids = [item["record_id"] for item in seen]
    assert len(ids) == len(set(ids)) == 25
    assert str(concurrent.id) not in ids
    assert all(
        set(item)
        >= {
            "record_id",
            "record_type",
            "status",
            "reason",
            "reason_code",
            "drilldown",
        }
        for item in seen
    )

    mismatch = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        params={"limit": 7, "status": "frozen", "cursor": first_body["next_cursor"]},
    )
    invalid_status = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        params={"status": "unknown_status"},
    )
    malformed_cursor = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        params={"cursor": "not-a-valid-ledger-cursor"},
    )
    assert mismatch.status_code == 422
    assert invalid_status.status_code == 422
    assert malformed_cursor.status_code == 422

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","foreign-token":"foreign-team"}',
    )
    foreign = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        headers={"Authorization": "Bearer foreign-token"},
    )
    unauthenticated = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow/ledger",
        headers={"Authorization": ""},
    )
    assert foreign.status_code == 404
    assert unauthenticated.status_code == 401


def test_workflow_polling_uses_bounded_database_ledger_queries(
    cmd_client, cmd_session, monkeypatch
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    job = cmd_session.scalar(
        select(AcquisitionJob)
        .where(AcquisitionJob.research_case_id == case_id)
        .order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        .limit(1)
    )
    assert job is not None
    base = datetime(2030, 2, 3, 4, 5, 6, tzinfo=UTC)
    cmd_session.add_all(
        [
            SourceReference(
                job_id=job.id,
                adapter_key="large-history",
                external_record_id=f"candidate-{index:04d}",
                external_version="v1",
                canonical_url=f"https://example.test/history/{index:04d}",
                title=f"历史候选资料 {index:04d}",
                published_at=base + timedelta(seconds=index),
                source_role="company_disclosure",
                metadata_json={"index": index},
                created_at=base + timedelta(seconds=index),
            )
            for index in range(250)
        ]
    )
    cmd_session.commit()

    def reject_full_ledger_load(*_args, **_kwargs):
        raise AssertionError("workflow polling must not load the full acquisition ledger")

    monkeypatch.setattr(AcquisitionModule, "workflow_ledger", reject_full_ledger_load)
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.split())
        if "source_references" in normalized:
            statements.append(normalized)

    event.listen(cmd_session.bind, "before_cursor_execute", capture_statement)
    try:
        workflow = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")
        ledger = cmd_client.get(
            f"/api/v1/event-research/{case_id}/workflow/ledger",
            params={"limit": 7, "status": "search_candidate"},
        )
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", capture_statement)

    assert workflow.status_code == 200, workflow.text
    assert ledger.status_code == 200, ledger.text
    summary = workflow.json()["source_ledger"]
    assert summary["total"] == 250
    assert len(summary["items"]) == 20
    assert ledger.json()["total"] == 250
    assert len(ledger.json()["items"]) == 7
    assert statements
    assert all(
        " LIMIT " in statement.upper() or "COUNT(" in statement.upper()
        for statement in statements
    )


def test_workflow_ledger_status_vocabulary_has_one_closed_source():
    from typing import get_args, get_type_hints

    from app.domain.acquisition import (
        AcquisitionWorkflowLedgerRecord,
        WORKFLOW_LEDGER_STATUSES,
        WorkflowLedgerStatus,
    )
    from app.schemas.v1.research_workflow import WorkflowSourceLedgerItemDTO

    expected = (
        "search_candidate",
        "fetching",
        "frozen",
        "deduplicated",
        "conflicted",
        "admitted",
        "quarantined",
        "skipped",
    )
    status_schema = WorkflowSourceLedgerItemDTO.model_json_schema()["properties"][
        "status"
    ]

    assert WORKFLOW_LEDGER_STATUSES == expected
    assert get_args(WorkflowLedgerStatus) == expected
    assert get_args(
        get_type_hints(AcquisitionWorkflowLedgerRecord)["status"]
    ) == get_args(WorkflowLedgerStatus)
    assert tuple(status_schema["enum"]) == expected


def test_monitoring_exposes_unreviewed_system_draft_and_no_user_action(
    cmd_client, cmd_session
):
    case_id, scope = _create_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id)
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed["orchestration_id"])
    )
    assert orchestration is not None
    now = datetime.now(UTC)
    orchestration.state = "monitoring"
    orchestration.user_stage = "report_monitoring"
    orchestration.current_system_action = "正在监测后续披露"
    orchestration.system_action_reason = "系统草稿已经生成"
    orchestration.next_action_kind = None
    orchestration.next_action_label = None
    orchestration.next_action_payload = None
    draft = EventResearchConclusion(
        research_case_id=case_id,
        scope_version_id=scope.id,
        state="ai_draft",
        text="当前证据更支持订单增长，但仍保留替代解释。",
        primary_factor="订单增长",
        evidence_link_ids=[],
        reviewer=None,
        created_at=now,
    )
    monitor = CaseMonitorVersion(
        research_case_id=case_id,
        version=1,
        status="active",
        frequency="daily",
        factor_ids=[],
        allowed_source_types=["company_disclosure"],
        next_verification_event="下一份月度经营公告",
        budget=100,
        changed_by="system:event-research",
        change_reason="自动研究完成后开始监测",
        created_at=now,
    )
    cmd_session.add_all([draft, monitor])
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow").json()
    assert body["stages"][-1]["status"] == "active"
    assert body["user_action"] is None
    assert body["user_action_summary"] == "当前无需操作"
    assert body["conclusion"]["state"] == "ai_draft"
    assert body["conclusion"]["human_reviewed"] is False
    assert body["conclusion"]["review_label"] == "系统生成，未经人工审核"
    assert body["monitor"]["version"] == 1
    assert body["monitor"]["source_types"] == ["company_disclosure"]


def test_projection_query_count_is_fixed_for_multiple_goals(cmd_client, cmd_session):
    case_id, scope = _create_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id)
    ResearchOrchestrationService(cmd_session).reconcile(
        case_id,
        OrchestrationPrincipal(tenant_id="test-team", actor="system:test"),
    )
    cmd_session.commit()
    statements = 0

    def count_statement(*_args, **_kwargs):
        nonlocal statements
        statements += 1

    bind = cmd_session.get_bind()
    event.listen(bind, "before_cursor_execute", count_statement)
    try:
        response = cmd_client.get(f"/api/v1/event-research/{case_id}/workflow")
    finally:
        event.remove(bind, "before_cursor_execute", count_statement)

    assert response.status_code == 200, response.text
    # The ledger is one UNION projection with database aggregation and a
    # bounded keyset window. The query bound stays fixed as goals and history grow.
    assert statements <= 18
