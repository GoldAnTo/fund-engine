from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.errors import ValidationFailedError

from app.models.event_research import (
    EventResearchScopeVersion,
)
from app.models.events import DomainEvent
from app.models.ledger import Thesis
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    JobEvent,
    ResearchRun,
    ResearchTask,
)
from app.models.research_monitor import ResearchRunEvent
from app.models.research_orchestration import (
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.repositories.research_orchestration import (
    ORCHESTRATION_STATES,
    ResearchOrchestrationRepository,
    USER_STAGES,
)
from app.services.research_orchestration import (
    AcquisitionProgressSnapshot,
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
from app.services.event_research_scope import EventResearchScopeService
from app.services.auto_research import AutoResearchService
from tests.protocol_provenance import seed_protocol_footprint


CONFIRM_ACTION = "正在根据已确认范围规划资料获取"
CONFIRM_REASON = (
    "研究范围已确认，系统将为每个证据目标生成受治理的获取任务"
)


def _event_payload(*, research_protocol_required: bool | None = None) -> dict:
    payload = {
        "raw_input": "公司上调资本开支指引，市场重新评估需求。",
        "event_title": "资本开支指引更新",
        "research_question": "资本开支上调能否被后续经营数据验证？",
        "candidate_factors": ["订单增长", "收入兑现", "替代解释"],
    }
    if research_protocol_required is not None:
        payload["research_protocol_required"] = research_protocol_required
    return payload


def _create_event_case(
    cmd_client,
    cmd_session,
    *,
    research_protocol_required: bool | None = None,
) -> tuple[uuid.UUID, EventResearchScopeVersion]:
    response = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            research_protocol_required=research_protocol_required,
        ),
    )
    assert response.status_code == 201, response.text
    case_id = uuid.UUID(response.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    return case_id, scope


def _confirm(cmd_client, case_id: uuid.UUID, scope_id: uuid.UUID, key: str):
    return cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={"scope_version_id": str(scope_id), "idempotency_key": key},
    )


def _count(cmd_session, model, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return int(cmd_session.scalar(statement) or 0)


def test_scope_decision_command_uses_server_actor_and_is_idempotent(
    cmd_client,
    cmd_session,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id, "confirm-before-decision")
    assert confirmed.status_code == 202, confirmed.text
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    assert orchestration is not None
    orchestration.state = "needs_scope_decision"
    orchestration.next_action_kind = "scope_decision"
    orchestration.next_action_label = "决定是否调整研究边界"
    orchestration.next_action_payload = {
        "missing": ["一手披露"],
        "attempted": ["交易所检索"],
        "cannot_continue_reason": "当前轮次已结束",
        "recommendation": {
            "kind": "revise_scope",
            "label": "调整研究边界",
            "impact": "创建新的冻结范围版本后继续补证",
        },
        "alternatives": [
            {"kind": "keep_scope", "label": "保持范围", "impact": "保留未知项"},
            {"kind": "stop", "label": "停止", "impact": "停止后续研究"},
        ],
    }
    cmd_session.commit()

    payload = {
        "kind": "keep_scope",
        "reason": "保持当前冻结范围，未解决项保留为未知",
        "expected_version": orchestration.version,
        "idempotency_key": "scope-decision-1",
    }
    first = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/decision",
        json=payload,
    )
    replay = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/decision",
        json=payload,
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert first.json()["state"] == "synthesizing_evidence"
    assert replay.json() == first.json()
    events = list(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id,
                ResearchOrchestrationEvent.transition == "scope_decided",
            )
        )
    )
    assert len(events) == 1
    assert events[0].actor == "user:test-team"
    assert events[0].payload_json["decision"] == "keep_scope"


def test_scope_decision_command_rejects_client_supplied_identity(
    cmd_client,
    cmd_session,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    _confirm(cmd_client, case_id, scope.id, "confirm-before-invalid-decision")

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/decision",
        json={
            "kind": "stop",
            "reason": "停止",
            "expected_version": 1,
            "idempotency_key": "invalid-identity",
            "actor": "user:forged",
        },
    )

    assert response.status_code == 422, response.text


def _block_on_protocol_scope_revision(cmd_client, cmd_session):
    case_id, first_scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(
        cmd_client,
        case_id,
        first_scope.id,
        "confirm-before-protocol-block",
    )
    assert confirmed.status_code == 202, confirmed.text
    revised = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": ["订单增长", "收入兑现", "新增协议因素"],
            "change_reason": "加入需要研究协议的新因素",
        },
    )
    assert revised.status_code == 200, revised.text
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    blocked_thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == "新增协议因素",
        )
    )
    assert scope is not None
    assert orchestration is not None
    assert blocked_thesis is not None
    assert orchestration.state == "needs_scope_decision"
    assert orchestration.current_research_run_id is None
    return case_id, scope, orchestration, blocked_thesis


def _resume_protocol(cmd_client, case_id, scope_id, key):
    return cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/resume-protocol",
        json={
            "scope_version_id": str(scope_id),
            "idempotency_key": key,
        },
    )


def test_scope_confirmation_prepares_the_automatic_mainline(cmd_client, cmd_session):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    thesis_ids = list(
        cmd_session.scalars(
            select(Thesis.id)
            .where(Thesis.research_case_id == case_id)
            .order_by(Thesis.statement, Thesis.created_at, Thesis.id)
        )
    )

    response = _confirm(cmd_client, case_id, scope.id, "confirm-mainline-1")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body == {
        "orchestration_id": body["orchestration_id"],
        "case_id": str(case_id),
        "scope_version_id": str(scope.id),
        "research_run_id": body["research_run_id"],
        "state": "planning_acquisition",
        "user_stage": "acquisition",
        "current_system_action": CONFIRM_ACTION,
        "system_action_reason": CONFIRM_REASON,
        "next_action": None,
        "version": 1,
    }
    orchestration_id = uuid.UUID(body["orchestration_id"])
    run_id = uuid.UUID(body["research_run_id"])
    orchestration = cmd_session.get(ResearchOrchestration, orchestration_id)
    run = cmd_session.get(ResearchRun, run_id)
    assert orchestration is not None
    assert orchestration.current_research_run_id == run_id
    assert orchestration.checkpoint_json == {
        "scope_version_id": str(scope.id),
        "research_run_id": str(run_id),
        "acquisition_round": 1,
    }
    assert run is not None
    assert (run.status, run.stage) == ("prepared", "awaiting_acquisition")
    assert run.max_rounds == 3
    assert run.budget == 100
    assert set(run.scope_thesis_ids or []) == {str(value) for value in thesis_ids}
    tasks = list(
        cmd_session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run_id)
            .order_by(ResearchTask.thesis_id, ResearchTask.task_type)
        )
    )
    assert len(tasks) == 4 * len(thesis_ids)
    assert {task.task_type for task in tasks} == {
        "support",
        "contradict",
        "result",
        "alternative",
    }
    assert _count(
        cmd_session,
        Job,
        Job.kind == "research_run",
        Job.target_id == run_id,
    ) == 0
    scope_event = cmd_session.scalar(
        select(ResearchRunEvent).where(
            ResearchRunEvent.run_id == run_id,
            ResearchRunEvent.stage == "scope",
        )
    )
    assert scope_event is not None
    assert scope_event.payload_json["trigger"] == "scope_confirmation"
    assert scope_event.payload_json["scope_version_id"] == str(scope.id)
    assert scope_event.payload_json["orchestration_id"] == str(orchestration_id)
    workflow_event = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration_id,
            ResearchOrchestrationEvent.transition == "scope_confirmed",
        )
    )
    assert workflow_event is not None
    assert workflow_event.actor == "user:test-team"


def test_late_same_key_replay_returns_the_current_persisted_projection(
    cmd_client,
    cmd_session,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    first = _confirm(cmd_client, case_id, scope.id, "late-replay")
    assert first.status_code == 202
    orchestration_id = uuid.UUID(first.json()["orchestration_id"])
    run_id = uuid.UUID(first.json()["research_run_id"])
    service = ResearchOrchestrationService(cmd_session)
    current = service.record_acquisition_progress(
        case_id,
        OrchestrationPrincipal(
            tenant_id="test-team",
            actor="tenant:test-team",
        ),
        AcquisitionProgressSnapshot(
            target_state="acquiring",
            user_stage="acquisition",
            action="正在获取已规划的资料",
            reason="资料获取计划已经冻结",
            checkpoint={
                "scope_version_id": str(scope.id),
                "research_run_id": str(run_id),
                "acquisition_round": 1,
            },
        ),
        "acquisition-progress-1",
    )
    cmd_session.commit()
    counts_before_replay = {
        "runs": _count(cmd_session, ResearchRun),
        "tasks": _count(cmd_session, ResearchTask),
        "run_events": _count(cmd_session, ResearchRunEvent),
        "workflow_events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
        ),
        "jobs": _count(cmd_session, Job, Job.kind == "research_run"),
        "job_events": _count(cmd_session, JobEvent),
    }
    version_before_replay = current.version

    replay = _confirm(cmd_client, case_id, scope.id, "late-replay")

    assert replay.status_code == 202, replay.text
    assert replay.json() == {
        "orchestration_id": str(orchestration_id),
        "case_id": str(case_id),
        "scope_version_id": str(scope.id),
        "research_run_id": str(run_id),
        "state": "acquiring",
        "user_stage": "acquisition",
        "current_system_action": "正在获取已规划的资料",
        "system_action_reason": "资料获取计划已经冻结",
        "next_action": None,
        "version": version_before_replay,
    }
    assert cmd_session.get(ResearchOrchestration, orchestration_id).version == (
        version_before_replay
    )
    assert counts_before_replay == {
        "runs": _count(cmd_session, ResearchRun),
        "tasks": _count(cmd_session, ResearchTask),
        "run_events": _count(cmd_session, ResearchRunEvent),
        "workflow_events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
        ),
        "jobs": _count(cmd_session, Job, Job.kind == "research_run"),
        "job_events": _count(cmd_session, JobEvent),
    }


def test_prepared_confirmation_is_not_presented_as_active_queued_work(
    cmd_client,
    cmd_session,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id, "prepared-not-active")
    assert confirmed.status_code == 202

    response = cmd_client.get("/api/v1/research-runs/active")

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_scope_revision_cancels_prepared_run_and_prepares_ordered_successor(
    cmd_client,
    cmd_session,
):
    case_id, first_scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, first_scope.id, "before-revision")
    assert confirmed.status_code == 202
    orchestration_id = uuid.UUID(confirmed.json()["orchestration_id"])
    old_run_id = uuid.UUID(confirmed.json()["research_run_id"])
    revised_factors = ["替代解释", "订单增长", "收入兑现"]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": revised_factors,
            "change_reason": "删除旧因素并调整优先级",
        },
    )

    assert response.status_code == 200, response.text
    scopes = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assert len(scopes) == 2
    revised_scope = scopes[-1]
    orchestration = cmd_session.get(ResearchOrchestration, orchestration_id)
    old_run = cmd_session.get(ResearchRun, old_run_id)
    assert orchestration is not None
    assert old_run is not None
    assert (old_run.status, old_run.stage) == ("cancelled", "stopped")
    assert {
        (task.status, task.stage)
        for task in cmd_session.scalars(
            select(ResearchTask).where(ResearchTask.run_id == old_run_id)
        )
    } == {("cancelled", "stopped")}
    assert orchestration.current_scope_version_id == revised_scope.id
    assert orchestration.current_research_run_id not in {None, old_run_id}
    assert (orchestration.state, orchestration.user_stage) == (
        "planning_acquisition",
        "acquisition",
    )
    assert orchestration.current_system_action == (
        "已确认的研究范围发生变化，正在重新规划资料获取"
    )
    assert orchestration.system_action_reason == (
        "已确认范围已修订，旧研究运行已取消，系统将按新范围重新规划资料获取"
    )
    successor = cmd_session.get(
        ResearchRun,
        orchestration.current_research_run_id,
    )
    assert successor is not None
    assert (successor.status, successor.stage) == (
        "prepared",
        "awaiting_acquisition",
    )
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 2
    thesis_by_statement = {
        thesis.statement: thesis.id
        for thesis in cmd_session.scalars(
            select(Thesis).where(Thesis.research_case_id == case_id)
        )
    }
    assert successor.scope_thesis_ids == [
        str(thesis_by_statement[statement]) for statement in revised_factors
    ]
    assert _count(
        cmd_session,
        Job,
        Job.kind == "research_run",
        Job.target_id.in_([old_run_id, successor.id]),
    ) == 0
    scope_event = cmd_session.scalar(
        select(ResearchRunEvent).where(
            ResearchRunEvent.run_id == successor.id,
            ResearchRunEvent.stage == "scope",
        )
    )
    assert scope_event is not None
    assert scope_event.payload_json["trigger"] == "scope_revision"
    assert scope_event.payload_json["factor_ids"] == successor.scope_thesis_ids
    assert scope_event.payload_json["scope_version_id"] == str(revised_scope.id)
    assert scope_event.payload_json["orchestration_id"] == str(orchestration_id)
    assert scope_event.payload_json["superseded_run_id"] == str(old_run_id)
    transitions = list(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition)
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == orchestration_id
            )
            .order_by(ResearchOrchestrationEvent.sequence)
        )
    )
    assert transitions == ["scope_confirmed", "scope_revised"]
    assert _count(
        cmd_session,
        DomainEvent,
        DomainEvent.aggregate_type == "research_orchestration",
        DomainEvent.aggregate_id == str(orchestration_id),
    ) == 2
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    assert lifecycle.active_run_id == successor.id
    assert lifecycle.status == "researching"
    with pytest.raises(ValueError, match="prepared"):
        AutoResearchService(cmd_session).enqueue_prepared_run(
            old_run_id,
            commit=False,
        )


def test_scope_revision_rollback_restores_scope_run_and_orchestration(
    cmd_client,
    cmd_session,
):
    case_id, first_scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, first_scope.id, "before-rollback")
    orchestration_id = uuid.UUID(confirmed.json()["orchestration_id"])
    old_run_id = uuid.UUID(confirmed.json()["research_run_id"])
    baseline = {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(cmd_session, DomainEvent),
    }

    EventResearchScopeService(cmd_session).update(
        case_id,
        ["替代解释", "订单增长", "收入兑现"],
        "human:scope-editor",
        "验证事务回滚",
    )
    changed = cmd_session.get(ResearchOrchestration, orchestration_id)
    assert changed is not None
    assert changed.current_scope_version_id != first_scope.id
    assert changed.current_research_run_id != old_run_id
    cmd_session.rollback()

    restored = cmd_session.get(ResearchOrchestration, orchestration_id)
    old_run = cmd_session.get(ResearchRun, old_run_id)
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert restored is not None
    assert restored.current_scope_version_id == first_scope.id
    assert restored.current_research_run_id == old_run_id
    assert restored.version == 1
    assert old_run is not None
    assert (old_run.status, old_run.stage) == (
        "prepared",
        "awaiting_acquisition",
    )
    assert lifecycle is not None
    assert lifecycle.active_run_id == old_run_id
    assert lifecycle.status == "researching"
    assert _count(cmd_session, EventResearchScopeVersion) == 1
    assert baseline == {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(cmd_session, DomainEvent),
    }


def test_protocol_blocked_scope_revision_has_no_stale_executable_run(
    cmd_client,
    cmd_session,
):
    case_id, first_scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, first_scope.id, "before-blocked-revision")
    orchestration_id = uuid.UUID(confirmed.json()["orchestration_id"])
    old_run_id = uuid.UUID(confirmed.json()["research_run_id"])

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": ["订单增长", "收入兑现", "新增协议因素"],
            "change_reason": "加入需要协议的新因素",
        },
    )

    assert response.status_code == 200, response.text
    revised_scope = cmd_session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    orchestration = cmd_session.get(ResearchOrchestration, orchestration_id)
    old_run = cmd_session.get(ResearchRun, old_run_id)
    assert revised_scope is not None
    assert orchestration is not None
    assert old_run is not None
    assert (old_run.status, old_run.stage) == ("cancelled", "stopped")
    assert orchestration.current_scope_version_id == revised_scope.id
    assert orchestration.current_research_run_id is None
    assert (orchestration.state, orchestration.user_stage) == (
        "needs_scope_decision",
        "scope_confirmation",
    )
    assert orchestration.next_action_kind == "complete_research_protocol"
    assert orchestration.next_action_label == (
        "完成新增因素的研究协议后再启动补证"
    )
    assert orchestration.next_action_payload["scope_version_id"] == str(
        revised_scope.id
    )
    context = orchestration.next_action_payload
    assert context["action"] == "complete_research_protocol"
    assert context["scope_version"] == revised_scope.version
    assert context["missing"]
    assert context["attempted"]
    assert context["cannot_continue_reason"]
    assert context["recommendation"] == {
        "kind": "complete_research_protocol",
        "label": "完成研究协议",
        "impact": "补全新增命题的研究协议后，系统可按当前范围启动补证。",
    }
    assert len(context["alternatives"]) >= 2
    assert all(
        set(item) == {"kind", "label", "impact"}
        for item in context["alternatives"]
    )
    assert context["blocked_theses"]
    assert {
        item["thesis_id"]: item["reason_codes"]
        for item in context["blocked_theses"]
    } == context["reason_codes"]

    workflow = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workflow"
    )
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["user_action"]["payload"] == context
    assert workflow.json()["user_action"]["recommendation"] == context[
        "recommendation"
    ]
    assert workflow.json()["user_action"]["alternatives"] == context[
        "alternatives"
    ]
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 1
    assert _count(cmd_session, Job, Job.kind == "research_run") == 0
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    assert lifecycle.status == "awaiting_scope"
    assert lifecycle.active_run_id is None
    assert lifecycle.next_human_action == orchestration.next_action_label
    assert list(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition)
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == orchestration_id
            )
            .order_by(ResearchOrchestrationEvent.sequence)
        )
    ) == ["scope_confirmed", "scope_revised"]
    assert _count(
        cmd_session,
        DomainEvent,
        DomainEvent.aggregate_type == "research_orchestration",
        DomainEvent.aggregate_id == str(orchestration_id),
    ) == 2
    replay = _confirm(
        cmd_client,
        case_id,
        first_scope.id,
        "before-blocked-revision",
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["scope_version_id"] == str(revised_scope.id)
    assert replay.json()["research_run_id"] is None
    assert replay.json()["state"] == "needs_scope_decision"
    assert replay.json()["user_stage"] == "scope_confirmation"
    assert replay.json()["next_action"] == {
        "kind": "complete_research_protocol",
        "label": "完成新增因素的研究协议后再启动补证",
        "payload": orchestration.next_action_payload,
    }
    with pytest.raises(ValueError, match="prepared"):
        AutoResearchService(cmd_session).enqueue_prepared_run(
            old_run_id,
            commit=False,
        )


def test_protocol_action_repository_rejects_incomplete_or_open_context(
    cmd_client, cmd_session
):
    case_id, _first_scope = _create_event_case(cmd_client, cmd_session)
    first_scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert first_scope is not None
    confirmed = _confirm(cmd_client, case_id, first_scope.id, "closed-context")
    assert confirmed.status_code == 202, confirmed.text
    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": ["订单增长", "收入兑现", "新增协议因素"],
            "change_reason": "验证协议上下文闭集",
        },
    )
    assert response.status_code == 200, response.text
    orchestration = cmd_session.get(
        ResearchOrchestration, uuid.UUID(confirmed.json()["orchestration_id"])
    )
    assert orchestration is not None
    incomplete = dict(orchestration.next_action_payload)
    incomplete.pop("alternatives")
    with pytest.raises(ValidationFailedError, match="closed typed context"):
        ResearchOrchestrationRepository._validate_protocol_action(incomplete)
    open_context = {
        **orchestration.next_action_payload,
        "query_generated_copy": "不允许 query 临时补文案",
    }
    with pytest.raises(ValidationFailedError, match="closed typed context"):
        ResearchOrchestrationRepository._validate_protocol_action(open_context)


def test_resume_protocol_completion_prepares_one_run_without_a_job(
    cmd_client,
    cmd_session,
):
    case_id, scope, orchestration, blocked_thesis = (
        _block_on_protocol_scope_revision(cmd_client, cmd_session)
    )
    seed_protocol_footprint(
        cmd_session,
        blocked_thesis,
        status="ready",
    )
    cmd_session.commit()

    response = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-protocol-1",
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body == {
        "orchestration_id": str(orchestration.id),
        "case_id": str(case_id),
        "scope_version_id": str(scope.id),
        "research_run_id": body["research_run_id"],
        "state": "planning_acquisition",
        "user_stage": "acquisition",
        "current_system_action": "研究协议已完成，正在准备资料获取",
        "system_action_reason": (
            "当前范围内所有必需研究协议均已通过校验，"
            "系统将按确认范围规划资料获取"
        ),
        "next_action": None,
        "version": 3,
    }
    run_id = uuid.UUID(body["research_run_id"])
    run = cmd_session.get(ResearchRun, run_id)
    assert run is not None
    assert (run.status, run.stage) == (
        "prepared",
        "awaiting_acquisition",
    )
    assert _count(
        cmd_session,
        Job,
        Job.kind == "research_run",
        Job.target_id == run.id,
    ) == 0
    assert len(
        list(
            cmd_session.scalars(
                select(ResearchTask).where(ResearchTask.run_id == run.id)
            )
        )
    ) == 12
    scope_event = cmd_session.scalar(
        select(ResearchRunEvent).where(
            ResearchRunEvent.run_id == run.id,
            ResearchRunEvent.stage == "scope",
        )
    )
    assert scope_event is not None
    assert scope_event.payload_json["trigger"] == "protocol_completion"
    assert scope_event.payload_json["scope_version_id"] == str(scope.id)
    assert scope_event.payload_json["orchestration_id"] == str(
        orchestration.id
    )
    event = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration.id,
            ResearchOrchestrationEvent.transition == "protocol_completed",
        )
    )
    assert event is not None
    assert event.actor == "user:test-team"
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    assert lifecycle.active_run_id == run.id
    assert lifecycle.status == "researching"
    assert lifecycle.current_gap is None
    assert lifecycle.next_human_action is None


def test_resume_protocol_rejects_a_stale_scope_version_number(
    cmd_client,
    cmd_session,
):
    case_id, scope, _orchestration, blocked_thesis = (
        _block_on_protocol_scope_revision(cmd_client, cmd_session)
    )
    seed_protocol_footprint(cmd_session, blocked_thesis, status="ready")
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/resume-protocol",
        json={
            "scope_version_id": str(scope.id),
            "scope_version": scope.version + 1,
            "idempotency_key": "resume-protocol-stale-version",
        },
    )

    assert response.status_code == 422, response.text
    assert "scope_version must match" in response.json()["error"]["message"]
    assert _count(cmd_session, ResearchRun) == 1


def test_resume_protocol_while_still_blocked_has_no_writes(
    cmd_client,
    cmd_session,
):
    case_id, scope, orchestration, _ = _block_on_protocol_scope_revision(
        cmd_client,
        cmd_session,
    )
    baseline = {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration.id),
        ),
        "version": orchestration.version,
    }

    response = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-still-blocked",
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert baseline == {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration.id),
        ),
        "version": cmd_session.get(
            ResearchOrchestration,
            orchestration.id,
        ).version,
    }


def test_resume_protocol_same_key_replays_and_different_key_conflicts(
    cmd_client,
    cmd_session,
):
    case_id, scope, orchestration, blocked_thesis = (
        _block_on_protocol_scope_revision(cmd_client, cmd_session)
    )
    seed_protocol_footprint(
        cmd_session,
        blocked_thesis,
        status="ready",
    )
    cmd_session.commit()
    first = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-replay",
    )
    assert first.status_code == 202, first.text
    baseline = {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration.id),
        ),
        "jobs": _count(cmd_session, Job, Job.kind == "research_run"),
        "version": cmd_session.get(
            ResearchOrchestration,
            orchestration.id,
        ).version,
    }

    replay = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-replay",
    )
    conflict = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-different-key",
    )

    assert replay.status_code == 202, replay.text
    assert replay.json() == first.json()
    assert conflict.status_code == 409, conflict.text
    assert baseline == {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration.id),
        ),
        "jobs": _count(cmd_session, Job, Job.kind == "research_run"),
        "version": cmd_session.get(
            ResearchOrchestration,
            orchestration.id,
        ).version,
    }


def test_resume_protocol_route_rolls_back_service_exception(
    cmd_client,
    cmd_session,
    monkeypatch,
):
    case_id, scope, orchestration, blocked_thesis = (
        _block_on_protocol_scope_revision(cmd_client, cmd_session)
    )
    seed_protocol_footprint(
        cmd_session,
        blocked_thesis,
        status="ready",
    )
    cmd_session.commit()
    baseline = {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "version": orchestration.version,
    }

    def fail_start(*args, **kwargs):
        raise RuntimeError("protocol successor persistence failed")

    monkeypatch.setattr(
        "app.services.auto_research.AutoResearchService.start",
        fail_start,
    )
    response = _resume_protocol(
        cmd_client,
        case_id,
        scope.id,
        "resume-rollback",
    )

    assert response.status_code == 500
    assert baseline == {
        "runs": _count(cmd_session, ResearchRun),
        "events": _count(cmd_session, ResearchOrchestrationEvent),
        "version": cmd_session.get(
            ResearchOrchestration,
            orchestration.id,
        ).version,
    }


def test_resume_protocol_request_has_no_identity_fields():
    from app.schemas.v1.research_workflow import (
        ResumeProtocolWorkflowRequest,
    )

    assert set(ResumeProtocolWorkflowRequest.model_fields) == {
        "scope_version_id",
        "scope_version",
        "idempotency_key",
    }


def test_resume_protocol_rejects_client_identity_fields(cmd_client, cmd_session):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/resume-protocol",
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": "client-identity-rejected",
            "actor": "client:forged",
            "tenant_id": "tenant:forged",
            "reviewer": "client:forged",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_resume_protocol_requires_authentication(client):
    response = client.post(
        f"/api/v1/event-research/{uuid.uuid4()}/workflow/resume-protocol",
        json={
            "scope_version_id": str(uuid.uuid4()),
            "idempotency_key": "missing-auth",
        },
    )

    assert response.status_code == 401


def test_workflow_response_supports_all_constrained_states_and_stages():
    from app.schemas.v1 import research_workflow

    action_type = getattr(research_workflow, "WorkflowNextActionDTO", None)
    assert action_type is not None
    next_action = action_type(
        kind="scope_decision",
        label="决定是否重试当前范围",
        payload={"attempted_rounds": 3},
    )
    for state in ORCHESTRATION_STATES:
        for user_stage in USER_STAGES:
            response = research_workflow.ConfirmResearchWorkflowResponse(
                orchestration_id=uuid.uuid4(),
                case_id=uuid.uuid4(),
                scope_version_id=uuid.uuid4(),
                research_run_id=uuid.uuid4(),
                state=state,
                user_stage=user_stage,
                current_system_action=None,
                system_action_reason=None,
                next_action=(
                    next_action if state == "needs_scope_decision" else None
                ),
                version=4,
            )
            assert response.state == state
            assert response.user_stage == user_stage
            if state == "needs_scope_decision":
                assert response.next_action == next_action


def test_same_key_replays_without_any_duplicate_write(cmd_client, cmd_session):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    first = _confirm(cmd_client, case_id, scope.id, "same-confirmation")
    assert first.status_code == 202
    orchestration_id = uuid.UUID(first.json()["orchestration_id"])
    run_id = uuid.UUID(first.json()["research_run_id"])
    first_counts = {
        "runs": _count(cmd_session, ResearchRun),
        "tasks": _count(cmd_session, ResearchTask),
        "run_events": _count(cmd_session, ResearchRunEvent),
        "workflow_events": _count(
            cmd_session,
            ResearchOrchestrationEvent,
            ResearchOrchestrationEvent.orchestration_id == orchestration_id,
        ),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration_id),
        ),
        "jobs": _count(
            cmd_session,
            Job,
            Job.kind == "research_run",
            Job.target_id == run_id,
        ),
    }
    assert first_counts == {
        "runs": 1,
        "tasks": 12,
        "run_events": 1,
        "workflow_events": 1,
        "outbox": 1,
        "jobs": 0,
    }
    assert ResearchOrchestrationRepository(
        cmd_session
    ).replay_command_fingerprint(
        orchestration_id,
        "same-confirmation",
    ) == {
        "actor": "user:test-team",
        "event_transition": "scope_confirmed",
        "current_scope_version_id": str(scope.id),
        "tenant_id": "test-team",
        "research_case_id": str(case_id),
    }

    replay = _confirm(cmd_client, case_id, scope.id, "same-confirmation")

    assert replay.status_code == 202
    assert replay.json() == first.json()
    assert cmd_session.get(ResearchOrchestration, orchestration_id).version == 1
    assert first_counts == {
        "runs": _count(cmd_session, ResearchRun),
        "tasks": _count(cmd_session, ResearchTask),
        "run_events": _count(cmd_session, ResearchRunEvent),
        "workflow_events": _count(
            cmd_session,
            ResearchOrchestrationEvent,
            ResearchOrchestrationEvent.orchestration_id == orchestration_id,
        ),
        "outbox": _count(
            cmd_session,
            DomainEvent,
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration_id),
        ),
        "jobs": _count(
            cmd_session,
            Job,
            Job.kind == "research_run",
            Job.target_id == run_id,
        ),
    }


def test_different_key_after_confirmation_conflicts(cmd_client, cmd_session):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    assert _confirm(cmd_client, case_id, scope.id, "first-key").status_code == 202

    response = _confirm(cmd_client, case_id, scope.id, "different-key")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 1


@pytest.mark.parametrize("kind", ["stale", "foreign"])
def test_stale_or_foreign_scope_is_rejected_without_writes(
    cmd_client,
    cmd_session,
    kind,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    if kind == "stale":
        newer = EventResearchScopeVersion(
            research_case_id=case_id,
            version=2,
            changed_by="human:researcher",
            change_summary="newer scope",
            created_at=scope.created_at,
        )
        cmd_session.add(newer)
        cmd_session.commit()
        requested_scope_id = scope.id
    else:
        _, foreign_scope = _create_event_case(cmd_client, cmd_session)
        requested_scope_id = foreign_scope.id

    response = _confirm(cmd_client, case_id, requested_scope_id, f"{kind}-scope")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 0
    assert _count(
        cmd_session,
        ResearchOrchestration,
        ResearchOrchestration.research_case_id == case_id,
    ) == 0


def test_empty_active_scope_is_rejected_without_writes(cmd_client, cmd_session):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    empty_scope = EventResearchScopeVersion(
        research_case_id=case_id,
        version=2,
        changed_by="human:researcher",
        change_summary="empty scope",
        created_at=scope.created_at,
    )
    cmd_session.add(empty_scope)
    cmd_session.commit()

    response = _confirm(cmd_client, case_id, empty_scope.id, "empty-scope")

    assert response.status_code == 422
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 0
    assert _count(
        cmd_session,
        ResearchOrchestration,
        ResearchOrchestration.research_case_id == case_id,
    ) == 0


def test_explicit_protocol_gate_rolls_back_preparation(cmd_client, cmd_session):
    case_id, scope = _create_event_case(
        cmd_client,
        cmd_session,
        research_protocol_required=True,
    )

    response = _confirm(cmd_client, case_id, scope.id, "blocked-by-protocol")

    assert response.status_code == 422
    assert "researchability gate blocked" in response.json()["error"]["message"]
    assert _count(
        cmd_session,
        ResearchRun,
        ResearchRun.research_case_id == case_id,
    ) == 0
    assert _count(
        cmd_session,
        ResearchOrchestration,
        ResearchOrchestration.research_case_id == case_id,
    ) == 0
    assert _count(cmd_session, ResearchOrchestrationEvent) == 0


def test_wrong_tenant_gets_not_found_safe_response(
    cmd_client,
    cmd_session,
    monkeypatch,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","intruder-token":"tenant-intruder"}',
    )

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        headers={"Authorization": "Bearer intruder-token"},
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": "intruder-confirm",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == (
        "event research case not found"
    )
    assert str(case_id) not in response.text
    assert _count(cmd_session, ResearchRun) == 0
    assert _count(cmd_session, ResearchOrchestration) == 0


def test_route_commits_once_on_success(cmd_client, cmd_session, monkeypatch):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    original_commit = cmd_session.commit
    original_rollback = cmd_session.rollback
    commits = 0
    rollbacks = 0

    def tracked_commit():
        nonlocal commits
        commits += 1
        return original_commit()

    def tracked_rollback():
        nonlocal rollbacks
        rollbacks += 1
        return original_rollback()

    monkeypatch.setattr(cmd_session, "commit", tracked_commit)
    monkeypatch.setattr(cmd_session, "rollback", tracked_rollback)

    response = _confirm(cmd_client, case_id, scope.id, "commit-once")

    assert response.status_code == 202
    assert (commits, rollbacks) == (1, 0)


def test_route_rolls_back_any_service_exception(
    cmd_client,
    cmd_session,
    monkeypatch,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    original_rollback = cmd_session.rollback
    rollbacks = 0

    def tracked_rollback():
        nonlocal rollbacks
        rollbacks += 1
        return original_rollback()

    def fail_start(*args, **kwargs):
        raise RuntimeError("prepared run persistence failed")

    monkeypatch.setattr(cmd_session, "rollback", tracked_rollback)
    monkeypatch.setattr("app.services.auto_research.AutoResearchService.start", fail_start)

    response = _confirm(cmd_client, case_id, scope.id, "rollback-on-error")

    assert response.status_code == 500
    assert rollbacks == 1
    assert _count(cmd_session, ResearchRun) == 0
    assert _count(cmd_session, ResearchOrchestration) == 0


def test_decision_route_rolls_back_service_exception_exactly_once(
    cmd_client,
    cmd_session,
    monkeypatch,
):
    case_id, scope = _create_event_case(cmd_client, cmd_session)
    confirmed = _confirm(cmd_client, case_id, scope.id, "confirm-before-decision-error")
    assert confirmed.status_code == 202, confirmed.text
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    assert orchestration is not None
    orchestration.state = "needs_scope_decision"
    cmd_session.commit()
    original_rollback = cmd_session.rollback
    rollbacks = 0

    def tracked_rollback():
        nonlocal rollbacks
        rollbacks += 1
        return original_rollback()

    def fail_decision(*args, **kwargs):
        raise RuntimeError("scope decision persistence failed")

    monkeypatch.setattr(cmd_session, "rollback", tracked_rollback)
    monkeypatch.setattr(
        "app.services.research_orchestration.ResearchOrchestrationService.decide_scope",
        fail_decision,
    )

    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/decision",
        json={
            "kind": "stop",
            "reason": "停止",
            "expected_version": orchestration.version,
            "idempotency_key": "decision-rollback-once",
        },
    )

    assert response.status_code == 500
    assert rollbacks == 1


def test_confirm_request_has_no_identity_or_run_scope_fields():
    from app.schemas.v1.research_workflow import ConfirmResearchWorkflowRequest

    assert set(ConfirmResearchWorkflowRequest.model_fields) == {
        "scope_version_id",
        "scope_version",
        "idempotency_key",
    }


def test_confirm_requires_authentication(client):
    response = client.post(
        f"/api/v1/event-research/{uuid.uuid4()}/workflow/confirm",
        json={
            "scope_version_id": str(uuid.uuid4()),
            "idempotency_key": "missing-auth",
        },
    )

    assert response.status_code == 401
