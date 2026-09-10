from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.errors import ValidationFailedError
from app.models.acquisition import AcquisitionAttempt, AcquisitionJob
from app.models.research_orchestration import (
    AcquisitionGoalCoverage,
    AcquisitionQueryPlan,
    AcquisitionSeries,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.services.research_acquisition import (
    ResearchAcquisitionService,
    _metric_terms_for_statement,
)
import app.services.research_acquisition as research_acquisition_module
from app.services.acquisition import AcquisitionModule
from app.services.research_orchestration import (
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
from app.repositories.research_orchestration import ResearchOrchestrationRepository


PRINCIPAL = OrchestrationPrincipal(
    tenant_id="test-team",
    actor="worker:research-orchestration",
)


def test_metric_terms_keep_the_thesis_and_add_grounded_financial_anchors():
    assert _metric_terms_for_statement("营业收入同比增长10.36%") == (
        "营业收入同比增长10.36%",
        "营业收入",
    )
    assert _metric_terms_for_statement(
        "归属于上市公司股东的净利润同比增长6.13%"
    ) == (
        "归属于上市公司股东的净利润同比增长6.13%",
        "归属于上市公司股东的净利润",
    )


def _typed_boundary_decision_context() -> dict:
    return {
        "action": "revise_frozen_research_boundary",
        "attempted_rounds": 1,
        "boundary_codes": ["metric_scope"],
        "affected_goals": [
            {"goal_id": "goal:support", "reason_codes": ["period_mismatch"]}
        ],
        "missing": [
            {"goal_id": "goal:support", "reason_codes": ["period_mismatch"]}
        ],
        "attempted": [
            {"goal_id": "goal:support", "search_completed": True}
        ],
        "cannot_continue_reason": "继续补证需要改变已冻结的研究边界。",
        "recommendation": {
            "kind": "revise_scope",
            "label": "调整研究边界",
            "impact": "创建新的冻结范围版本后，系统才能按新边界继续补证；现有运行与证据记录保持不变。",
        },
        "alternatives": [
            {
                "kind": "keep_scope",
                "label": "保持当前研究范围",
                "impact": "不改变已冻结边界，未解决目标继续明确保留为未知。",
            },
            {
                "kind": "stop",
                "label": "停止本次研究",
                "impact": "停止后续自动补证，并保留当前证据、缺口与运行记录。",
            },
        ],
    }


def test_repository_accepts_only_closed_typed_boundary_decision_context() -> None:
    payload = _typed_boundary_decision_context()

    ResearchOrchestrationRepository._validate_boundary_decision(payload)

    forged_payloads = [
        {**payload, "checkpoint_text": "trust me"},
        {**payload, "recommendation": {**payload["recommendation"], "kind": "retry"}},
        {
            **payload,
            "alternatives": [
                *payload["alternatives"],
                {"kind": "continue", "label": "继续", "impact": "忽略边界"},
            ],
        },
        {
            **payload,
            "affected_goals": [
                {"goal_id": "goal:support", "reason_codes": ["unit_mismatch"]}
            ],
        },
        {
            **payload,
            "boundary_codes": ["metric_scope", "unit_semantics"],
        },
    ]
    for forged in forged_payloads:
        with pytest.raises(ValidationFailedError, match="typed boundary decision"):
            ResearchOrchestrationRepository._validate_boundary_decision(forged)


def _create_confirmed_case(cmd_client, *, factors: list[str] | None = None):
    response = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "台积电上调先进封装产能计划。",
            "event_title": "CoWoS 产能指引更新",
            "company_name": "台积电",
            "ticker": "2330",
            "event_at": "2026-08-01T08:00:00Z",
            "research_question": "产能上调是否由可持续需求驱动？",
            "candidate_factors": factors
            or ["AI 订单增长", "先进封装收入兑现", "客户库存回补"],
        },
    )
    assert response.status_code == 201, response.text
    case_id = uuid.UUID(response.json()["case_id"])
    return case_id


def _confirm(cmd_client, cmd_session, case_id: uuid.UUID) -> ResearchOrchestration:
    from app.models.event_research import EventResearchScopeVersion

    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    response = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": f"confirm:{scope.id}",
        },
    )
    assert response.status_code == 202, response.text
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    assert orchestration is not None
    return orchestration


def test_dispatch_translates_frozen_scope_into_stable_complete_goal_set(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)

    result = ResearchAcquisitionService(cmd_session).dispatch_round(
        orchestration,
        principal=PRINCIPAL,
    )

    assert len(result.goals) == 7
    assert [goal.objective for goal in result.goals] == [
        "support",
        "contradict",
        "support",
        "contradict",
        "support",
        "contradict",
        "alternative_explanation",
    ]
    assert len({goal.goal_id for goal in result.goals}) == 7
    alternative = result.goals[-1]
    assert alternative.goal_id == f"event:{case_id}:alternative_explanation"
    assert alternative.thesis_id == result.goals[0].thesis_id

    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob).order_by(AcquisitionJob.created_at, AcquisitionJob.id)
        )
    )
    assert len(jobs) == 7
    assert all(job.request_snapshot["entity_names"] == ["台积电"] for job in jobs)
    assert all(job.request_snapshot["security_codes"] == ["2330"] for job in jobs)
    assert all(job.request_snapshot["period_start"] == "2025-08-01" for job in jobs)
    assert all(job.request_snapshot["period_end"] == "2026-08-01" for job in jobs)
    assert all(
        job.request_snapshot["cutoff"] == "2026-08-01T23:59:59.999999Z"
        for job in jobs
    )
    assert all(job.request_snapshot["round"] == 1 for job in jobs)
    assert all(
        job.request_snapshot["research_run_id"]
        == str(orchestration.current_research_run_id)
        for job in jobs
    )
    assert all(
        job.request_snapshot["scope_version_id"]
        == str(orchestration.current_scope_version_id)
        for job in jobs
    )
    assert all(
        job.request_snapshot["allowed_source_roles"]
        == ["company_disclosure", "licensed_provider"]
        for job in jobs
    )
    assert all(
        job.request_snapshot["source_policy_version"] == "b-scope-v2"
        and job.request_snapshot["planner_version"] == "goal-query-v1"
        for job in jobs
    )
    jobs_by_id = {job.id: job for job in jobs}
    for goal in result.goals[:-1]:
        job = jobs_by_id[goal.job_id]
        assert job.request_snapshot["metric_terms"] == [
            job.request_snapshot["thesis_statement"]
        ]
    assert jobs_by_id[alternative.job_id].request_snapshot["metric_terms"] == [
        "产能上调是否由可持续需求驱动？"
    ]
    assert all(
        job.idempotency_key
        == (
            f"run:{orchestration.current_research_run_id}:"
            f"scope:{orchestration.current_scope_version_id}:"
            f"goal:{job.request_snapshot['goal_id']}:round:1"
        )
        for job in jobs
    )
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def test_single_active_thesis_creates_two_thesis_goals_and_one_event_goal(
    cmd_session,
):
    from app.models.event_research import (
        EventResearchBrief,
        EventResearchScopeFactor,
        EventResearchScopeVersion,
    )
    from app.models.ledger import ResearchCase, Thesis
    from app.repositories.documents import DocumentRepository
    from app.services.case_tenant_access import CaseTenantAccess
    from app.services.ingest import DocumentService

    now = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    case = ResearchCase(
        title="单命题事件",
        industry_topic="事件研究",
        research_object="单命题公司",
        core_question="单一命题能否被验证？",
        evidence_cutoff=now.date(),
        created_by="human:researcher",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    document_service = DocumentService(DocumentRepository(cmd_session))
    document = document_service.freeze(
        raw="单命题事件原文".encode(),
        source_url="https://example.test/single-thesis",
    )
    document_service.attach_to_case(
        research_case_id=case.id,
        document_version_id=document.id,
    )
    CaseTenantAccess(cmd_session).admit_initial_case(
        case_id=case.id,
        tenant_id=PRINCIPAL.tenant_id,
        initial_document_version_id=document.id,
        admitted_by=PRINCIPAL.actor,
    )
    brief = EventResearchBrief(
        research_case_id=case.id,
        raw_input="单命题事件原文",
        source_url="https://example.test/single-thesis",
        source_type="public_url",
        source_metadata={},
        event_title="单命题事件",
        company_name="单命题公司",
        ticker="600001.SH",
        event_at=now,
        market_reaction=None,
        research_question="单一命题能否被验证？",
        extraction_state="human_confirmed",
        created_at=now,
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by=PRINCIPAL.actor,
        change_summary="single thesis scope",
        created_at=now,
    )
    thesis = Thesis(
        research_case_id=case.id,
        statement="订单增长可持续",
        research_protocol_required=False,
        created_at=now,
        created_by=PRINCIPAL.actor,
        creator_type="human",
        review_state="confirmed",
    )
    cmd_session.add_all([brief, scope, thesis])
    cmd_session.flush()
    cmd_session.add(
        EventResearchScopeFactor(
            scope_version_id=scope.id,
            statement=thesis.statement,
            description="订单同比增长",
            position=1,
        )
    )
    cmd_session.flush()
    orchestration = ResearchOrchestrationService(cmd_session).confirm_scope(
        case.id,
        PRINCIPAL,
        "confirm-single-thesis",
        scope_version_id=scope.id,
    )

    result = ResearchAcquisitionService(cmd_session).dispatch_round(
        orchestration,
        principal=PRINCIPAL,
    )

    assert [goal.objective for goal in result.goals] == [
        "support",
        "contradict",
        "alternative_explanation",
    ]
    assert {goal.thesis_id for goal in result.goals} == {thesis.id}


def test_orchestration_reconciliation_is_idempotent_and_advances_to_acquiring(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)

    first = service.reconcile(case_id, PRINCIPAL)
    second = service.reconcile(case_id, PRINCIPAL)

    assert first.state == "acquiring"
    assert second.state == "acquiring"
    goals = second.checkpoint_json["acquisition"]["goals"]
    assert len(goals) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7
    assert second.last_heartbeat_at is not None


def test_restart_replays_durable_dispatch_without_duplicate_jobs_or_plans(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    first = ResearchOrchestrationService(cmd_session).reconcile(case_id, PRINCIPAL)
    first_job_ids = [
        item["job_id"] for item in first.checkpoint_json["acquisition"]["goals"]
    ]
    cmd_session.commit()

    with Session(cmd_session.get_bind()) as restarted:
        resumed = ResearchOrchestrationService(restarted).reconcile(case_id, PRINCIPAL)
        restarted.commit()
        assert resumed.state == "acquiring"
        assert [
            item["job_id"]
            for item in resumed.checkpoint_json["acquisition"]["goals"]
        ] == first_job_ids
        assert restarted.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
        assert restarted.scalar(select(func.count()).select_from(AcquisitionSeries)) == 7
        assert restarted.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def test_nonterminal_jobs_keep_acquiring_without_enqueuing_synthesis(
    cmd_client, cmd_session
):
    from app.models.operational import Job

    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    version = acquiring.version

    current = service.reconcile(case_id, PRINCIPAL)

    assert current.state == "acquiring"
    assert current.version == version + 1
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run")
        )
        == 0
    )


def test_all_terminal_jobs_advance_only_to_assessing_coverage(
    cmd_client, cmd_session
):
    from app.models.operational import Job

    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    job_ids = [
        uuid.UUID(item["job_id"])
        for item in acquiring.checkpoint_json["acquisition"]["goals"]
    ]
    for job in cmd_session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.id.in_(job_ids))
    ):
        job.status = "succeeded"
        job.stage = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        plan = cmd_session.scalar(
            select(AcquisitionQueryPlan).where(
                AcquisitionQueryPlan.acquisition_job_id == job.id
            )
        )
        assert plan is not None
        for query_index, query in enumerate(plan.ordered_queries_json):
            cmd_session.add(
                AcquisitionAttempt(
                    job_id=job.id,
                    adapter_key=query["adapter_key"],
                    operation="search",
                    attempt_no=1,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    outcome="succeeded",
                    retryable=False,
                    safe_metadata={"query_index": query_index},
                )
            )
    cmd_session.flush()

    current = service.reconcile(case_id, PRINCIPAL)

    assert current.state == "assessing_coverage"
    assert current.current_system_action == "正在评估各资料目标的证据覆盖度"
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run")
        )
        == 0
    )


def test_coverage_reconcile_uses_one_batch_snapshot_seam(
    cmd_client, cmd_session, monkeypatch
) -> None:
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    job_ids = [
        uuid.UUID(item["job_id"])
        for item in acquiring.checkpoint_json["acquisition"]["goals"]
    ]
    for job in cmd_session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.id.in_(job_ids))
    ):
        job.status = "succeeded"
        job.stage = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        plan = cmd_session.scalar(
            select(AcquisitionQueryPlan).where(
                AcquisitionQueryPlan.acquisition_job_id == job.id
            )
        )
        assert plan is not None
        for query_index, query in enumerate(plan.ordered_queries_json):
            cmd_session.add(
                AcquisitionAttempt(
                    job_id=job.id,
                    adapter_key=query["adapter_key"],
                    operation="search",
                    attempt_no=1,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    outcome="succeeded",
                    retryable=False,
                    safe_metadata={"query_index": query_index},
                )
            )
    cmd_session.flush()
    assessing = service.reconcile(case_id, PRINCIPAL)
    assert assessing.state == "assessing_coverage"

    original_batch = AcquisitionModule.coverage_snapshots
    batch_calls: list[tuple[uuid.UUID, ...]] = []

    def observe_batch(self, requested_job_ids, *, principal):
        batch_calls.append(requested_job_ids)
        return original_batch(self, requested_job_ids, principal=principal)

    def fail_individual(*_args, **_kwargs):
        raise AssertionError("coverage reconcile must use the batch snapshot seam")

    monkeypatch.setattr(AcquisitionModule, "coverage_snapshots", observe_batch)
    monkeypatch.setattr(AcquisitionModule, "get", fail_individual)
    monkeypatch.setattr(AcquisitionModule, "query_plan", fail_individual)
    monkeypatch.setattr(AcquisitionModule, "coverage_search_operations", fail_individual)
    monkeypatch.setattr(AcquisitionModule, "coverage_evidence", fail_individual)

    service.reconcile(case_id, PRINCIPAL)

    assert len(batch_calls) == 1
    assert set(batch_calls[0]) == set(job_ids)


def test_coverage_continue_dispatches_only_unresolved_goals_once(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    injected_checkpoint = dict(acquiring.checkpoint_json)
    injected_checkpoint["required_boundary_changes"] = [
        "arbitrary-client-string"
    ]
    acquiring.checkpoint_json = injected_checkpoint
    first_goal_rows = acquiring.checkpoint_json["acquisition"]["goals"]
    first_job_ids = [uuid.UUID(item["job_id"]) for item in first_goal_rows]
    for job in cmd_session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.id.in_(first_job_ids))
    ):
        job.status = "succeeded"
        job.stage = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        plan = cmd_session.scalar(
            select(AcquisitionQueryPlan).where(
                AcquisitionQueryPlan.acquisition_job_id == job.id
            )
        )
        assert plan is not None
        for query_index, query in enumerate(plan.ordered_queries_json):
            cmd_session.add(
                AcquisitionAttempt(
                    job_id=job.id,
                    adapter_key=query["adapter_key"],
                    operation="search",
                    attempt_no=1,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    outcome="succeeded",
                    retryable=False,
                    safe_metadata={"query_index": query_index},
                )
            )
    cmd_session.flush()

    assessing = service.reconcile(case_id, PRINCIPAL)
    assessing_state = assessing.state
    planning = service.reconcile(case_id, PRINCIPAL)
    planning_state = planning.state
    pending_goal_ids = list(planning.checkpoint_json["pending_goal_ids"])
    second = service.reconcile(case_id, PRINCIPAL)

    assert assessing_state == "assessing_coverage"
    assert planning_state == "planning_acquisition"
    assert pending_goal_ids == [
        item["goal_id"] for item in first_goal_rows if item["objective"] == "support"
    ]
    coverage_rows = list(
        cmd_session.scalars(
            select(AcquisitionGoalCoverage).order_by(AcquisitionGoalCoverage.goal_id)
        )
    )
    assert len(coverage_rows) == len(first_goal_rows)
    assert {row.policy_version for row in coverage_rows} == {
        "event-goal-coverage-v1"
    }
    assert {
        row.goal_id for row in coverage_rows if row.status == "unmet"
    } == set(pending_goal_ids)
    assert all(row.evaluation_round == 1 for row in coverage_rows)
    assert second.state == "acquiring"
    second_goals = second.checkpoint_json["acquisition"]["goals"]
    assert [item["goal_id"] for item in second_goals] == pending_goal_ids
    assert all(item["objective"] == "support" for item in second_goals)
    second_job_ids = [uuid.UUID(item["job_id"]) for item in second_goals]
    plans = list(
        cmd_session.scalars(
            select(AcquisitionQueryPlan).where(
                AcquisitionQueryPlan.acquisition_job_id.in_(second_job_ids)
            )
        )
    )
    assert len(plans) == len(second_goals)
    assert all(plan.previous_query_plan_id is not None for plan in plans)
    assert all(plan.expansion_trigger == "coverage_unmet" for plan in plans)
    assert all(plan.diff_json["reason"].strip() for plan in plans)
    assert all(plan.diff_json["added_queries"] for plan in plans)
    assert all(plan.diff_json["removed_queries"] for plan in plans)

    job_count = cmd_session.scalar(select(func.count()).select_from(AcquisitionJob))
    cmd_session.commit()
    sessions = sessionmaker(bind=cmd_session.get_bind(), future=True)
    with sessions() as reopened:
        replay = ResearchOrchestrationService(reopened).reconcile(
            case_id, PRINCIPAL
        )
        assert replay.state == "acquiring"
        assert (
            reopened.scalar(select(func.count()).select_from(AcquisitionJob))
            == job_count
        )


def test_corrupt_checkpoint_fails_closed_with_explicit_event(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    acquiring.checkpoint_json = {
        **acquiring.checkpoint_json,
        "acquisition": {"round": 1, "goals": "not-a-list"},
        "secret": "do-not-persist",
    }
    cmd_session.flush()

    current = service.reconcile(case_id, PRINCIPAL)

    assert current.state == "failed"
    assert current.recovery_status == "failed"
    event = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == current.id,
            ResearchOrchestrationEvent.transition
            == "acquisition_reconciliation_failed",
        )
    )
    assert event is not None
    assert event.payload_json["reason_code"] == "ValidationFailedError"
    assert "do-not-persist" not in json.dumps(
        {
            "checkpoint": current.checkpoint_json,
            "event": event.payload_json,
        },
        sort_keys=True,
    )
    assert current.checkpoint_json["failure_summary"]["reason_code"] == (
        "ValidationFailedError"
    )

    replay = service.reconcile(case_id, PRINCIPAL)
    assert replay.state == "failed"
    assert cmd_session.scalar(
        select(func.count())
        .select_from(ResearchOrchestrationEvent)
        .where(
            ResearchOrchestrationEvent.orchestration_id == current.id,
            ResearchOrchestrationEvent.transition
            == "acquisition_reconciliation_failed",
        )
    ) == 1


def test_cross_case_checkpointed_job_fails_closed(cmd_client, cmd_session):
    first_case = _create_confirmed_case(cmd_client)
    second_case = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, first_case)
    _confirm(cmd_client, cmd_session, second_case)
    service = ResearchOrchestrationService(cmd_session)
    first = service.reconcile(first_case, PRINCIPAL)
    second = service.reconcile(second_case, PRINCIPAL)
    first_goals = deepcopy(first.checkpoint_json["acquisition"]["goals"])
    first_goals[0]["job_id"] = second.checkpoint_json["acquisition"]["goals"][0][
        "job_id"
    ]
    first.checkpoint_json = {
        **first.checkpoint_json,
        "acquisition": {"round": 1, "goals": first_goals},
    }
    cmd_session.flush()

    current = service.reconcile(first_case, PRINCIPAL)

    assert current.state == "failed"


def test_cross_case_current_scope_pointer_is_cleared_and_fails_closed(
    cmd_client, cmd_session
):
    from app.models.event_research import EventResearchScopeVersion

    first_case = _create_confirmed_case(cmd_client)
    second_case = _create_confirmed_case(cmd_client)
    first = _confirm(cmd_client, cmd_session, first_case)
    second_scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == second_case
        )
    )
    assert second_scope is not None
    first.current_scope_version_id = second_scope.id
    cmd_session.flush()

    current = ResearchOrchestrationService(cmd_session).reconcile(
        first_case, PRINCIPAL
    )

    assert current.state == "failed"
    assert current.current_scope_version_id is None
    assert current.recovery_status == "failed"


def test_acquisition_bridge_depends_only_on_public_acquisition_module():
    source = inspect.getsource(research_acquisition_module)

    assert "repositories.acquisition" not in source
    assert "acquisition.sources" not in source
    assert "datasources.exchanges" not in source
    assert "AcquisitionModule" in source


def _recovery_events(cmd_session, orchestration_id):
    return list(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id == orchestration_id,
                ResearchOrchestrationEvent.transition.in_(
                    (
                        "acquisition_recovery_started",
                        "recovery_started",
                        "recovery_completed",
                    )
                ),
            )
            .order_by(ResearchOrchestrationEvent.sequence)
        )
    )


def test_stale_planning_recovery_completes_before_dispatch_without_duplicates(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)
    original_checkpoint = deepcopy(orchestration.checkpoint_json)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    service = ResearchOrchestrationService(cmd_session)

    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    assert orchestration.state == "recovering"
    assert service.complete_acquisition_recoveries(now=now, limit=10) == 1
    assert orchestration.state == "planning_acquisition"
    assert orchestration.checkpoint_json == original_checkpoint
    current = service.reconcile(case_id, PRINCIPAL)

    assert current.state == "acquiring"
    assert [event.transition for event in _recovery_events(cmd_session, current.id)] == [
        "acquisition_recovery_started",
        "recovery_completed",
    ]
    recovery_events = _recovery_events(cmd_session, current.id)
    started_payload = recovery_events[0].payload_json
    completed_payload = recovery_events[1].payload_json
    assert started_payload["episode_id"] == completed_payload["episode_id"]
    assert started_payload["resume_state"] == "planning_acquisition"
    assert completed_payload["resume_state"] == "planning_acquisition"
    assert recovery_events[0].idempotency_key.endswith(":started")
    assert recovery_events[1].idempotency_key.endswith(":completed")
    assert "checkpoint" not in started_payload
    assert "job_ids" not in started_payload
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def test_stale_acquiring_recovery_reuses_existing_jobs_series_and_plans(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    acquiring = service.reconcile(case_id, PRINCIPAL)
    now = datetime.now(timezone.utc)
    original_checkpoint = deepcopy(acquiring.checkpoint_json)
    original_job_ids = [
        item["job_id"] for item in original_checkpoint["acquisition"]["goals"]
    ]
    acquiring.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()

    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    assert service.complete_acquisition_recoveries(now=now, limit=10) == 1
    resumed = service.reconcile(case_id, PRINCIPAL)

    assert resumed.state == "acquiring"
    assert resumed.checkpoint_json == original_checkpoint
    assert [
        item["job_id"] for item in resumed.checkpoint_json["acquisition"]["goals"]
    ] == original_job_ids
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 7
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def test_fresh_acquisition_workflow_does_not_start_recovery(cmd_client, cmd_session):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)

    count = ResearchOrchestrationService(
        cmd_session
    ).start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    )

    assert count == 0
    assert orchestration.state == "planning_acquisition"
    assert _recovery_events(cmd_session, orchestration.id) == []


def test_restart_completes_persisted_recovery_episode_without_second_start(
    cmd_client, cmd_session
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    assert ResearchOrchestrationService(
        cmd_session
    ).start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    orchestration_id = orchestration.id
    cmd_session.commit()

    with Session(cmd_session.get_bind()) as restarted:
        service = ResearchOrchestrationService(restarted)
        assert service.start_stale_acquisition_recoveries(
            now=now + timedelta(minutes=1),
            stale_before=now - timedelta(hours=1),
            limit=10,
        ) == 0
        assert service.complete_acquisition_recoveries(
            now=now + timedelta(minutes=1), limit=10
        ) == 1
        service.reconcile(case_id, PRINCIPAL)
        restarted.commit()

    with Session(cmd_session.get_bind()) as check:
        events = _recovery_events(check, orchestration_id)
        assert [event.transition for event in events] == [
            "acquisition_recovery_started",
            "recovery_completed",
        ]
        assert len({event.idempotency_key for event in events}) == 2
        assert check.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
        assert check.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def _prepare_stale_acquiring(cmd_client, cmd_session):
    case_id = _create_confirmed_case(cmd_client)
    _confirm(cmd_client, cmd_session, case_id)
    service = ResearchOrchestrationService(cmd_session)
    orchestration = service.reconcile(case_id, PRINCIPAL)
    assert orchestration.state == "acquiring"
    return case_id, orchestration, service


@pytest.mark.parametrize(
    "corruption",
    [
        "goal-order",
        "goal-id",
        "missing-job",
        "cross-case-job",
        "stale-run",
        "stale-scope",
        "frozen-binding",
    ],
)
def test_acquiring_recovery_completes_only_after_full_public_binding_validation(
    cmd_client,
    cmd_session,
    corruption,
):
    _, orchestration, service = _prepare_stale_acquiring(
        cmd_client,
        cmd_session,
    )
    checkpoint = deepcopy(orchestration.checkpoint_json)
    goals = checkpoint["acquisition"]["goals"]
    first_job_id = uuid.UUID(goals[0]["job_id"])
    other = None
    if corruption in {"cross-case-job", "stale-run", "stale-scope"}:
        _, other, _ = _prepare_stale_acquiring(cmd_client, cmd_session)

    if corruption == "goal-order":
        goals[0], goals[1] = goals[1], goals[0]
    elif corruption == "goal-id":
        goals[0]["goal_id"] = "do-not-persist"
    elif corruption == "missing-job":
        goals[0]["job_id"] = str(uuid.uuid4())
    elif corruption == "cross-case-job":
        goals[0]["job_id"] = other.checkpoint_json["acquisition"]["goals"][0][
            "job_id"
        ]
    elif corruption == "stale-run":
        job = cmd_session.get(AcquisitionJob, first_job_id)
        assert job is not None and other is not None
        job.research_run_id = other.current_research_run_id
    elif corruption == "stale-scope":
        job = cmd_session.get(AcquisitionJob, first_job_id)
        assert job is not None and other is not None
        snapshot = dict(job.request_snapshot)
        snapshot["scope_version_id"] = str(other.current_scope_version_id)
        job.request_snapshot = snapshot
    else:
        job = cmd_session.get(AcquisitionJob, first_job_id)
        assert job is not None
        snapshot = dict(job.request_snapshot)
        snapshot["metric_terms"] = ["do-not-persist"]
        job.request_snapshot = snapshot

    orchestration.checkpoint_json = checkpoint
    now = datetime.now(timezone.utc)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()

    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) >= 1
    assert orchestration.state == "recovering"
    assert service.complete_acquisition_recoveries(now=now, limit=10) >= 1

    assert orchestration.state == "failed"
    events = _recovery_events(cmd_session, orchestration.id)
    assert [event.transition for event in events] == [
        "acquisition_recovery_started",
    ]
    failure = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration.id,
            ResearchOrchestrationEvent.transition == "recovery_failed",
        )
    )
    assert failure is not None
    assert failure.payload_json["reason_code"] == "acquisition_binding_invalid"


def test_missing_mutable_marker_resumes_from_immutable_acquisition_start_event(
    cmd_client,
    cmd_session,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    original_checkpoint = deepcopy(orchestration.checkpoint_json)
    now = datetime.now(timezone.utc)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    service = ResearchOrchestrationService(cmd_session)
    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    checkpoint = dict(orchestration.checkpoint_json)
    checkpoint.pop("_acquisition_recovery")
    orchestration.checkpoint_json = checkpoint
    cmd_session.flush()

    assert service.complete_acquisition_recoveries(now=now, limit=10) == 1

    assert orchestration.state == "planning_acquisition"
    assert orchestration.checkpoint_json == original_checkpoint
    assert [event.transition for event in _recovery_events(cmd_session, orchestration.id)] == [
        "acquisition_recovery_started",
        "recovery_completed",
    ]


def test_missing_marker_with_unrecoverable_checkpoint_fails_from_start_history(
    cmd_client,
    cmd_session,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    service = ResearchOrchestrationService(cmd_session)
    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    orchestration.checkpoint_json = ["do-not-persist"]
    cmd_session.flush()

    assert service.complete_acquisition_recoveries(now=now, limit=10) == 1

    _assert_safe_recovery_failure(
        cmd_session,
        orchestration,
        reason_code="checkpoint_not_object",
    )


def test_generic_recovering_without_acquisition_start_history_is_not_selected(
    cmd_client,
    cmd_session,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    orchestration.state = "recovering"
    orchestration.recovery_status = "recovering"
    orchestration.checkpoint_json = {}
    cmd_session.flush()

    assert ResearchOrchestrationService(
        cmd_session
    ).complete_acquisition_recoveries(
        now=datetime.now(timezone.utc),
        limit=10,
    ) == 0

    assert orchestration.state == "recovering"
    assert _recovery_events(cmd_session, orchestration.id) == []


def _assert_safe_recovery_failure(
    cmd_session,
    orchestration: ResearchOrchestration,
    *,
    reason_code: str,
) -> None:
    assert orchestration.state == "failed"
    assert orchestration.recovery_status == "failed"
    assert orchestration.last_heartbeat_at is not None
    event = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration.id,
            ResearchOrchestrationEvent.transition == "recovery_failed",
        )
    )
    assert event is not None
    assert event.payload_json["reason_code"] == reason_code
    persisted = json.dumps(
        {
            "checkpoint": orchestration.checkpoint_json,
            "event": event.payload_json,
        },
        sort_keys=True,
    )
    assert "do-not-persist" not in persisted


@pytest.mark.parametrize(
    ("checkpoint", "reason_code"),
    [
        pytest.param(["do-not-persist"], "checkpoint_not_object", id="malformed"),
        pytest.param({}, "checkpoint_context_missing", id="missing"),
    ],
)
def test_stale_planning_corrupt_checkpoint_fails_closed_safely(
    cmd_client,
    cmd_session,
    checkpoint,
    reason_code,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)
    orchestration.checkpoint_json = checkpoint
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()

    processed = ResearchOrchestrationService(
        cmd_session
    ).start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    )

    assert processed == 1
    _assert_safe_recovery_failure(
        cmd_session,
        orchestration,
        reason_code=reason_code,
    )


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        pytest.param(
            "reserved-marker",
            "recovery_marker_collision",
            id="reserved-marker-collision",
        ),
        pytest.param(
            "stale-scope",
            "checkpoint_context_mismatch",
            id="stale-scope-binding",
        ),
        pytest.param(
            "stale-run",
            "checkpoint_context_mismatch",
            id="stale-run-binding",
        ),
    ],
)
def test_stale_planning_invalid_recovery_context_fails_closed(
    cmd_client,
    cmd_session,
    mutation,
    reason_code,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    checkpoint = dict(orchestration.checkpoint_json)
    if mutation == "reserved-marker":
        checkpoint["_acquisition_recovery"] = {
            "secret": "do-not-persist",
        }
    elif mutation == "stale-scope":
        checkpoint["scope_version_id"] = str(uuid.uuid4())
    else:
        checkpoint["research_run_id"] = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    orchestration.checkpoint_json = checkpoint
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()

    assert ResearchOrchestrationService(
        cmd_session
    ).start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1

    _assert_safe_recovery_failure(
        cmd_session,
        orchestration,
        reason_code=reason_code,
    )


@pytest.mark.parametrize(
    ("replacement", "reason_code"),
    [
        pytest.param("do-not-persist", "recovery_marker_not_object", id="scalar"),
        pytest.param(
            {
                "episode_id": "a" * 24,
                "resume_state": "do-not-persist",
                "checkpoint_digest": "b" * 16,
            },
            "recovery_resume_state_invalid",
            id="bad-resume-state",
        ),
        pytest.param(
            {
                "resume_state": "planning_acquisition",
                "checkpoint_digest": "b" * 16,
            },
            "recovery_episode_id_invalid",
            id="missing-episode",
        ),
        pytest.param(
            {
                "episode_id": "do-not-persist",
                "resume_state": "planning_acquisition",
                "checkpoint_digest": "b" * 16,
            },
            "recovery_episode_id_invalid",
            id="bad-episode",
        ),
        pytest.param(
            {
                "episode_id": "a" * 24,
                "resume_state": "planning_acquisition",
                "checkpoint_digest": "do-not-persist",
            },
            "recovery_checkpoint_digest_mismatch",
            id="digest-mismatch",
        ),
    ],
)
def test_every_malformed_recovery_marker_is_selected_and_failed_closed(
    cmd_client,
    cmd_session,
    replacement,
    reason_code,
):
    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    now = datetime.now(timezone.utc)
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    service = ResearchOrchestrationService(cmd_session)
    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1
    checkpoint = dict(orchestration.checkpoint_json)
    checkpoint["_acquisition_recovery"] = replacement
    orchestration.checkpoint_json = checkpoint
    cmd_session.flush()

    processed = service.complete_acquisition_recoveries(now=now, limit=10)

    assert processed == 1
    _assert_safe_recovery_failure(
        cmd_session,
        orchestration,
        reason_code=reason_code,
    )


def test_corrupt_candidate_does_not_abort_later_healthy_recovery(
    cmd_client,
    cmd_session,
):
    corrupt_case_id = _create_confirmed_case(cmd_client)
    healthy_case_id = _create_confirmed_case(cmd_client)
    corrupt = _confirm(cmd_client, cmd_session, corrupt_case_id)
    healthy = _confirm(cmd_client, cmd_session, healthy_case_id)
    now = datetime.now(timezone.utc)
    corrupt.checkpoint_json = {"secret": "do-not-persist"}
    corrupt.last_heartbeat_at = now - timedelta(hours=3)
    healthy.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()
    service = ResearchOrchestrationService(cmd_session)

    assert service.start_stale_acquisition_recoveries(
        now=now,
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 2
    assert corrupt.state == "failed"
    assert healthy.state == "recovering"
    assert service.complete_acquisition_recoveries(now=now, limit=10) == 1
    assert service.reconcile_batch(
        now=now + timedelta(minutes=1),
        stale_before=now - timedelta(hours=1),
        limit=10,
    ) == 1

    assert healthy.state == "acquiring"
    assert cmd_session.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
    _assert_safe_recovery_failure(
        cmd_session,
        corrupt,
        reason_code="checkpoint_context_missing",
    )


def test_recovery_failure_transition_rolls_back_atomically_on_crash(
    cmd_client,
    cmd_session,
    monkeypatch,
):
    import app.repositories.research_orchestration as repository_module

    case_id = _create_confirmed_case(cmd_client)
    orchestration = _confirm(cmd_client, cmd_session, case_id)
    corrupt_checkpoint = {"secret": "do-not-persist"}
    now = datetime.now(timezone.utc)
    orchestration.checkpoint_json = corrupt_checkpoint
    orchestration.last_heartbeat_at = now - timedelta(hours=2)
    cmd_session.flush()

    def crash_during_event(*_args, **_kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(repository_module, "emit_event", crash_during_event)

    with pytest.raises(RuntimeError, match="simulated crash"):
        ResearchOrchestrationService(
            cmd_session
        ).start_stale_acquisition_recoveries(
            now=now,
            stale_before=now - timedelta(hours=1),
            limit=10,
        )

    cmd_session.expire_all()
    persisted = cmd_session.get(ResearchOrchestration, orchestration.id)
    assert persisted is not None
    assert persisted.state == "planning_acquisition"
    assert persisted.checkpoint_json == corrupt_checkpoint
    assert _recovery_events(cmd_session, persisted.id) == []
