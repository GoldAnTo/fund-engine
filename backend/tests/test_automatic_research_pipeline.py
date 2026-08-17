from __future__ import annotations

import uuid
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY
from app.errors import ValidationFailedError
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AIAssessment,
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseTenantAdmission,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    JobEvent,
    ResearchRun,
    ResearchTask,
    TaskItem,
)
from app.models.research_monitor import ResearchRunEvent
from app.models.proposals import Proposal
from app.repositories.auto_research import AutoResearchRepository
from app.services.case_monitor import ResearchRunEventRepository


def _automatic_run(
    session,
    *,
    factor: str = "需求增长",
    factors: list[str] | None = None,
    plan_mutation=None,
    max_rounds: int = 3,
    budget: int = 100,
) -> ResearchRun:
    factor_statements = factors or [factor]
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
    theses = [
        Thesis(
            research_case_id=case.id,
            statement=statement,
            created_by="system:automatic-intake",
            creator_type="ai",
            review_state="draft",
            created_at=now,
        )
        for statement in factor_statements
    ]
    session.add_all(theses)
    session.flush()
    repo = AutoResearchRepository(session)
    run = repo.create_run(
        research_case_id=case.id,
        scope_thesis_ids=[str(thesis.id) for thesis in theses],
        max_rounds=max_rounds,
        budget=budget,
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="system:automatic-intake",
        change_summary="automatic scope",
        created_at=now,
    )
    session.add(scope)
    session.flush()
    session.add_all(
        [
            *[
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=statement,
                    position=position,
                )
                for position, statement in enumerate(factor_statements)
            ],
            EventResearchLifecycle(
                research_case_id=case.id,
                status="researching",
                active_run_id=run.id,
                current_round=0,
                status_summary="automatic research running",
                current_gap=None,
                next_human_action=None,
                updated_at=now,
            ),
        ]
    )
    scope_payload = {
        "workflow_mode": "automatic",
        "factor_ids": [str(thesis.id) for thesis in theses],
        "factor_statements": factor_statements,
        "automatic_protocol": {"factors": factor_statements},
        "automatic_evidence_plan": {
            "items": [
                {
                    "factor": statement,
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
                for statement in factor_statements
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
    for thesis in theses:
        for task_type in ("support", "contradict", "result", "alternative"):
            repo.create_task(
                run_id=run.id,
                research_case_id=case.id,
                thesis_id=thesis.id,
                task_type=task_type,
                query=f"{task_type}: {thesis.statement}",
            )
    repo.enqueue_run_job(run)
    return run


def _admit_link(session, job: AcquisitionJob) -> EvidenceLink:
    """Persist one coherent automatic-admission lineage for a source job."""
    now = datetime.now(timezone.utc)
    raw = f"admitted-{job.id}".encode()
    sha = hashlib.sha256(raw).hexdigest()
    document = DocumentVersion(
        content_sha256=sha,
        source_url=f"https://example.test/{job.id}",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="自动采集证据",
    )
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key="test",
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
        adapter_key="test",
        external_record_id=str(job.id),
        external_version="v1",
        canonical_url=document.source_url,
        title="自动采集证据",
        published_at=now,
        source_role="company_disclosure",
        metadata_json={},
        created_at=now,
    )
    session.add_all([span, attempt, reference])
    session.flush()
    candidate = AtomicClaimCandidate(
        source_span_id=span.id,
        canonical_key=uuid.uuid4().hex,
        quote="自动采集证据",
        quote_start=0,
        quote_end=6,
        quote_sha256=hashlib.sha256("自动采集证据".encode()).hexdigest(),
        normalized_text="自动采集证据",
        claim_type="reported_claim",
        authority_level="primary",
        structured_fields={},
        validation_result={},
        created_at=now,
    )
    artifact = RetrievalArtifact(
        source_reference_id=reference.id,
        attempt_id=attempt.id,
        content_sha256=sha,
        raw_bytes=raw,
        mime_type="text/plain",
        byte_size=len(raw),
        final_url=document.source_url,
        retrieved_at=now,
    )
    session.add_all([candidate, artifact])
    session.flush()
    session.add(
        RetrievalArtifactDocument(
            retrieval_artifact_id=artifact.id,
            document_version_id=document.id,
            relation="created",
            publication_key=sha,
            created_at=now,
        )
    )
    decision = AutomaticAdmissionDecision(
        job_id=job.id,
        candidate_id=candidate.id,
        retrieval_artifact_id=artifact.id,
        outcome="admitted",
        gate_version="test-v1",
        policy_version=B_SCOPE_POLICY.version,
        gate_results={},
        created_at=now,
    )
    session.add(decision)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="自动采集证据",
        atomic_claim_candidate_id=candidate.id,
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    session.add(statement)
    session.flush()
    link = EvidenceLink(
        thesis_id=job.thesis_id,
        source_statement_id=statement.id,
        role=job.request_snapshot["target_link_role"],
        reason="automatic admission",
        scope={},
        available_at=now,
        creator_type="ai",
        review_state="automatically_admitted",
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    session.add(link)
    session.flush()
    return link


class _AssessmentGenerator:
    def __init__(
        self,
        *,
        conclusion="supported",
        gaps=None,
        before_persist_hook=None,
        displayed_as_provisional=True,
        creator_type="ai",
    ):
        self.conclusion = conclusion
        self.gaps = list(gaps or [])
        self.before_persist_hook = before_persist_hook
        self.displayed_as_provisional = displayed_as_provisional
        self.creator_type = creator_type
        self.calls = []

    def generate(
        self,
        thesis_id,
        cutoff,
        session,
        *,
        evidence_link_ids=None,
        before_persist=None,
    ):
        self.calls.append(
            {
                "thesis_id": thesis_id,
                "evidence_link_ids": list(evidence_link_ids or []),
            }
        )
        # Match AssessmentGenerator's provider boundary: reads and any caller
        # work are committed before the external call returns.
        session.commit()
        if self.before_persist_hook is not None:
            self.before_persist_hook(session)
        if before_persist is not None and not before_persist():
            return None
        snapshot = EvidenceSnapshot(
            thesis_id=thesis_id,
            cutoff=cutoff,
            evidence_link_ids=[str(value) for value in (evidence_link_ids or [])],
            created_at=cutoff,
        )
        session.add(snapshot)
        session.flush()
        assessment = AIAssessment(
            snapshot_id=snapshot.id,
            conclusion=self.conclusion,
            rationale="当前证据的暂定判断",
            gaps=self.gaps,
            displayed_as_provisional=self.displayed_as_provisional,
            creator_type=self.creator_type,
            model_version="test",
            created_at=cutoff,
        )
        session.add(assessment)
        session.flush()
        return assessment


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


def test_recovered_research_job_gets_a_new_claim_fence(session) -> None:
    run = _automatic_run(session)
    repo = AutoResearchRepository(session)
    first = repo.claim_next_run_job()
    assert first is not None
    first_token = first.claim_token
    assert first_token
    first.started_at = datetime.now(timezone.utc) - timedelta(hours=2)

    assert repo.recover_stale_run_jobs(
        before=datetime.now(timezone.utc) - timedelta(minutes=30)
    ) == 1
    second = repo.claim_next_run_job()
    assert second is not None and second.id == first.id
    assert second.claim_token
    assert second.claim_token != first_token
    second_id = second.id
    second_token = second.claim_token
    session.commit()

    repo.record_job_completion(
        second,
        status="failed",
        step="failed",
        error="stale worker",
        expected_claim_token=first_token,
    )
    persisted = session.get(Job, second_id)
    assert persisted is not None and persisted.status == "running"
    assert persisted.claim_token == second_token


def test_advance_completes_from_one_admitted_partial_source_without_human_gates(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    admitted = _admit_link(session, jobs[0])
    jobs[0].status, jobs[0].stage, jobs[0].admitted_count = "partial", "partial", 1
    jobs[0].exception_count = 2
    jobs[1].status, jobs[1].stage = "failed", "failed"
    jobs[2].status, jobs[2].stage = "cancelled", "cancelled"

    assert pipeline.advance(run) == "completed"
    assert run.status == "succeeded"
    assert run.stage == "complete"
    assert run.stop_reason == "automatic_completed"
    conclusion = session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == run.research_case_id
        )
    )
    assert conclusion is not None
    assert conclusion.state == "system_generated"
    assert conclusion.evidence_link_ids == [str(admitted.id)]
    assert "失败 1" in conclusion.text
    assert "取消 1" in conclusion.text
    assert "未执行 0" in conclusion.text
    assert "跳过/异常条目 2" in conclusion.text
    lifecycle = session.get(EventResearchLifecycle, run.research_case_id)
    assert lifecycle is not None
    assert lifecycle.status == "completed"
    assert lifecycle.next_human_action is None
    assert [call["thesis_id"] for call in generator.calls] == [jobs[0].thesis_id]
    assert generator.calls[0]["evidence_link_ids"] == [admitted.id]
    assert session.scalar(select(func.count()).select_from(TaskItem)) == 0

    source_tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert {(task.status, task.stage) for task in source_tasks} == {
        ("done", "completed"),
        ("failed", "failed"),
    }
    assert all(
        task.result
        and set(
            (
                "acquisition_job_id",
                "objective",
                "acquisition_status",
                "reference_count",
                "frozen_count",
                "admitted_count",
                "exception_count",
            )
        ).issubset(task.result)
        for task in source_tasks
    )
    assignment = session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == admitted.id
        )
    )
    assert assignment is not None and assignment.disposition == "mapped"
    assert pipeline.advance(run) == "completed"
    assert session.scalar(
        select(func.count()).select_from(EventResearchConclusion)
    ) == 1
    assert session.scalar(select(func.count()).select_from(Proposal)) == 0
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0


def test_advance_fails_final_round_with_no_usable_evidence(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    for job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        job.status, job.stage = "failed", "failed"

    assert pipeline.advance(run) == "failed"
    assert run.status == "failed"
    assert run.stage == "failed"
    assert run.stop_reason == "no_usable_evidence"
    assert generator.calls == []
    assert session.scalar(
        select(func.count()).select_from(EventResearchConclusion)
    ) == 0


def test_advance_replenishes_once_then_fails_when_all_rounds_have_zero_evidence(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["尚无可用证据"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    for job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        job.status, job.stage = "failed", "failed"
    assert pipeline.advance(run) == "waiting_for_sources"
    assert run.round == 2
    round_two = [
        job
        for job in session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
        if job.request_snapshot["round"] == 2
    ]
    assert len(round_two) == 3
    for job in round_two:
        job.status, job.stage = "failed", "failed"

    assert pipeline.advance(run) == "failed"
    assert run.stop_reason == "no_usable_evidence"
    assert run.budget_used == 6
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_finalizes_gapped_assessment_at_round_limit(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["缺少量化验证"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert "证据不足" in conclusion.text
    assert "缺少量化验证" in conclusion.text


def test_advance_creates_exact_next_round_for_gaps_and_is_idempotent(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["缺少行业对照"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    round_one_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, round_one_jobs[0])
    for index, job in enumerate(round_one_jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    round_one_jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "waiting_for_sources"
    assert run.round == 2
    assert run.status == "waiting_for_sources"
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .order_by(ResearchTask.round, ResearchTask.task_type)
        )
    )
    assert {task.round for task in tasks} == {1, 2}
    assert {task.task_type for task in tasks if task.round == 2} == {
        "support",
        "contradict",
        "alternative",
        "result",
    }
    assert all(
        "缺少行业对照" in task.query for task in tasks if task.round == 2
    )
    assert session.scalar(
        select(func.count())
        .select_from(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run.id)
    ) == 6

    assert pipeline.advance(run) == "waiting_for_sources"
    assert session.scalar(
        select(func.count())
        .select_from(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run.id)
    ) == 6
    assert session.scalar(
        select(func.count())
        .select_from(ResearchTask)
        .where(ResearchTask.run_id == run.id)
    ) == 8


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_advance_rejects_non_exact_multi_thesis_result_task_matrix(
    session, mutation: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, factors=["需求增长", "利润改善"])
    result_tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type == "result")
            .order_by(ResearchTask.id)
        )
    )
    if mutation == "missing":
        session.delete(result_tasks[0])
        session.flush()
    else:
        AutoResearchRepository(session).create_task(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=result_tasks[0].thesis_id,
            task_type="result",
            query="duplicate result",
        )

    with pytest.raises(ValueError, match="result task matrix"):
        AutomaticResearchPipeline(
            session, assessment_generator=_AssessmentGenerator()
        ).advance(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_advance_rejects_reused_assessment_bound_to_the_wrong_scoped_thesis(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(
        session, factors=["需求增长", "利润改善"], max_rounds=1
    )
    second = session.get(Thesis, uuid.UUID((run.scope_thesis_ids or [])[1]))
    assert second is not None
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    first_thesis_id = uuid.UUID((run.scope_thesis_ids or [])[0])
    admitted_job = next(job for job in jobs if job.thesis_id == first_thesis_id)
    admitted = _admit_link(session, admitted_job)
    for job in jobs:
        job.status, job.stage = "failed", "failed"
    admitted_job.status, admitted_job.stage, admitted_job.admitted_count = (
        "succeeded",
        "succeeded",
        1,
    )
    wrong_snapshot = EvidenceSnapshot(
        thesis_id=second.id,
        cutoff=datetime.now(timezone.utc),
        evidence_link_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    session.add(wrong_snapshot)
    session.flush()
    wrong_assessment = AIAssessment(
        snapshot_id=wrong_snapshot.id,
        conclusion="supported",
        rationale="wrong thesis",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="ai",
        created_at=datetime.now(timezone.utc),
    )
    session.add(wrong_assessment)
    session.flush()
    first_result = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
            ResearchTask.thesis_id == first_thesis_id,
        )
    )
    assert first_result is not None
    first_result.status, first_result.stage = "done", "completed"
    first_result.result = {
        "task_type": "result",
        "assessment_id": str(wrong_assessment.id),
        "conclusion": wrong_assessment.conclusion,
        "gaps": [],
    }

    with pytest.raises(ValueError, match="assessment.*thesis|evidence scope"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
    assert admitted.id is not None


def test_advance_rejects_generated_assessment_not_marked_provisional(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            displayed_as_provisional=False,
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    with pytest.raises(ValueError, match="provenance"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_rejects_reused_non_ai_assessment(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    admitted = _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    snapshot = EvidenceSnapshot(
        thesis_id=jobs[0].thesis_id,
        cutoff=datetime.now(timezone.utc),
        evidence_link_ids=[str(admitted.id)],
        created_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)
    session.flush()
    assessment = AIAssessment(
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="not automatic AI provenance",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="human",
        created_at=datetime.now(timezone.utc),
    )
    session.add(assessment)
    session.flush()
    result_task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
        )
    )
    assert result_task is not None
    result_task.status, result_task.stage = "done", "completed"
    result_task.result = {
        "task_type": "result",
        "assessment_id": str(assessment.id),
        "conclusion": assessment.conclusion,
        "gaps": [],
    }

    with pytest.raises(ValueError, match="provenance"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_assessment_excludes_prior_run_and_reviewed_visible_evidence(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    current = _admit_link(session, jobs[0])
    prior_run = AutoResearchRepository(session).create_run(
        research_case_id=run.research_case_id,
        scope_thesis_ids=run.scope_thesis_ids,
    )
    prior_job = AcquisitionJob(
        tenant_id=jobs[0].tenant_id,
        research_case_id=run.research_case_id,
        thesis_id=jobs[0].thesis_id,
        research_run_id=prior_run.id,
        idempotency_key=f"prior:{uuid.uuid4()}",
        request_snapshot=dict(jobs[0].request_snapshot),
        policy_snapshot=dict(jobs[0].policy_snapshot),
        status="succeeded",
        stage="succeeded",
        attempt=1,
        reference_count=1,
        fetched_count=1,
        frozen_count=1,
        admitted_count=1,
        exception_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(prior_job)
    session.flush()
    prior = _admit_link(session, prior_job)
    reviewed = EvidenceLink(
        thesis_id=jobs[0].thesis_id,
        source_statement_id=current.source_statement_id,
        role="supports",
        reason="reviewed evidence outside automatic run",
        scope={"source": "reviewed"},
        available_at=datetime.now(timezone.utc),
        creator_type="human",
        review_state="reviewed",
        created_at=datetime.now(timezone.utc),
    )
    session.add(reviewed)
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    result_task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
        )
    )
    assert result_task is not None and result_task.result is not None
    assessment = session.get(
        AIAssessment, uuid.UUID(result_task.result["assessment_id"])
    )
    assert assessment is not None
    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    assert snapshot is not None
    assert snapshot.evidence_link_ids == [str(current.id)]
    assert str(prior.id) not in snapshot.evidence_link_ids
    assert str(reviewed.id) not in snapshot.evidence_link_ids
    conclusion = session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == run.research_case_id
        )
    )
    assert conclusion is not None
    assert conclusion.evidence_link_ids == snapshot.evidence_link_ids


def test_advance_rejects_replaced_current_scope_before_mapping_or_assessment(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    current_scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert current_scope is not None
    replacement = EventResearchScopeVersion(
        research_case_id=run.research_case_id,
        version=current_scope.version + 1,
        changed_by="human:reviewer",
        change_summary="replace frozen automatic scope",
        created_at=datetime.now(timezone.utc),
    )
    session.add(replacement)
    session.flush()
    session.add(
        EventResearchScopeFactor(
            scope_version_id=replacement.id,
            statement="替换后的范围",
            position=0,
        )
    )

    with pytest.raises(ValueError, match="current scope"):
        pipeline.advance(run)
    assert generator.calls == []
    assert session.scalar(
        select(func.count()).select_from(EventResearchScopeEvidenceAssignment)
    ) == 0


def test_automatic_conclusion_id_is_run_derived_and_ignores_overlapping_result(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    assert pipeline.advance(run) == "completed"
    original = session.scalar(select(EventResearchConclusion))
    assert original is not None
    overlapping = EventResearchConclusion(
        research_case_id=original.research_case_id,
        scope_version_id=original.scope_version_id,
        state="system_generated",
        text=original.text,
        primary_factor=original.primary_factor,
        evidence_link_ids=original.evidence_link_ids,
        based_on_conclusion_id=None,
        reviewer=None,
        created_at=original.created_at + timedelta(seconds=1),
    )
    session.add(overlapping)
    session.flush()

    retried = EventConclusionService(session).create_automatic_result(
        run.research_case_id, run.id
    )
    expected_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fund-engine:event-research:automatic:{run.id}",
    )
    assert retried.id == expected_id
    assert retried.id == original.id
    assert retried.id != overlapping.id


@pytest.mark.parametrize("drift", ["primary_factor", "reviewer", "based_on"])
def test_automatic_conclusion_retry_rejects_immutable_identity_drift(
    session, monkeypatch, drift: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    admitted = _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    with monkeypatch.context() as patch:
        patch.setattr(
            EventConclusionService,
            "create_automatic_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stop before conclusion")
            ),
        )
        with pytest.raises(RuntimeError, match="stop before conclusion"):
            pipeline.advance(run)

    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert scope is not None
    based_on_id = None
    if drift == "based_on":
        seed = EventResearchConclusion(
            research_case_id=run.research_case_id,
            scope_version_id=scope.id,
            state="system_generated",
            text="unrelated seed",
            primary_factor=None,
            evidence_link_ids=[],
            based_on_conclusion_id=None,
            reviewer=None,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        session.add(seed)
        session.flush()
        based_on_id = seed.id
    expected_text = (
        "需求增长：得到当前证据支持。当前证据的暂定判断\n"
        "局限：结论仅基于本次冻结范围内自动准入且映射到当前范围的证据。"
        "采集任务：失败 2，取消 0，未执行 0，跳过/异常条目 0。"
    )
    conflict = EventResearchConclusion(
        id=uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"fund-engine:event-research:automatic:{run.id}",
        ),
        research_case_id=run.research_case_id,
        scope_version_id=scope.id,
        state="system_generated",
        text=expected_text,
        primary_factor="错误主因素" if drift == "primary_factor" else "需求增长",
        evidence_link_ids=[str(admitted.id)],
        based_on_conclusion_id=based_on_id,
        reviewer="human:reviewer" if drift == "reviewer" else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(conflict)
    session.flush()

    with pytest.raises(ValidationFailedError, match="immutable"):
        EventConclusionService(session).create_automatic_result(
            run.research_case_id, run.id
        )


def test_automatic_clean_success_still_reports_evidence_boundary(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for job in jobs:
        job.status, job.stage = "succeeded", "succeeded"
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert "局限" in conclusion.text
    assert "冻结范围" in conclusion.text


def test_cancel_after_provider_prevents_assessment_attachment_and_conclusion(
    tmp_path,
) -> None:
    from app.models.ledger import Base
    from app.services.auto_research import AutoResearchService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    engine = create_engine(
        f"sqlite:///{tmp_path / 'cancel-automatic-assessment.db'}", future=True
    )
    session_local = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(engine)
    worker = session_local()
    try:
        run = _automatic_run(worker, max_rounds=1)
        pipeline = AutomaticResearchPipeline(
            worker, assessment_generator=_AssessmentGenerator()
        )
        assert pipeline.advance(run) == "waiting_for_sources"
        jobs = list(
            worker.scalars(
                select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
            )
        )
        _admit_link(worker, jobs[0])
        for index, job in enumerate(jobs):
            job.status = "succeeded" if index == 0 else "failed"
            job.stage = job.status
        jobs[0].admitted_count = 1
        run_id, case_id = run.id, run.research_case_id
        worker.commit()

        def cancel_in_other_session(_worker_session):
            with session_local() as cancelling:
                AutoResearchService(cancelling).cancel_run(
                    run_id,
                    actor="human:test",
                    change_reason="cancel after provider",
                )

        pipeline = AutomaticResearchPipeline(
            worker,
            assessment_generator=_AssessmentGenerator(
                before_persist_hook=cancel_in_other_session
            ),
        )
        stale = worker.get(ResearchRun, run_id)
        assert stale is not None
        with pytest.raises(ValueError, match="cancelled|stale"):
            pipeline.advance(stale)
        worker.rollback()
    finally:
        worker.close()

    with Session(engine) as check:
        persisted = check.get(ResearchRun, run_id)
        assert persisted is not None and persisted.status == "cancelled"
        assert check.scalar(select(func.count()).select_from(AIAssessment)) == 0
        assert check.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
        result_task = check.scalar(
            select(ResearchTask).where(
                ResearchTask.run_id == run_id,
                ResearchTask.task_type == "result",
            )
        )
        assert result_task is not None
        assert result_task.status == "cancelled"
        assert result_task.result is None
