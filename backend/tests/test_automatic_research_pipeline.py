from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY
from app.models.acquisition import AcquisitionJob
from app.models.event_research import EventResearchBrief
from app.models.ledger import CaseTenantAdmission, DocumentVersion, ResearchCase, Thesis
from app.models.operational import Job, JobEvent, ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.repositories.auto_research import AutoResearchRepository
from app.services.case_monitor import ResearchRunEventRepository


def _automatic_run(
    session, *, factor: str = "需求增长", plan_mutation=None
) -> ResearchRun:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="需求事件",
        industry_topic="事件研究",
        created_by="tenant:team-a",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="event://automatic-test",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add_all([case, document])
    session.flush()
    session.add_all(
        [
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id="team-a",
                initial_document_version_id=document.id,
                admitted_by="tenant:team-a",
                admitted_at=now,
            ),
            EventResearchBrief(
                research_case_id=case.id,
                raw_input="公司披露需求与订单变化。",
                source_type="pasted_snapshot",
                event_title="需求事件",
                company_name="示例公司",
                ticker="600000",
                research_question="需求能否持续增长？",
                workflow_mode="automatic",
                extraction_state="system_generated",
                created_at=now,
            ),
        ]
    )
    thesis = Thesis(
        research_case_id=case.id,
        statement=factor,
        created_by="system:automatic-intake",
        creator_type="ai",
        review_state="draft",
        created_at=now,
    )
    session.add(thesis)
    session.flush()
    repo = AutoResearchRepository(session)
    run = repo.create_run(
        research_case_id=case.id,
        scope_thesis_ids=[str(thesis.id)],
    )
    scope_payload = {
        "workflow_mode": "automatic",
        "factor_ids": [str(thesis.id)],
        "factor_statements": [factor],
        "automatic_protocol": {"factors": [factor]},
        "automatic_evidence_plan": {
            "items": [
                {
                    "factor": factor,
                    "objectives": [
                        "support",
                        "contradict",
                        "alternative_explanation",
                    ],
                    "allowed_source_roles": [
                        "company_disclosure",
                        "licensed_provider",
                    ],
                }
            ]
        },
    }
    if plan_mutation is not None:
        plan_mutation(scope_payload)
    ResearchRunEventRepository(session).append(
        run.id,
        stage="scope",
        status="completed",
        message="scope frozen",
        payload_json=scope_payload,
    )
    for task_type in ("support", "contradict", "result", "alternative"):
        repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type=task_type,
            query=f"{task_type}: {factor}",
        )
    repo.enqueue_run_job(run)
    return run


def _scope_event(session, run: ResearchRun) -> ResearchRunEvent:
    event = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run.id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    return event


def test_dispatch_sources_queues_three_frozen_idempotent_jobs(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    old_task_time = datetime.now(timezone.utc) - timedelta(days=1)
    for task in session.scalars(
        select(ResearchTask)
        .where(ResearchTask.run_id == run.id)
        .where(ResearchTask.task_type != "result")
    ):
        task.updated_at = old_task_time
    first = AutomaticResearchPipeline(session).dispatch_sources(run)
    second = AutomaticResearchPipeline(session).dispatch_sources(run)

    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert first == second == "waiting_for_sources"
    assert run.round == 1
    assert len(jobs) == 3
    assert {job.request_snapshot["objective"] for job in jobs} == {
        "support",
        "contradict",
        "alternative_explanation",
    }
    assert {job.research_run_id for job in jobs} == {run.id}
    assert all(job.request_snapshot["cutoff"] for job in jobs)
    assert all(job.request_snapshot["round"] == 1 for job in jobs)
    assert all(
        job.idempotency_key
        == (
            f"automatic:{run.id}:1:{job.thesis_id}:"
            f"{job.request_snapshot['objective']}"
        )
        for job in jobs
    )
    assert all(job.request_snapshot["entity_names"] == ["示例公司"] for job in jobs)
    assert all(job.request_snapshot["security_codes"] == ["600000"] for job in jobs)
    assert all(job.request_snapshot["metric_terms"] == ["需求增长"] for job in jobs)
    assert all(
        job.request_snapshot["allowed_source_roles"]
        == sorted(B_SCOPE_POLICY.allowed_source_roles)
        for job in jobs
    )
    assert {
        (job.request_snapshot["objective"], job.request_snapshot["target_link_role"])
        for job in jobs
    } == {
        ("support", "supports"),
        ("contradict", "contradicts"),
        ("alternative_explanation", "contextualizes"),
    }
    assert all(task.stage == "acquire" for task in tasks)
    assert all(
        task.updated_at.replace(tzinfo=None) > old_task_time.replace(tzinfo=None)
        for task in tasks
    )
    assert all(task.result and task.result["acquisition_job_id"] for task in tasks)
    assert {task.result["objective"] for task in tasks if task.result} == {
        "support",
        "contradict",
        "alternative_explanation",
    }
    waiting_events = list(
        session.scalars(
            select(ResearchRunEvent).where(
                ResearchRunEvent.run_id == run.id,
                ResearchRunEvent.stage == "retrieve",
                ResearchRunEvent.status == "waiting",
            )
        )
    )
    assert len(waiting_events) == 1


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda scope: scope.pop("automatic_evidence_plan"), "evidence plan"),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"factor": "不匹配因素"}
            ),
            "factor",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"objectives": ["support", "unknown"]}
            ),
            "objective",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"objectives": ["support", "contradict"]}
            ),
            "objective missing",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"allowed_source_roles": ["uploaded_file"]}
            ),
            "source roles",
        ),
    ],
)
def test_dispatch_sources_fails_closed_for_invalid_frozen_plan(
    session, mutation, message: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, plan_mutation=mutation)

    with pytest.raises(ValueError, match=message):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rolls_back_all_requests_when_later_request_fails(
    session, monkeypatch
) -> None:
    from app.services.acquisition import AcquisitionModule
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    original_request = AcquisitionModule.request
    request_count = 0

    def fail_second_request(self, request, *, principal):
        nonlocal request_count
        request_count += 1
        if request_count == 2:
            raise RuntimeError("injected second acquisition failure")
        return original_request(self, request, principal=principal)

    monkeypatch.setattr(AcquisitionModule, "request", fail_second_request)

    with pytest.raises(RuntimeError, match="second acquisition failure"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert all(task.result is None and task.stage == "planned" for task in tasks)
    assert session.scalar(
        select(func.count())
        .select_from(ResearchRunEvent)
        .where(
            ResearchRunEvent.run_id == run.id,
            ResearchRunEvent.stage == "retrieve",
            ResearchRunEvent.status == "waiting",
        )
    ) == 0
    assert run.status == "queued" and run.stage == "planning"


def test_dispatch_sources_rejects_missing_required_task_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "contradict",
        )
    )
    assert task is not None
    task.status = "cancelled"

    with pytest.raises(ValueError, match="task matrix"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_extra_objective_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    thesis_id = uuid.UUID((run.scope_thesis_ids or [])[0])
    AutoResearchRepository(session).create_task(
        run_id=run.id,
        research_case_id=run.research_case_id,
        thesis_id=thesis_id,
        task_type="verify_rule",
        query="unexpected objective",
    )

    with pytest.raises(ValueError, match="task matrix"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_thesis_outside_frozen_scope(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    outside = Thesis(
        research_case_id=run.research_case_id,
        statement="范围外因素",
        created_by="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(outside)
    session.flush()
    AutoResearchRepository(session).create_task(
        run_id=run.id,
        research_case_id=run.research_case_id,
        thesis_id=outside.id,
        task_type="support",
        query="outside frozen scope",
    )

    with pytest.raises(ValueError, match="task matrix|frozen scope"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_cross_case_task_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    other_case = ResearchCase(
        title="other",
        industry_topic="other",
        created_by="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(other_case)
    session.flush()
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "support",
        )
    )
    assert task is not None
    task.research_case_id = other_case.id

    with pytest.raises(ValueError, match="task matrix|run case"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_zero_queued_source_tasks(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    for task in session.scalars(
        select(ResearchTask)
        .where(ResearchTask.run_id == run.id)
        .where(ResearchTask.task_type != "result")
    ):
        task.status = "cancelled"

    with pytest.raises(ValueError, match="task matrix|no acquisition"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_does_not_resurrect_a_stale_cancelled_run(
    tmp_path,
) -> None:
    from app.models.ledger import Base
    from app.services.auto_research import AutoResearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'cancel-before-dispatch.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as setup:
        run = _automatic_run(setup)
        run_id = run.id
        setup.commit()

    worker = session_local()
    try:
        stale_run = worker.get(ResearchRun, run_id)
        assert stale_run is not None and stale_run.status == "queued"
        with session_local() as cancelling:
            AutoResearchService(cancelling).cancel_run(
                run_id,
                actor="human:test",
                change_reason="cancel before dispatch lock",
            )

        with pytest.raises(ValueError, match="no longer dispatchable"):
            AutoResearchService(worker).execute(stale_run)
        worker.rollback()
    finally:
        worker.close()

    with Session(engine) as check:
        persisted = check.get(ResearchRun, run_id)
        assert persisted is not None and persisted.status == "cancelled"
        persisted_job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert persisted_job is not None and persisted_job.status == "cancelled"
        assert check.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
        cancelled_tasks = list(
            check.scalars(select(ResearchTask).where(ResearchTask.run_id == run_id))
        )
        assert all(task.result is None for task in cancelled_tasks)
        assert all(task.stage == "stopped" for task in cancelled_tasks)
        assert check.scalar(
            select(func.count())
            .select_from(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == run_id,
                ResearchRunEvent.stage == "retrieve",
                ResearchRunEvent.status == "waiting",
            )
        ) == 0


@pytest.mark.pg_only
def test_postgres_cancel_committed_in_other_session_wins_before_dispatch_lock(
    session,
) -> None:
    from app.services.auto_research import AutoResearchService

    run = _automatic_run(session)
    run_id = run.id
    session.commit()
    session_local = sessionmaker(bind=session.get_bind(), future=True)
    worker = session_local()
    try:
        stale_run = worker.get(ResearchRun, run_id)
        assert stale_run is not None and stale_run.status == "queued"
        with session_local() as cancelling:
            AutoResearchService(cancelling).cancel_run(
                run_id,
                actor="human:pg-test",
                change_reason="committed before dispatch lock",
            )
        with pytest.raises(ValueError, match="no longer dispatchable"):
            AutoResearchService(worker).execute(stale_run)
        worker.rollback()
    finally:
        worker.close()

    assert session.get(ResearchRun, run_id).status == "cancelled"
    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


@pytest.mark.parametrize(
    "last_terminal_status",
    ["succeeded", "partial", "failed", "cancelled"],
)
def test_wait_for_sources_and_requeue_only_after_all_linked_jobs_terminal(
    session, last_terminal_status: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    AutomaticResearchPipeline(session).dispatch_sources(run)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None

    repo.wait_for_sources(run, research_job)
    source_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    assert run.status == "waiting_for_sources"
    assert run.stage == "retrieve"
    assert research_job.status == "waiting_for_sources"
    assert research_job.step == "retrieve"
    assert research_job.finished_at is None
    parked_event = session.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == research_job.id)
        .order_by(JobEvent.seq.desc())
        .limit(1)
    )
    assert parked_event is not None
    assert parked_event.message == "waiting for governed acquisition jobs"
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 1

    for source_job in source_jobs[:-1]:
        source_job.status = "succeeded"
    source_jobs[-1].status = "running"
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 1

    source_jobs[-1].status = last_terminal_status
    assert repo.requeue_source_ready_runs() == 1
    assert run.status == "queued" and run.stage == "analyze"
    assert research_job.status == "queued" and research_job.step == "analyze"
    assert research_job.attempt == 2
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 2
    latest = session.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == research_job.id)
        .order_by(JobEvent.seq.desc())
        .limit(1)
    )
    assert latest is not None
    assert latest.status == "queued" and latest.step == "analyze"
    assert latest.message == "sources ready"


def test_requeue_source_ready_runs_keeps_job_waiting_when_no_sources_exist(session) -> None:
    run = _automatic_run(session)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None
    repo.wait_for_sources(run, research_job)

    assert repo.requeue_source_ready_runs() == 0
    assert run.status == "waiting_for_sources"
    assert research_job.status == "waiting_for_sources"
    assert research_job.attempt == 1


def test_requeued_source_ready_job_gets_a_fresh_stale_recovery_clock(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    AutomaticResearchPipeline(session).dispatch_sources(run)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None
    old_started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    research_job.started_at = old_started_at
    repo.wait_for_sources(run, research_job)
    for source_job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        source_job.status = "succeeded"

    assert repo.requeue_source_ready_runs() == 1
    assert research_job.started_at is None

    claimed = repo.claim_next_run_job()
    assert claimed is not None and claimed.id == research_job.id
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert claimed.started_at is not None
    assert claimed.started_at > stale_cutoff
    assert repo.recover_stale_run_jobs(before=stale_cutoff) == 0
    assert claimed.status == "running"
    assert claimed.attempt == 2
