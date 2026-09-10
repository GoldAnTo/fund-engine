"""Automatic event-research completion from ready coverage to monitoring."""

from __future__ import annotations

import uuid
import inspect
from datetime import UTC, datetime, timedelta

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
)
from app.models.ledger import AtomicClaimReview, Thesis
from app.models.operational import EventResearchLifecycle, Job, ResearchTask
from app.models.proposals import Proposal
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent
from app.models.research_orchestration import (
    AcquisitionQueryPlan,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.services.event_research_scope_evidence import (
    append_automatic_scope_evidence_assignment,
    validated_automatic_run_evidence,
)
from app.services.event_conclusion import EventConclusionService
from app.services.auto_research import AutoResearchService, SynthesisRetryPolicy
from app.services.case_monitor import (
    CaseMonitorConfig,
    CaseMonitorService,
    ResearchRunEventRepository,
)
from app.errors import NotFoundError, ValidationFailedError
import pytest
from app.services.research_orchestration import (
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
import app.services.research_orchestration as research_orchestration_module
from app.queries.event_research import EventResearchQueries
from tests.test_event_research_scope import _automatically_admitted_evidence
from tests.test_research_orchestration_service import _seed_case


TENANT = "test-tenant"
PRINCIPAL = OrchestrationPrincipal(
    tenant_id=TENANT,
    actor="worker:research-orchestration",
)
NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def test_orchestration_uses_only_the_typed_auto_research_service_seam() -> None:
    source = inspect.getsource(research_orchestration_module)
    assert "auto_research.repo" not in source
    assert "AutoResearchService(self._session).repo" not in source
    for method in (
        "synthesis_status",
        "reconcile_synthesis_failure",
        "cancel_for_scope_replacement",
    ):
        assert callable(getattr(AutoResearchService, method))


def _seed_ready_automatic_case(session):
    case, scope = _seed_case(session, tenant_id=TENANT)
    session.add(
        EventResearchBrief(
            research_case_id=case.id,
            raw_input="fixture event",
            source_url="https://example.test/event",
            source_type="pasted_snapshot",
            source_metadata={},
            event_title="Fixture event",
            company_name="Fixture Co",
            ticker="FIX",
            event_at=NOW,
            market_reaction="down",
            research_question="why",
            extraction_state="confirmed",
            created_at=NOW,
        )
    )
    session.flush()
    factor = session.scalar(
        select(EventResearchScopeFactor.statement).where(
            EventResearchScopeFactor.scope_version_id == scope.id
        )
    )
    thesis = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case.id,
            Thesis.statement == factor,
        )
    )
    assert factor is not None and thesis is not None
    goal_id = f"thesis:{thesis.id}:support"
    link, decision, run = _automatically_admitted_evidence(
        session,
        case.id,
        factor,
        goal_id=goal_id,
    )
    append_automatic_scope_evidence_assignment(
        session,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=run.id,
        goal_id=goal_id,
        evidence_link_id=link.id,
        automatic_admission_decision_id=decision.id,
        factor_statement=factor,
        provenance={"policy_version": "event-goal-coverage-v1"},
        created_at=NOW,
    )
    orchestration = ResearchOrchestration(
        tenant_id=TENANT,
        research_case_id=case.id,
        current_scope_version_id=scope.id,
        current_research_run_id=run.id,
        state="assessing_coverage",
        user_stage="acquisition",
        current_system_action="资料目标覆盖评估已完成",
        system_action_reason="所有必要资料目标均满足冻结覆盖策略",
        checkpoint_json={
            "scope_version_id": str(scope.id),
            "research_run_id": str(run.id),
            "acquisition_round": 1,
            "coverage_decision": {
                "decision": "ready",
                "policy_version": "event-goal-coverage-v1",
                "acquisition_round": 1,
                "unresolved_goal_ids": [],
                "unknown_goal_ids": [],
                "independent_source_identities": ["sse:fixture"],
                "zero_new_independent_source_rounds": 0,
            },
        },
        next_action_kind=None,
        next_action_label=None,
        next_action_payload=None,
        last_heartbeat_at=NOW,
        recovery_status="healthy",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(orchestration)
    session.commit()
    return case, scope, thesis, link, run, orchestration


def _seed_ready_case_without_evidence(session):
    case, scope = _seed_case(session, tenant_id=TENANT)
    thesis = session.scalar(select(Thesis).where(Thesis.research_case_id == case.id))
    assert thesis is not None
    run = AutoResearchService(session).start(
        case.id,
        thesis_ids=[thesis.id],
        enqueue=False,
        commit=False,
    )
    orchestration = ResearchOrchestration(
        tenant_id=TENANT,
        research_case_id=case.id,
        current_scope_version_id=scope.id,
        current_research_run_id=run.id,
        state="assessing_coverage",
        user_stage="acquisition",
        current_system_action="资料目标覆盖评估已完成",
        system_action_reason="反证检索完成，但没有因素获得支持证据",
        checkpoint_json={
            "scope_version_id": str(scope.id),
            "research_run_id": str(run.id),
            "acquisition_round": 1,
            "coverage_decision": {
                "decision": "ready",
                "policy_version": "event-goal-coverage-v1",
                "acquisition_round": 1,
                "unresolved_goal_ids": [],
                "unknown_goal_ids": [],
                "independent_source_identities": [],
                "zero_new_independent_source_rounds": 1,
            },
        },
        last_heartbeat_at=NOW,
        recovery_status="healthy",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(orchestration)
    session.commit()
    return case, scope, thesis, run, orchestration


def _append_same_run_event_alternative(
    session,
    *,
    case,
    scope,
    thesis,
    run,
    role="contextualizes",
):
    goal_id = f"event:{case.id}:alternative_explanation"
    link, decision, seeded_run = _automatically_admitted_evidence(
        session,
        case.id,
        thesis.statement,
        goal_id=goal_id,
        role=role,
        research_run=run,
    )
    assert seeded_run.id == run.id
    append_automatic_scope_evidence_assignment(
        session,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=run.id,
        goal_id=goal_id,
        evidence_link_id=link.id,
        automatic_admission_decision_id=decision.id,
        factor_statement=None,
        provenance={
            "policy_version": "event-goal-coverage-v1",
            "mapping_scope": "event",
        },
        created_at=NOW,
    )
    return link


def _record_orchestrated_synthesis_receipt(
    session,
    *,
    run,
    scope,
    orchestration,
    evidence_link_ids,
) -> None:
    ResearchRunEventRepository(session).append(
        run.id,
        stage="scope",
        status="completed",
        message="已冻结本次运行范围",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "orchestration_id": str(orchestration.id),
        },
    )
    canonical_ids = sorted(str(item) for item in evidence_link_ids)
    job = session.scalar(
        select(Job).where(Job.kind == "research_run", Job.target_id == run.id)
    )
    assert job is not None
    job.status = "succeeded"
    job.step = "complete"
    job.attempt = max(1, job.attempt)
    tasks = list(
        session.scalars(select(ResearchTask).where(ResearchTask.run_id == run.id))
    )
    if not tasks:
        task = ResearchTask(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=uuid.UUID(run.scope_thesis_ids[0]),
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="fixture synthesis receipt",
            evidence_count=len(canonical_ids),
            result={
                "task_type": "result",
                "execution_mode": "orchestrated_evidence_synthesis",
                "scope_version_id": str(scope.id),
                "evidence_link_ids": canonical_ids,
            },
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(task)
        tasks = [task]
    for task in tasks:
        task.status = "done"
        task.stage = "completed"
        task.evidence_count = len(canonical_ids)
        task.result = {
            "task_type": task.task_type,
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "job_id": str(job.id),
            "job_attempt": job.attempt,
            "evidence_link_ids": canonical_ids,
        }
        task.updated_at = NOW
    run.status = "succeeded"
    run.stage = "complete"
    run.stop_reason = "orchestrated_evidence_synthesized"
    ResearchRunEventRepository(session).append(
        run.id,
        stage="complete",
        status="completed",
        message="当前研究范围内已准入证据归并完成",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "job_id": str(job.id),
            "job_attempt": job.attempt,
            "evidence_link_ids": canonical_ids,
            "evidence_count": len(canonical_ids),
        },
    )
    session.flush()


def test_ready_automatic_evidence_completes_once_without_fabricating_review(
    cmd_session,
) -> None:
    case, scope, thesis, link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    clock = [NOW]
    service = ResearchOrchestrationService(cmd_session, clock=lambda: clock[0])

    first = service.reconcile(case.id, PRINCIPAL)
    replay = service.reconcile(case.id, PRINCIPAL)

    assert first.id == replay.id == orchestration.id
    assert replay.state == "synthesizing_evidence"
    assert run.status == "queued"
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run", Job.target_id == run.id)
        )
        == 1
    )

    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[link.id],
    )
    service.reconcile_research_run_completion(
        run.id,
        actor="worker:research-run:test",
    )
    for _ in range(3):
        service.reconcile(case.id, PRINCIPAL)
    cmd_session.commit()

    drafts = list(
        cmd_session.scalars(
            select(EventResearchConclusion).where(
                EventResearchConclusion.research_case_id == case.id,
                EventResearchConclusion.state == "ai_draft",
            )
        )
    )
    monitors = list(
        cmd_session.scalars(
            select(CaseMonitorVersion).where(
                CaseMonitorVersion.research_case_id == case.id,
                CaseMonitorVersion.status == "active",
            )
        )
    )
    transitions = list(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id
            )
        )
    )

    assert orchestration.state == "monitoring"
    assert len(drafts) == 1
    assert drafts[0].scope_version_id == scope.id
    assert drafts[0].reviewer is None
    assert drafts[0].evidence_link_ids == [str(link.id)]
    assert drafts[0].primary_factor == thesis.statement
    assert len(monitors) == 1
    assert monitors[0].factor_ids == [str(thesis.id)]
    assert transitions.count("report_generated") == 1
    assert transitions.count("monitoring_started") == 1
    assert cmd_session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0
    assignment = cmd_session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == link.id
        )
    )
    assert assignment is not None
    assert assignment.assignment_kind == "automatic"
    history = EventResearchQueries(cmd_session).conclusion_history(case.id)
    version = history.versions[0]
    assert version.system_generated is True
    assert version.human_reviewed is False
    assert version.review_label == "系统生成，未经人工审核"
    workbench = EventResearchQueries(cmd_session).workbench(case.id)
    assert workbench.conclusion.state == "ai_draft"
    assert workbench.conclusion.system_generated is True
    assert workbench.conclusion.human_reviewed is False
    assert workbench.conclusion.review_label == "系统生成，未经人工审核"
    assert [citation.review_state for citation in workbench.conclusion.citations] == [
        "automatically_admitted"
    ]

    published = EventConclusionService(cmd_session).publish(
        case.id,
        text="人工确认后的结论",
        reviewer="human:owner",
    )
    assert published.state == "published"
    assert published.reviewer == "human:owner"


def test_waiting_for_human_review_never_advances_orchestration_synthesis(
    cmd_session,
) -> None:
    case, _scope, _thesis, _link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    run.status = "waiting_for_review"
    run.stage = "claim_review"
    cmd_session.flush()

    still_synthesizing = service.reconcile(case.id, PRINCIPAL)

    assert still_synthesizing.id == orchestration.id
    assert still_synthesizing.state == "synthesizing_evidence"
    assert "evidence_synthesized" not in set(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id
            )
        )
    )


def test_succeeded_run_without_matching_synthesis_receipt_never_advances(
    cmd_session,
) -> None:
    case, scope, _thesis, _link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="已冻结本次运行范围",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "orchestration_id": str(orchestration.id),
        },
    )
    run.status = "succeeded"
    run.stage = "complete"
    run.stop_reason = "no_new_evidence"
    cmd_session.flush()

    unchanged = service.reconcile(case.id, PRINCIPAL)

    assert unchanged.state == "synthesizing_evidence"
    assert "evidence_synthesized" not in set(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id
            )
        )
    )


def test_synthesis_receipt_must_equal_persisted_task_consumption(
    cmd_session,
) -> None:
    case, scope, _thesis, link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[],
    )
    task = cmd_session.scalar(select(ResearchTask).where(ResearchTask.run_id == run.id))
    assert task is not None
    task.result = {
        **task.result,
        "evidence_link_ids": [str(link.id)],
    }
    task.evidence_count = 1
    cmd_session.flush()

    unchanged = service.reconcile(case.id, PRINCIPAL)

    assert unchanged.state == "synthesizing_evidence"


def test_synthesis_receipt_requires_the_same_completed_job_attempt(
    cmd_session,
) -> None:
    case, scope, _thesis, link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[link.id],
    )
    job = cmd_session.scalar(
        select(Job).where(Job.kind == "research_run", Job.target_id == run.id)
    )
    assert job is not None
    job.status = "queued"
    job.step = "queued"
    job.attempt += 1
    cmd_session.flush()

    unchanged = service.reconcile(case.id, PRINCIPAL)

    assert unchanged.state == "synthesizing_evidence"


def test_orchestrated_evidence_synthesis_completes_from_frozen_assignments(
    cmd_session,
) -> None:
    case, scope, thesis, link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    foreign_link, foreign_decision, foreign_run = _automatically_admitted_evidence(
        cmd_session,
        case.id,
        thesis.statement,
        goal_id=f"thesis:{thesis.id}:foreign-run-support",
    )
    append_automatic_scope_evidence_assignment(
        cmd_session,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=foreign_run.id,
        goal_id=f"thesis:{thesis.id}:foreign-run-support",
        evidence_link_id=foreign_link.id,
        automatic_admission_decision_id=foreign_decision.id,
        factor_statement=thesis.statement,
        provenance={"policy_version": "event-goal-coverage-v1"},
        created_at=NOW,
    )
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="已冻结本次运行范围",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "orchestration_id": str(_orchestration.id),
            "factor_ids": [str(thesis.id)],
        },
    )
    for task_type in ("support", "contradict", "result", "alternative"):
        cmd_session.add(
            ResearchTask(
                run_id=run.id,
                research_case_id=case.id,
                thesis_id=thesis.id,
                status="queued",
                stage="planned",
                round=1,
                task_type=task_type,
                query=f"{task_type}: {thesis.statement}",
                evidence_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    orchestration_service = ResearchOrchestrationService(cmd_session)
    assert orchestration_service.reconcile(case.id, PRINCIPAL).state == (
        "synthesizing_evidence"
    )
    service = AutoResearchService(cmd_session)
    claim = service.claim_next_run_job()
    assert claim is not None
    cmd_session.commit()

    service.execute(run, claim=claim)

    assert (run.status, run.stage, run.stop_reason) == (
        "succeeded",
        "complete",
        "orchestrated_evidence_synthesized",
    )
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(AtomicClaimReview)
            .where(AtomicClaimReview.reviewer.is_not(None))
        )
        == 0
    )
    assert cmd_session.scalar(select(func.count()).select_from(Proposal)) == 0
    completed_tasks = list(
        cmd_session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .order_by(ResearchTask.task_type)
        )
    )
    assert {task.status for task in completed_tasks} == {"done"}
    assert all(
        task.result["execution_mode"] == "orchestrated_evidence_synthesis"
        for task in completed_tasks
    )
    evidence_ids_by_task = {
        task.task_type: task.result["evidence_link_ids"] for task in completed_tasks
    }
    assert evidence_ids_by_task["support"] == [str(link.id)]
    assert evidence_ids_by_task["contradict"] == []
    assert evidence_ids_by_task["result"] == [str(link.id)]
    assert evidence_ids_by_task["alternative"] == []
    assert service.record_job_completion(
        claim,
        status=run.status,
        step=run.stage,
        run=run,
    )
    service.reconcile_orchestration_after_worker(
        run.id,
        actor="worker:research-run:test",
    )
    cmd_session.commit()

    draft = cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id,
            EventResearchConclusion.state == "ai_draft",
        )
    )
    assert draft is not None
    assert draft.evidence_link_ids == [str(link.id)]
    assert str(foreign_link.id) not in draft.evidence_link_ids


def test_same_run_event_alternative_flows_through_synthesis_and_report(
    cmd_session,
) -> None:
    case, scope, thesis, support, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    alternative = _append_same_run_event_alternative(
        cmd_session,
        case=case,
        scope=scope,
        thesis=thesis,
        run=run,
    )

    snapshot = validated_automatic_run_evidence(
        cmd_session,
        tenant_id=TENANT,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=run.id,
    )
    assert {item.link.id for item in snapshot} == {support.id, alternative.id}
    event_item = next(item for item in snapshot if item.link.id == alternative.id)
    assert (
        event_item.assignment.acquisition_goal_id,
        event_item.assignment.disposition,
        event_item.assignment.factor_statement,
        event_item.assignment.automatic_provenance_json["mapping_scope"],
    ) == (
        f"event:{case.id}:alternative_explanation",
        "unmapped",
        None,
        "event",
    )

    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="已冻结本次运行范围",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "orchestration_id": str(orchestration.id),
            "factor_ids": [str(thesis.id)],
        },
    )
    for task_type in ("support", "contradict", "result", "alternative"):
        cmd_session.add(
            ResearchTask(
                run_id=run.id,
                research_case_id=case.id,
                thesis_id=thesis.id,
                status="queued",
                stage="planned",
                round=1,
                task_type=task_type,
                query=f"{task_type}: {thesis.statement}",
                evidence_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    assert (
        ResearchOrchestrationService(cmd_session).reconcile(case.id, PRINCIPAL).state
        == "synthesizing_evidence"
    )
    service = AutoResearchService(cmd_session)
    claim = service.claim_next_run_job()
    assert claim is not None
    cmd_session.commit()

    service.execute(run, claim=claim)

    completed_tasks = list(
        cmd_session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .order_by(ResearchTask.task_type)
        )
    )
    evidence_ids_by_task = {
        task.task_type: task.result["evidence_link_ids"] for task in completed_tasks
    }
    assert evidence_ids_by_task["support"] == [str(support.id)]
    assert evidence_ids_by_task["contradict"] == []
    assert evidence_ids_by_task["alternative"] == [str(alternative.id)]
    assert set(evidence_ids_by_task["result"]) == {
        str(support.id),
        str(alternative.id),
    }
    completion = cmd_session.scalar(
        select(ResearchRunEvent)
        .where(
            ResearchRunEvent.run_id == run.id,
            ResearchRunEvent.stage == "complete",
            ResearchRunEvent.status == "completed",
        )
        .order_by(ResearchRunEvent.seq.desc())
    )
    assert completion is not None
    assert set(completion.payload_json["evidence_link_ids"]) == {
        str(support.id),
        str(alternative.id),
    }
    assert service.record_job_completion(
        claim,
        status=run.status,
        step=run.stage,
        run=run,
    )
    service.reconcile_orchestration_after_worker(
        run.id,
        actor="worker:research-run:test",
    )
    cmd_session.commit()

    draft = cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id,
            EventResearchConclusion.state == "ai_draft",
        )
    )
    assert draft is not None
    assert set(draft.evidence_link_ids) == {str(support.id), str(alternative.id)}


def test_orchestrated_worker_completes_a_real_empty_evidence_receipt(
    cmd_session,
) -> None:
    case, scope, thesis, run, orchestration = _seed_ready_case_without_evidence(
        cmd_session
    )
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="已冻结本次运行范围",
        payload_json={
            "execution_mode": "orchestrated_evidence_synthesis",
            "scope_version_id": str(scope.id),
            "orchestration_id": str(orchestration.id),
            "factor_ids": [str(thesis.id)],
        },
    )
    orchestration_service = ResearchOrchestrationService(cmd_session)
    assert orchestration_service.reconcile(case.id, PRINCIPAL).state == (
        "synthesizing_evidence"
    )
    service = AutoResearchService(cmd_session)
    claim = service.claim_next_run_job()
    assert claim is not None
    cmd_session.commit()

    service.execute(run, claim=claim)
    assert service.record_job_completion(
        claim,
        status=run.status,
        step=run.stage,
        run=run,
    )
    service.reconcile_orchestration_after_worker(
        run.id,
        actor="worker:research-run:test",
    )
    cmd_session.commit()

    assert (run.status, run.stage) == ("succeeded", "complete")
    completion = cmd_session.scalar(
        select(CaseMonitorVersion).where(CaseMonitorVersion.research_case_id == case.id)
    )
    draft = cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id,
            EventResearchConclusion.state == "ai_draft",
        )
    )
    assert orchestration.state == "monitoring"
    assert completion is not None
    assert draft is not None
    assert draft.evidence_link_ids == []
    assert draft.primary_factor is None


def test_explicit_synthesis_receipt_never_reuses_a_different_existing_draft(
    cmd_session,
) -> None:
    case, scope, _thesis, link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    stale = EventResearchConclusion(
        research_case_id=case.id,
        scope_version_id=scope.id,
        state="ai_draft",
        text="stale fixture",
        primary_factor=None,
        evidence_link_ids=[],
        based_on_conclusion_id=None,
        reviewer=None,
        created_at=NOW,
    )
    cmd_session.add(stale)
    cmd_session.flush()

    with pytest.raises(ValidationFailedError, match="existing draft"):
        EventConclusionService(cmd_session).create_draft(
            case.id,
            system_generated=True,
            evidence_link_ids=[link.id],
            research_run_id=run.id,
            tenant_id=TENANT,
        )


def test_strict_automatic_snapshot_rejects_corrupt_admission_lineage(
    cmd_session,
) -> None:
    case, scope, _thesis, link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    decision = cmd_session.get(
        AutomaticAdmissionDecision,
        link.automatic_admission_decision_id,
    )
    assert decision is not None
    decision.outcome = "quarantined"

    with (
        cmd_session.no_autoflush,
        pytest.raises(
            ValidationFailedError,
            match="lineage is inconsistent",
        ),
    ):
        validated_automatic_run_evidence(
            cmd_session,
            tenant_id=TENANT,
            case_id=case.id,
            scope_version_id=scope.id,
            research_run_id=run.id,
        )


def test_strict_automatic_snapshot_rejects_job_and_policy_drift(
    cmd_session,
) -> None:
    case, scope, _thesis, link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    assignment = cmd_session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == link.id
        )
    )
    decision = cmd_session.get(
        AutomaticAdmissionDecision,
        link.automatic_admission_decision_id,
    )
    assert assignment is not None and decision is not None
    job = cmd_session.get(AcquisitionJob, decision.job_id)
    plan = cmd_session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == decision.job_id
        )
    )
    assert job is not None and plan is not None

    def read_snapshot():
        return validated_automatic_run_evidence(
            cmd_session,
            tenant_id=TENANT,
            case_id=case.id,
            scope_version_id=scope.id,
            research_run_id=run.id,
        )

    with cmd_session.no_autoflush:
        job.stage = "failed"
        with pytest.raises(ValidationFailedError, match="lineage is inconsistent"):
            read_snapshot()
        job.stage = "succeeded"
        plan.policy_version = "corrupt-plan-policy"
        with pytest.raises(ValidationFailedError, match="lineage is inconsistent"):
            read_snapshot()
        plan.policy_version = job.policy_snapshot["version"]
        decision.policy_version = "corrupt-decision-policy"
        with pytest.raises(ValidationFailedError, match="lineage is inconsistent"):
            read_snapshot()
        decision.policy_version = job.policy_snapshot["version"]
        assignment.automatic_provenance_json = {
            **assignment.automatic_provenance_json,
            "policy_version": "corrupt-policy",
        }
        with pytest.raises(ValidationFailedError, match="lineage is inconsistent"):
            read_snapshot()


def test_strict_automatic_snapshot_requires_case_tenant_admission(
    cmd_session,
) -> None:
    case, scope, thesis, run, _orchestration = _seed_ready_case_without_evidence(
        cmd_session
    )
    wrong_tenant = "consistently-corrupted-tenant"
    goal_id = f"thesis:{thesis.id}:support"
    link, decision, seeded_run = _automatically_admitted_evidence(
        cmd_session,
        case.id,
        thesis.statement,
        goal_id=goal_id,
        research_run=run,
        tenant_id=wrong_tenant,
    )
    assert seeded_run.id == run.id
    append_automatic_scope_evidence_assignment(
        cmd_session,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=run.id,
        goal_id=goal_id,
        evidence_link_id=link.id,
        automatic_admission_decision_id=decision.id,
        factor_statement=thesis.statement,
        provenance={"policy_version": "event-goal-coverage-v1"},
        created_at=NOW,
    )

    with pytest.raises(ValidationFailedError, match="tenant admission"):
        validated_automatic_run_evidence(
            cmd_session,
            tenant_id=wrong_tenant,
            case_id=case.id,
            scope_version_id=scope.id,
            research_run_id=run.id,
        )


def test_strict_automatic_snapshot_rejects_non_contextual_event_alternative(
    cmd_session,
) -> None:
    case, scope, thesis, _support, run, _orchestration = (
        _seed_ready_automatic_case(cmd_session)
    )
    _append_same_run_event_alternative(
        cmd_session,
        case=case,
        scope=scope,
        thesis=thesis,
        run=run,
        role="supports",
    )

    with pytest.raises(ValidationFailedError, match="lineage is inconsistent"):
        validated_automatic_run_evidence(
            cmd_session,
            tenant_id=TENANT,
            case_id=case.id,
            scope_version_id=scope.id,
            research_run_id=run.id,
        )


def test_strict_automatic_snapshot_uses_constant_query_count(cmd_session) -> None:
    case, scope, thesis, _link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    _append_same_run_event_alternative(
        cmd_session,
        case=case,
        scope=scope,
        thesis=thesis,
        run=run,
    )
    case_id, scope_id, run_id = case.id, scope.id, run.id
    cmd_session.expire_all()
    selects: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    sqlalchemy_event.listen(
        cmd_session.bind,
        "before_cursor_execute",
        count_selects,
    )
    try:
        snapshot = validated_automatic_run_evidence(
            cmd_session,
            tenant_id=TENANT,
            case_id=case_id,
            scope_version_id=scope_id,
            research_run_id=run_id,
        )
    finally:
        sqlalchemy_event.remove(
            cmd_session.bind,
            "before_cursor_execute",
            count_selects,
        )

    assert len(snapshot) == 2
    assert len(selects) <= 4


def test_ready_without_supported_factor_creates_insufficient_system_draft(
    cmd_session,
) -> None:
    case, scope, _thesis, run, orchestration = _seed_ready_case_without_evidence(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[],
    )

    service.reconcile_research_run_completion(
        run.id,
        actor="worker:research-run:test",
    )
    for _ in range(3):
        service.reconcile(case.id, PRINCIPAL)

    draft = cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id,
            EventResearchConclusion.state == "ai_draft",
        )
    )
    assert orchestration.state == "monitoring"
    assert draft is not None
    assert draft.primary_factor is None
    assert draft.evidence_link_ids == []
    assert "没有任何因素获得充分证据支持" in draft.text
    assert "系统生成，未经人工审核" in draft.text


def test_monitoring_system_draft_is_not_projected_as_published(
    cmd_client, cmd_session, monkeypatch
) -> None:
    case, scope, _thesis, link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    clock = [NOW]
    service = ResearchOrchestrationService(cmd_session, clock=lambda: clock[0])
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[link.id],
    )
    service.reconcile_research_run_completion(
        run.id,
        actor="worker:research-run:test",
    )
    for _ in range(3):
        service.reconcile(case.id, PRINCIPAL)
    cmd_session.commit()

    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    workbench = EventResearchQueries(cmd_session).workbench(case.id)
    assert orchestration.state == "monitoring"
    assert lifecycle is not None
    assert lifecycle.status == "draft_ready"
    assert workbench.lifecycle.status == "draft_ready"
    assert workbench.conclusion.state == "ai_draft"
    assert workbench.conclusion.review_label == "系统生成，未经人工审核"
    assert lifecycle.next_human_action is None
    assert workbench.next_action.kind == "wait"
    assert workbench.next_action.label == "当前无需操作"
    assert workbench.event.next_action_kind == "wait"

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":{"tenant_id":"test-tenant","actor_id":"publisher-7","roles":[]}}',
    )
    response = cmd_client.get(f"/api/v1/event-research/{case.id}/workbench")
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"]["status"] == "draft_ready"
    assert response.json()["conclusion"]["state"] == "ai_draft"
    assert response.json()["next_action"] == {
        "kind": "wait",
        "label": "当前无需操作",
        "count": None,
    }
    assert response.json()["conclusion"]["review_label"] == "系统生成，未经人工审核"

    forged = cmd_client.post(
        f"/api/v1/event-research/{case.id}/conclusion/publish",
        json={
            "text": "人工审核后发布的结论",
            "reviewer": "human:forged-owner",
        },
    )
    assert forged.status_code == 422, forged.text
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(EventResearchConclusion)
            .where(
                EventResearchConclusion.research_case_id == case.id,
                EventResearchConclusion.state == "published",
            )
        )
        == 0
    )

    published = cmd_client.post(
        f"/api/v1/event-research/{case.id}/conclusion/publish",
        headers={"X-Reviewer": "human:forged-header"},
        json={"text": "人工审核后发布的结论"},
    )
    assert published.status_code == 201, published.text
    assert cmd_session.get(EventResearchLifecycle, case.id).status == "published"
    stored = cmd_session.get(
        EventResearchConclusion,
        uuid.UUID(published.json()["conclusion_id"]),
    )
    assert stored is not None
    assert stored.reviewer == "user:publisher-7"


def test_other_run_event_level_evidence_is_excluded_from_draft_snapshot(
    cmd_session,
) -> None:
    case, scope, thesis, support, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    goal_id = f"event:{case.id}:alternative_explanation"
    alternative, decision, alternative_run = _automatically_admitted_evidence(
        cmd_session,
        case.id,
        thesis.statement,
        goal_id=goal_id,
        role="contextualizes",
    )
    append_automatic_scope_evidence_assignment(
        cmd_session,
        case_id=case.id,
        scope_version_id=scope.id,
        research_run_id=alternative_run.id,
        goal_id=goal_id,
        evidence_link_id=alternative.id,
        automatic_admission_decision_id=decision.id,
        factor_statement=None,
        provenance={
            "policy_version": "event-goal-coverage-v1",
            "mapping_scope": "event",
        },
        created_at=NOW,
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[support.id],
    )
    service.reconcile_research_run_completion(
        run.id,
        actor="worker:research-run:test",
    )

    draft = cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id,
            EventResearchConclusion.state == "ai_draft",
        )
    )
    assert draft is not None
    assert draft.evidence_link_ids == [str(support.id)]
    assert str(alternative.id) not in draft.evidence_link_ids


def test_failed_synthesis_is_requeued_and_recovered_without_duplicate_side_effects(
    cmd_session,
) -> None:
    case, _scope, _thesis, _link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    clock = [NOW]
    service = ResearchOrchestrationService(cmd_session, clock=lambda: clock[0])
    service.reconcile(case.id, PRINCIPAL)
    job = cmd_session.scalar(
        select(Job).where(Job.kind == "research_run", Job.target_id == run.id)
    )
    assert job is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "task_failed"
    job.status = "running"
    cmd_session.flush()

    still_terminalizing = service.reconcile(case.id, PRINCIPAL)

    assert still_terminalizing.state == "synthesizing_evidence"
    assert job.status == "running"
    job.status = "failed"
    job.step = "failed"
    job.failure_count = 1
    cmd_session.flush()

    retry_wait = service.reconcile(case.id, PRINCIPAL)
    still_waiting = service.reconcile(case.id, PRINCIPAL)
    assert still_waiting.state == "retry_wait"
    assert run.status == "failed"
    assert job.status == "failed"
    clock[0] = NOW + timedelta(minutes=1)
    recovered = service.reconcile(case.id, PRINCIPAL)

    assert retry_wait.id == recovered.id == orchestration.id
    assert recovered.state == "synthesizing_evidence"
    assert run.status == "queued"
    assert job.status == "queued"
    assert job.attempt == 2
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run", Job.target_id == run.id)
        )
        == 1
    )


def test_exhausted_synthesis_failure_never_becomes_a_report_or_monitor(
    cmd_session,
) -> None:
    case, _scope, _thesis, _link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    service = ResearchOrchestrationService(cmd_session)
    service.reconcile(case.id, PRINCIPAL)
    job = cmd_session.scalar(
        select(Job).where(Job.kind == "research_run", Job.target_id == run.id)
    )
    assert job is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "task_failed"
    job.status = "failed"
    job.step = "failed"
    # Provider failures, unlike stale-worker lease recovery, consume this
    # persistent bounded retry budget.
    job.failure_count = 3
    cmd_session.commit()

    for _ in range(5):
        service.reconcile(case.id, PRINCIPAL)
        cmd_session.commit()

    cmd_session.refresh(run)
    cmd_session.refresh(job)
    cmd_session.refresh(orchestration)
    assert run.status == "failed"
    assert job.status == "failed"
    assert job.failure_count == 3
    assert job.next_retry_at is None
    assert orchestration.state in {"synthesizing_evidence", "retry_wait", "failed"}
    transitions = set(
        cmd_session.scalars(
            select(ResearchOrchestrationEvent.transition).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id
            )
        )
    )
    assert "evidence_synthesized" not in transitions
    assert "report_generated" not in transitions
    assert "monitoring_started" not in transitions
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(EventResearchConclusion)
            .where(EventResearchConclusion.research_case_id == case.id)
        )
        == 0
    )
    assert (
        cmd_session.scalar(
            select(func.count())
            .select_from(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case.id)
        )
        == 0
    )


def test_synthesis_retry_backoff_is_persistent_and_due_time_is_clock_driven(
    cmd_session,
) -> None:
    case, _scope, _thesis, _link, run, _orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    ResearchOrchestrationService(cmd_session).reconcile(case.id, PRINCIPAL)
    job = cmd_session.scalar(
        select(Job).where(Job.kind == "research_run", Job.target_id == run.id)
    )
    assert job is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "task_failed"
    job.status = "failed"
    job.step = "failed"
    job.failure_count = 1
    cmd_session.commit()
    policy = SynthesisRetryPolicy(
        version="test-retry-v1",
        max_failures=3,
        base_delay=timedelta(minutes=1),
        max_delay=timedelta(minutes=5),
    )

    scheduled = AutoResearchService(
        cmd_session, clock=lambda: NOW
    ).reconcile_synthesis_failure(run.id, policy=policy, commit=True)

    assert scheduled.state == "scheduled"
    assert job.status == "failed"
    assert run.status == "failed"
    assert job.next_retry_at is not None
    assert job.next_retry_at.replace(tzinfo=UTC) == NOW + timedelta(minutes=1)
    assert job.retry_policy_version == "test-retry-v1"
    original_attempt = job.attempt

    # A freshly constructed service simulates a worker/API restart. The
    # database timestamp, not process memory, remains authoritative.
    with Session(cmd_session.bind) as restarted:
        early = AutoResearchService(
            restarted, clock=lambda: NOW + timedelta(seconds=59)
        ).reconcile_synthesis_failure(run.id, policy=policy, commit=True)
    cmd_session.expire_all()
    assert early.state == "scheduled"
    assert job.status == "failed"
    assert job.attempt == original_attempt

    with Session(cmd_session.bind) as restarted:
        due = AutoResearchService(
            restarted, clock=lambda: NOW + timedelta(minutes=1)
        ).reconcile_synthesis_failure(run.id, policy=policy, commit=True)
    with Session(cmd_session.bind) as restarted_again:
        duplicate = AutoResearchService(
            restarted_again, clock=lambda: NOW + timedelta(minutes=2)
        ).reconcile_synthesis_failure(run.id, policy=policy, commit=True)
    cmd_session.expire_all()
    assert due.state == duplicate.state == "queued"
    assert run.status == "queued"
    assert job.status == "queued"
    assert job.attempt == original_attempt + 1
    assert job.next_retry_at is None


def test_existing_active_monitor_is_reused_and_wrong_tenant_is_rejected(
    cmd_session,
) -> None:
    case, scope, thesis, link, run, orchestration = _seed_ready_automatic_case(
        cmd_session
    )
    existing = CaseMonitorService(cmd_session).save(
        case.id,
        actor="human:owner",
        config=CaseMonitorConfig(
            frequency="weekly",
            factor_ids=[thesis.id],
            allowed_source_types=sorted(B_SCOPE_POLICY.allowed_source_roles),
            next_verification_event="下一次财报",
            budget=25,
            change_reason="人工已配置",
        ),
    )
    service = ResearchOrchestrationService(cmd_session)
    with pytest.raises(NotFoundError):
        service.reconcile(
            case.id,
            OrchestrationPrincipal("other-tenant", "worker:intruder"),
        )
    unrelated_run = AutoResearchService(cmd_session).start(
        case.id,
        thesis_ids=[thesis.id],
        enqueue=False,
        commit=False,
    )
    with pytest.raises(ValidationFailedError, match="not owned"):
        service.reconcile_research_run_completion(
            unrelated_run.id,
            actor="worker:research-run:test",
        )
    service.reconcile(case.id, PRINCIPAL)
    _record_orchestrated_synthesis_receipt(
        cmd_session,
        run=run,
        scope=scope,
        orchestration=orchestration,
        evidence_link_ids=[link.id],
    )
    service.reconcile_research_run_completion(
        run.id,
        actor="worker:research-run:test",
    )
    for _ in range(3):
        service.reconcile(case.id, PRINCIPAL)

    monitors = list(
        cmd_session.scalars(
            select(CaseMonitorVersion).where(
                CaseMonitorVersion.research_case_id == case.id
            )
        )
    )
    assert orchestration.state == "monitoring"
    assert [monitor.id for monitor in monitors] == [existing.id]
