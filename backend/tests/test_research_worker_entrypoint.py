from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker


def _seed_terminal_race(session_local):
    from app.models.ledger import (
        CaseDocumentVersion,
        CaseTenantAdmission,
        DocumentVersion,
        ResearchCase,
    )
    from app.repositories.auto_research import AutoResearchRepository

    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="research worker terminal race",
            industry_topic="terminal state",
            created_by="test",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        document = DocumentVersion(
            content_sha256=uuid.uuid4().hex,
            source_url=f"https://example.test/terminal-race/{case.id}",
            available_at=now,
            acquired_at=now,
            parser_version="test",
        )
        setup.add(document)
        setup.flush()
        setup.add_all(
            [
                CaseDocumentVersion(
                    research_case_id=case.id,
                    document_version_id=document.id,
                    linked_at=now,
                ),
                CaseTenantAdmission(
                    research_case_id=case.id,
                    tenant_id="test-team",
                    initial_document_version_id=document.id,
                    admitted_by="test-fixture",
                    admitted_at=now,
                ),
            ]
        )
        run = AutoResearchRepository(setup).create_run(
            research_case_id=case.id,
            max_rounds=1,
            budget=1,
        )
        job = AutoResearchRepository(setup).enqueue_run_job(run)
        setup.commit()
        return run.id, job.id


def _patch_worker_dependencies(monkeypatch, run_research_worker, session_local):
    monkeypatch.setattr(run_research_worker, "SessionLocal", session_local)


def _postgres_session_is_blocked(engine, *, pid: int, done: Event) -> bool:
    """Observe a real lock wait without timing the interleaving with sleep."""
    with engine.connect() as observer:
        for _ in range(500):
            if observer.execute(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                {"pid": pid},
            ).scalar_one():
                return True
            if done.is_set():
                return False
    raise AssertionError("cancel neither blocked nor completed")


def test_worker_holds_a_claim_for_the_configured_observation_window(
    monkeypatch,
) -> None:
    from app.scripts import run_research_worker

    observed: list[float] = []
    monkeypatch.setattr(run_research_worker.time, "sleep", observed.append)

    run_research_worker._hold_claim_for_observation(15)
    run_research_worker._hold_claim_for_observation(0)

    assert observed == [15]


def test_worker_persists_failure_when_post_execution_callback_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.models.ledger import Base
    from app.models.operational import Job, ResearchRun
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-callback-failure.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    def finish_execute(self, run, claim=None):
        self._active_claim = claim
        self.repo.update_run(
            run,
            status="succeeded",
            stage="complete",
            stop_reason="fixture_complete",
        )
        self.session.flush()

    monkeypatch.setattr(AutoResearchService, "execute", finish_execute)
    monkeypatch.setattr(
        AutoResearchService,
        "reconcile_orchestration_after_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("callback failed")
        ),
    )

    with pytest.raises(RuntimeError, match="callback failed"):
        run_research_worker.run_once()

    with session_local() as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        assert run is not None
        assert job is not None
        assert (run.status, run.stage, run.stop_reason) == (
            "failed",
            "failed",
            "execution_failed",
        )
        assert (job.status, job.step, job.failure_count) == (
            "failed",
            "failed",
            1,
        )


def test_worker_recovers_committed_running_task_after_process_crash(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.ledger import Base, ResearchCase, Thesis
    from app.models.operational import Job, JobEvent, ResearchRun, ResearchTask
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-process-crash-recovery.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="worker crash recovery",
            industry_topic="committed task checkpoint",
            created_by="test",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="recovery must replay interrupted tasks",
            review_state="confirmed",
            created_by="test",
            created_at=now,
        )
        setup.add(thesis)
        setup.flush()
        repo = AutoResearchRepository(setup)
        run = repo.create_run(
            research_case_id=case.id,
            max_rounds=1,
            budget=10,
            scope_thesis_ids=[str(thesis.id)],
        )
        completed = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type="support",
            query="already completed",
        )
        completed.status = "done"
        completed.stage = "completed"
        completed.result = {"checkpoint": "must survive"}
        interrupted = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type="support",
            query="provider exits before result persistence",
        )
        waiting = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type="contradict",
            query="must still run after recovery",
        )
        job = repo.enqueue_run_job(run)
        setup.commit()
        case_id, run_id, job_id = case.id, run.id, job.id
        completed_id, interrupted_id, waiting_id = (
            completed.id,
            interrupted.id,
            waiting.id,
        )

    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    provider_calls: list[uuid.UUID] = []
    process_crashed = False

    def provider_checkpoint(self, _proposer, task, _run, **_kwargs):
        nonlocal process_crashed
        if not process_crashed:
            process_crashed = True
            raise SystemExit("simulated process exit")
        provider_calls.append(task.id)
        return []

    monkeypatch.setattr(
        AutoResearchService,
        "_propose_for_task",
        provider_checkpoint,
    )

    with pytest.raises(SystemExit, match="simulated process exit"):
        run_research_worker.run_once(recover_after_minutes=30)

    stale_started_at = now - timedelta(hours=2)
    with session_local() as stale:
        persisted_run = stale.get(ResearchRun, run_id)
        persisted_job = stale.get(Job, job_id)
        persisted_interrupted = stale.get(ResearchTask, interrupted_id)
        assert persisted_run is not None and persisted_run.status == "running"
        assert persisted_run.round == 1
        assert persisted_job is not None and persisted_job.status == "running"
        assert persisted_interrupted is not None
        assert persisted_interrupted.status == "running"
        persisted_job.started_at = stale_started_at
        stale.commit()

    assert run_research_worker.run_once(recover_after_minutes=30)

    with Session(engine) as check:
        recovered_run = check.get(ResearchRun, run_id)
        recovered_job = check.get(Job, job_id)
        recovered_tasks = {
            task.id: task
            for task in check.scalars(
                select(ResearchTask).where(ResearchTask.run_id == run_id)
            )
        }
        assert recovered_run is not None and recovered_run.status == "succeeded"
        assert recovered_job is not None and recovered_job.status == "succeeded"
        assert recovered_tasks[completed_id].status == "done"
        assert recovered_tasks[completed_id].result == {"checkpoint": "must survive"}
        assert recovered_tasks[interrupted_id].status == "done"
        assert recovered_tasks[waiting_id].status == "done"
        assert provider_calls == [interrupted_id, waiting_id]
        assert recovered_job.attempt == 2
        assert recovered_job.failure_count == 0
        assert recovered_job.started_at is not None
        recovered_started_at = recovered_job.started_at
        if recovered_started_at.tzinfo is None:
            recovered_started_at = recovered_started_at.replace(tzinfo=timezone.utc)
        assert recovered_started_at > stale_started_at
        assert recovered_job.finished_at is not None
        assert recovered_job.error is None
        assert (
            check.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.research_case_id == case_id)
            )
            == 1
        )
        assert (
            check.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.target_type == "research_run", Job.target_id == run_id)
            )
            == 1
        )
        assert (
            check.scalar(
                select(func.count())
                .select_from(ResearchTask)
                .where(
                    ResearchTask.run_id == run_id,
                    ResearchTask.id.in_([completed_id, interrupted_id, waiting_id]),
                )
            )
            == 3
        )
        recovery_events = list(
            check.scalars(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.step == "recovered",
                )
            )
        )
        assert len(recovery_events) == 1


@pytest.mark.parametrize("stale_outcome", ["success", "failure"])
def test_reclaimed_attempt_fences_stale_worker_provider_return(
    tmp_path: Path, monkeypatch, stale_outcome: str
) -> None:
    from app.models.ledger import Base, ResearchCase, Thesis
    from app.models.operational import Job, ResearchRun, ResearchTask
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(
        f"sqlite:///{tmp_path / f'worker-attempt-fence-{stale_outcome}.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title=f"attempt fencing {stale_outcome}",
            industry_topic="worker lease",
            created_by="test",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="only the current worker may persist provider output",
            review_state="confirmed",
            created_by="test",
            created_at=now,
        )
        setup.add(thesis)
        setup.flush()
        repo = AutoResearchRepository(setup)
        run = repo.create_run(
            research_case_id=case.id,
            max_rounds=1,
            budget=5,
            scope_thesis_ids=[str(thesis.id)],
        )
        task = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type="support",
            query="fenced provider call",
        )
        job = repo.enqueue_run_job(run)
        setup.commit()
        run_id, task_id, job_id = run.id, task.id, job.id

    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    provider_lock = Lock()
    provider_calls = 0
    stale_provider_entered = Event()
    current_provider_entered = Event()
    release_stale_provider = Event()
    release_current_provider = Event()
    worker_errors: list[BaseException] = []
    claims: list[object] = []
    original_claim = AutoResearchRepository.claim_next_run_job

    def observe_claim(self):
        claim = original_claim(self)
        if claim is not None:
            claims.append(claim)
        return claim

    def controlled_provider(self, _proposer, task, run, **_kwargs):
        nonlocal provider_calls
        with provider_lock:
            provider_calls += 1
            call_number = provider_calls
        if call_number == 1:
            stale_provider_entered.set()
            assert release_stale_provider.wait(timeout=5)
            if stale_outcome == "failure":
                raise RuntimeError("stale provider failed after lease recovery")
            return []
        if call_number == 2:
            current_provider_entered.set()
            assert release_current_provider.wait(timeout=5)
            return []
        raise AssertionError(f"unexpected provider call {call_number}")

    monkeypatch.setattr(
        AutoResearchRepository,
        "claim_next_run_job",
        observe_claim,
    )
    monkeypatch.setattr(
        AutoResearchService,
        "_propose_for_task",
        controlled_provider,
    )

    def run_worker() -> None:
        try:
            run_research_worker.run_once(recover_after_minutes=30)
        except BaseException as exc:
            worker_errors.append(exc)

    stale_worker = Thread(target=run_worker)
    current_worker = Thread(target=run_worker)
    stale_worker.start()
    assert stale_provider_entered.wait(timeout=5)
    with session_local() as stale:
        persisted_job = stale.get(Job, job_id)
        assert persisted_job is not None
        persisted_job.started_at = now - timedelta(hours=2)
        stale.commit()
    current_worker.start()
    assert current_provider_entered.wait(timeout=5)

    try:
        assert len(claims) == 2
        assert claims[0].job_id == claims[1].job_id == job_id
        assert claims[0].claimed_attempt == 1
        assert claims[1].claimed_attempt == 2

        release_stale_provider.set()
        stale_worker.join(timeout=5)
        assert not stale_worker.is_alive()
        with Session(engine) as check:
            persisted_job = check.get(Job, job_id)
            persisted_run = check.get(ResearchRun, run_id)
            persisted_task = check.get(ResearchTask, task_id)
            assert persisted_job is not None
            assert persisted_job.status == "running"
            assert persisted_job.attempt == 2
            assert persisted_run is not None
            assert persisted_run.status == "running"
            assert persisted_task is not None
            assert persisted_task.status == "running"

        release_current_provider.set()
        current_worker.join(timeout=5)
        assert not current_worker.is_alive()
    finally:
        release_stale_provider.set()
        release_current_provider.set()
        stale_worker.join(timeout=5)
        current_worker.join(timeout=5)

    assert worker_errors == []
    with Session(engine) as check:
        persisted_job = check.get(Job, job_id)
        persisted_run = check.get(ResearchRun, run_id)
        persisted_task = check.get(ResearchTask, task_id)
        assert persisted_job is not None
        assert persisted_job.status == "succeeded"
        assert persisted_job.attempt == 2
        assert persisted_run is not None
        assert persisted_run.status == "succeeded"
        assert persisted_task is not None
        assert persisted_task.status == "done"
        assert (
            check.scalar(select(func.count()).select_from(Job).where(Job.id == job_id))
            == 1
        )
        assert (
            check.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.id == run_id)
            )
            == 1
        )


@pytest.mark.pg_only
def test_postgres_reclaimed_attempt_fences_stale_worker_repeatedly(
    engine, monkeypatch
) -> None:
    from app.models.ledger import ResearchCase, Thesis
    from app.models.operational import Job, ResearchRun, ResearchTask
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    session_local = sessionmaker(bind=engine, future=True)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    provider_lock = Lock()
    state: dict[str, object] = {}
    claims: list[object] = []
    original_claim = AutoResearchRepository.claim_next_run_job

    def observe_claim(self):
        claim = original_claim(self)
        if claim is not None:
            claims.append(claim)
        return claim

    def controlled_provider(self, _proposer, task, run, **_kwargs):
        with provider_lock:
            state["calls"] = int(state["calls"]) + 1
            call_number = int(state["calls"])
        entered = state["entered"]
        releases = state["releases"]
        assert isinstance(entered, tuple) and isinstance(releases, tuple)
        entered[call_number - 1].set()
        assert releases[call_number - 1].wait(timeout=10)
        if call_number == 1 and state["stale_outcome"] == "failure":
            raise RuntimeError("stale PostgreSQL provider failed after recovery")
        return []

    monkeypatch.setattr(
        AutoResearchRepository,
        "claim_next_run_job",
        observe_claim,
    )
    monkeypatch.setattr(
        AutoResearchService,
        "_propose_for_task",
        controlled_provider,
    )

    for iteration in range(3):
        now = datetime.now(timezone.utc)
        with session_local() as setup:
            case = ResearchCase(
                title=f"postgres attempt fencing {iteration}",
                industry_topic="worker lease",
                created_by="test",
                created_at=now,
            )
            setup.add(case)
            setup.flush()
            thesis = Thesis(
                research_case_id=case.id,
                statement=f"only attempt two persists {iteration}",
                review_state="confirmed",
                created_by="test",
                created_at=now,
            )
            setup.add(thesis)
            setup.flush()
            repo = AutoResearchRepository(setup)
            run = repo.create_run(
                research_case_id=case.id,
                max_rounds=1,
                budget=5,
                scope_thesis_ids=[str(thesis.id)],
            )
            task = repo.create_task(
                run_id=run.id,
                research_case_id=case.id,
                thesis_id=thesis.id,
                task_type="support",
                query=f"fenced provider call {iteration}",
            )
            job = repo.enqueue_run_job(run)
            setup.commit()
            run_id, task_id, job_id = run.id, task.id, job.id

        stale_entered, current_entered = Event(), Event()
        release_stale, release_current = Event(), Event()
        state.clear()
        state.update(
            calls=0,
            entered=(stale_entered, current_entered),
            releases=(release_stale, release_current),
            stale_outcome="success" if iteration % 2 == 0 else "failure",
        )
        worker_errors: list[BaseException] = []
        claim_offset = len(claims)

        def run_worker() -> None:
            try:
                run_research_worker.run_once(recover_after_minutes=30)
            except BaseException as exc:
                worker_errors.append(exc)

        stale_worker = Thread(target=run_worker)
        current_worker = Thread(target=run_worker)
        stale_worker.start()
        assert stale_entered.wait(timeout=10)
        with session_local() as stale:
            persisted_job = stale.get(Job, job_id)
            assert persisted_job is not None
            persisted_job.started_at = now - timedelta(hours=2)
            stale.commit()
        current_worker.start()
        assert current_entered.wait(timeout=10)

        try:
            iteration_claims = claims[claim_offset:]
            assert [claim.claimed_attempt for claim in iteration_claims] == [1, 2]
            release_stale.set()
            stale_worker.join(timeout=10)
            assert not stale_worker.is_alive()
            with Session(engine) as check:
                assert check.get(Job, job_id).status == "running"
                assert check.get(Job, job_id).attempt == 2
                assert check.get(ResearchRun, run_id).status == "running"
                assert check.get(ResearchTask, task_id).status == "running"

            release_current.set()
            current_worker.join(timeout=10)
            assert not current_worker.is_alive()
        finally:
            release_stale.set()
            release_current.set()
            stale_worker.join(timeout=10)
            current_worker.join(timeout=10)

        assert worker_errors == []
        with Session(engine) as check:
            persisted_job = check.get(Job, job_id)
            assert persisted_job is not None
            assert persisted_job.status == "succeeded"
            assert persisted_job.attempt == 2
            assert check.get(ResearchRun, run_id).status == "succeeded"
            assert check.get(ResearchTask, task_id).status == "done"


def test_stale_recovery_is_idempotent_and_does_not_reopen_failed_job(
    tmp_path: Path,
) -> None:
    from app.models.ledger import Base, ResearchCase
    from app.models.operational import JobEvent, ResearchTask
    from app.repositories.auto_research import AutoResearchRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale-recovery-idempotency.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    stale_started_at = now - timedelta(hours=2)
    with Session(engine) as session:
        case = ResearchCase(
            title="idempotent stale recovery",
            industry_topic="worker retry",
            created_by="test",
            created_at=now,
        )
        session.add(case)
        session.flush()
        repo = AutoResearchRepository(session)
        run = repo.create_run(research_case_id=case.id, max_rounds=1, budget=5)
        task = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            task_type="support",
            query="interrupted task",
        )
        job = repo.enqueue_run_job(run)
        run.status = "running"
        run.stage = "research"
        run.round = 1
        task.status = "running"
        task.stage = "research"
        job.status = "running"
        job.started_at = stale_started_at
        session.flush()

        assert repo.recover_stale_run_jobs(before=now - timedelta(hours=1)) == 1
        assert repo.recover_stale_run_jobs(before=now - timedelta(hours=1)) == 0
        assert run.status == "queued" and run.round == 0
        assert task.status == "queued" and task.stage == "planning"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchTask)
                .where(ResearchTask.run_id == run.id)
            )
            == 1
        )
        assert job.status == "queued" and job.attempt == 2
        assert job.failure_count == 0
        assert job.started_at is None and job.finished_at is None
        assert job.error == "worker lease expired; run and running tasks requeued"
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(
                    JobEvent.job_id == job.id,
                    JobEvent.step == "recovered",
                )
            )
            == 1
        )

        run.status = "running"
        run.stage = "research"
        run.round = 1
        task.status = "running"
        task.stage = "research"
        job.status = "failed"
        job.step = "failed"
        job.started_at = stale_started_at
        job.finished_at = now
        job.error = "terminal failure"
        session.flush()

        assert repo.recover_stale_run_jobs(before=now - timedelta(hours=1)) == 0
        assert run.status == "running" and run.round == 1
        assert task.status == "running"
        assert job.status == "failed"
        assert job.attempt == 2
        assert job.error == "terminal failure"
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(
                    JobEvent.job_id == job.id,
                    JobEvent.step == "recovered",
                )
            )
            == 1
        )


def test_stale_recovery_rejects_cross_case_job_ownership(tmp_path: Path) -> None:
    from app.models.ledger import Base, ResearchCase
    from app.models.operational import Job, JobEvent
    from app.repositories.auto_research import AutoResearchRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale-recovery-ownership.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        owner = ResearchCase(
            title="run owner",
            industry_topic="ownership",
            created_by="test",
            created_at=now,
        )
        wrong_case = ResearchCase(
            title="wrong job owner",
            industry_topic="ownership",
            created_by="test",
            created_at=now,
        )
        session.add_all([owner, wrong_case])
        session.flush()
        repo = AutoResearchRepository(session)
        run = repo.create_run(research_case_id=owner.id, max_rounds=1, budget=5)
        task = repo.create_task(
            run_id=run.id,
            research_case_id=owner.id,
            task_type="support",
            query="must not cross case boundary",
        )
        run.status = "running"
        run.stage = "research"
        run.round = 1
        task.status = "running"
        task.stage = "research"
        job = Job(
            kind="research_run",
            status="running",
            target_type="research_run",
            target_id=run.id,
            research_case_id=wrong_case.id,
            started_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=2),
        )
        session.add(job)
        session.flush()

        assert repo.recover_stale_run_jobs(before=now - timedelta(hours=1)) == 0
        assert run.status == "running" and run.round == 1
        assert task.status == "running"
        assert job.status == "failed"
        assert job.step == "recovery_rejected"
        assert job.error == "research run ownership mismatch; recovery refused"
        event = session.scalar(
            select(JobEvent).where(
                JobEvent.job_id == job.id,
                JobEvent.step == "recovery_rejected",
            )
        )
        assert event is not None and event.status == "failed"


def test_scheduler_reconciles_event_acquisition_before_worker_claims_research_jobs(
    cmd_client, cmd_session, monkeypatch
) -> None:
    import uuid

    from app.models.acquisition import AcquisitionJob
    from app.models.event_research import EventResearchScopeVersion
    from app.models.operational import Job
    from app.models.research_orchestration import (
        AcquisitionQueryPlan,
        ResearchOrchestration,
    )
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker, run_scheduler

    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "公司上调产能指引。",
            "event_title": "产能指引更新",
            "company_name": "测试公司",
            "ticker": "600001.SH",
            "event_at": "2026-08-01T08:00:00Z",
            "research_question": "新增产能是否有持续需求支持？",
            "candidate_factors": ["订单增长", "收入兑现", "库存回补"],
        },
    )
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    confirmed = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": "worker-order-confirm",
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    cmd_session.commit()

    session_local = sessionmaker(bind=cmd_session.get_bind(), future=True)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    original_claim = AutoResearchRepository.claim_next_run_job
    observed_states: list[str] = []

    def observe_claim(self):
        orchestration = self._session.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.research_case_id == case_id
            )
        )
        assert orchestration is not None
        observed_states.append(orchestration.state)
        return original_claim(self)

    monkeypatch.setattr(
        AutoResearchRepository,
        "claim_next_run_job",
        observe_claim,
    )

    assert run_scheduler.run_once(session_factory=session_local)
    assert run_research_worker.run_once() is False

    assert observed_states == ["acquiring"]
    with Session(cmd_session.get_bind()) as check:
        assert check.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
        assert check.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7
        assert (
            check.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "research_run")
            )
            == 0
        )


def test_scheduler_recovers_stale_acquisition_before_worker_claim(
    cmd_client, cmd_session, monkeypatch
) -> None:
    import uuid

    from app.models.acquisition import AcquisitionJob
    from app.models.event_research import EventResearchScopeVersion
    from app.models.research_orchestration import (
        AcquisitionQueryPlan,
        ResearchOrchestration,
        ResearchOrchestrationEvent,
    )
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker, run_scheduler
    from app.services.research_orchestration import ResearchOrchestrationService

    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "公司上调产能指引。",
            "event_title": "失联恢复事件",
            "company_name": "恢复测试公司",
            "ticker": "600001.SH",
            "event_at": "2026-08-01T08:00:00Z",
            "research_question": "恢复后是否继续同一资料获取任务？",
            "candidate_factors": ["订单增长", "收入兑现", "库存回补"],
        },
    )
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    confirmed = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": "worker-stale-recovery-confirm",
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    assert orchestration is not None
    orchestration.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=2)
    cmd_session.commit()

    session_local = sessionmaker(bind=cmd_session.get_bind(), future=True)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    order: list[str] = []
    original_start = ResearchOrchestrationService.start_stale_acquisition_recoveries
    original_complete = ResearchOrchestrationService.complete_acquisition_recoveries
    original_reconcile = ResearchOrchestrationService.reconcile_batch
    original_claim = AutoResearchRepository.claim_next_run_job

    def observe_start(self, **kwargs):
        order.append("recover_stale")
        return original_start(self, **kwargs)

    def observe_complete(self, **kwargs):
        order.append("complete_recovery")
        return original_complete(self, **kwargs)

    def observe_reconcile(self, **kwargs):
        order.append("reconcile")
        return original_reconcile(self, **kwargs)

    def observe_claim(self):
        order.append("claim_research_job")
        return original_claim(self)

    monkeypatch.setattr(
        ResearchOrchestrationService,
        "start_stale_acquisition_recoveries",
        observe_start,
    )
    monkeypatch.setattr(
        ResearchOrchestrationService,
        "complete_acquisition_recoveries",
        observe_complete,
    )
    monkeypatch.setattr(
        ResearchOrchestrationService,
        "reconcile_batch",
        observe_reconcile,
    )
    monkeypatch.setattr(
        AutoResearchRepository,
        "claim_next_run_job",
        observe_claim,
    )

    assert run_scheduler.run_once(
        session_factory=session_local,
        recover_after_minutes=30,
    )
    assert run_research_worker.run_once(recover_after_minutes=30) is False
    assert order == [
        "recover_stale",
        "complete_recovery",
        "reconcile",
        "claim_research_job",
    ]
    order.clear()
    run_scheduler.run_once(
        session_factory=session_local,
        recover_after_minutes=30,
    )
    run_research_worker.run_once(recover_after_minutes=30)

    with Session(cmd_session.get_bind()) as check:
        recovery_events = list(
            check.scalars(
                select(ResearchOrchestrationEvent)
                .where(
                    ResearchOrchestrationEvent.orchestration_id == orchestration.id,
                    ResearchOrchestrationEvent.transition.in_(
                        (
                            "acquisition_recovery_started",
                            "recovery_completed",
                        )
                    ),
                )
                .order_by(ResearchOrchestrationEvent.sequence)
            )
        )
        assert [event.transition for event in recovery_events] == [
            "acquisition_recovery_started",
            "recovery_completed",
        ]
        assert check.scalar(select(func.count()).select_from(AcquisitionJob)) == 7
        assert check.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 7


def test_scheduler_isolates_corrupt_recovery_and_does_not_duplicate_failure(
    cmd_client, cmd_session, monkeypatch
) -> None:
    import uuid

    from app.models.event_research import EventResearchScopeVersion
    from app.models.research_orchestration import (
        ResearchOrchestration,
        ResearchOrchestrationEvent,
    )
    from app.scripts import run_scheduler

    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "公司资料获取检查点损坏。",
            "event_title": "损坏恢复事件",
            "company_name": "恢复隔离公司",
            "ticker": "600002.SH",
            "event_at": "2026-08-01T08:00:00Z",
            "research_question": "坏检查点是否会拖垮 worker？",
            "candidate_factors": ["订单增长", "收入兑现", "库存回补"],
        },
    )
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    confirmed = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope.id),
            "idempotency_key": "worker-corrupt-recovery-confirm",
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    orchestration = cmd_session.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.research_case_id == case_id
        )
    )
    assert orchestration is not None
    orchestration.checkpoint_json = {"secret": "do-not-persist"}
    orchestration.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=2)
    cmd_session.commit()

    session_local = sessionmaker(bind=cmd_session.get_bind(), future=True)
    assert run_scheduler.run_once(
        session_factory=session_local,
        recover_after_minutes=30,
    )
    run_scheduler.run_once(
        session_factory=session_local,
        recover_after_minutes=30,
    )
    with Session(cmd_session.get_bind()) as check:
        persisted = check.get(ResearchOrchestration, orchestration.id)
        assert persisted is not None
        assert persisted.state == "failed"
        events = list(
            check.scalars(
                select(ResearchOrchestrationEvent).where(
                    ResearchOrchestrationEvent.orchestration_id == orchestration.id,
                    ResearchOrchestrationEvent.transition == "recovery_failed",
                )
            )
        )
        assert len(events) == 1
        assert events[0].payload_json["reason_code"] == "checkpoint_context_missing"


def test_worker_loads_local_env_before_database_import(tmp_path: Path) -> None:
    database_path = tmp_path / "worker.db"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"DATABASE_URL=sqlite:///{database_path}\n",
        encoding="utf-8",
    )
    backend = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment["APP_ENV"] = "development"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from app import env; "
                f"env.ENV_PATH = Path({str(env_path)!r}); "
                "from app.scripts import run_research_worker; "
                "from app.db import engine; "
                "print(engine.url)"
            ),
        ],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"sqlite:///{database_path}"


def test_worker_commits_failed_run_job_event_and_airun_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    from app.ai.client import LLMClient
    from app.models.ledger import (
        AIRun,
        Base,
        CaseDocumentVersion,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        Thesis,
    )
    from app.models.operational import Job, ResearchRun
    from app.models.research_monitor import ResearchRunEvent
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'worker-atomic.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="worker atomic failure",
            industry_topic="i",
            created_by="test",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        setup.add(
            Thesis(
                research_case_id=case.id,
                statement="Provider failure must fail the run",
                created_by="test",
                created_at=now,
            )
        )
        document = DocumentVersion(
            content_sha256="worker-atomic-document",
            source_url="https://example.test/worker-atomic",
            available_at=now,
            acquired_at=now,
            parser_version="test",
        )
        setup.add(document)
        setup.flush()
        setup.add_all(
            [
                SourceSpan(
                    document_version_id=document.id,
                    locator={"page": 1},
                    verbatim_text=(
                        "Management described sustained accelerator demand "
                        "and a longer order backlog."
                    ),
                ),
                CaseDocumentVersion(
                    research_case_id=case.id,
                    document_version_id=document.id,
                    linked_at=now,
                ),
            ]
        )
        setup.commit()
        run = AutoResearchService(setup).start(case.id, max_rounds=1, budget=10)
        run_id = run.id

    client = LLMClient(model_version="provider-test", mock=True)

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError("provider transport failed")

    monkeypatch.setattr(client, "chat_json", fail_provider)
    monkeypatch.setattr(LLMClient, "from_env", classmethod(lambda cls: client))
    monkeypatch.setattr(run_research_worker, "SessionLocal", session_local)
    original_completion = AutoResearchRepository.record_job_completion

    def assert_no_intermediate_terminal_commit(self, claim, **kwargs):
        with Session(engine) as check:
            persisted_run = check.get(ResearchRun, run_id)
            persisted_job = check.get(Job, claim.job_id)
            assert persisted_run is not None and persisted_run.status == "running"
            assert persisted_job is not None and persisted_job.status == "running"
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(AIRun)
                    .where(AIRun.status == "failed")
                )
                == 0
            )
        return original_completion(self, claim, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository,
        "record_job_completion",
        assert_no_intermediate_terminal_commit,
    )

    assert run_research_worker.run_once()

    with Session(engine) as check:
        persisted_run = check.get(ResearchRun, run_id)
        job = check.scalar(
            select(Job).where(
                Job.target_type == "research_run", Job.target_id == run_id
            )
        )
        assert persisted_run is not None
        assert persisted_run.status == "failed"
        assert persisted_run.stage == "failed"
        assert persisted_run.stop_reason == "task_failed"
        assert job is not None and job.status == "failed" and job.step == "failed"
        assert job.failure_count == 1
        assert (
            check.scalar(
                select(func.count())
                .select_from(AIRun)
                .where(AIRun.kind == "extract", AIRun.status == "failed")
            )
            == 1
        )
        event = check.scalar(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run_id)
            .where(ResearchRunEvent.stage == "failed")
        )
        assert event is not None and event.status == "failed"


def test_worker_job_error_does_not_persist_unhandled_exception_details(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.ledger import Base
    from app.models.operational import Job, ResearchRun
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-safe-error.db'}", future=True
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    def fail_execute(self, run, *, claim):
        raise RuntimeError(
            "upstream https://provider.invalid?token=sentinel-secret failed"
        )

    monkeypatch.setattr(AutoResearchService, "execute", fail_execute)

    with pytest.raises(RuntimeError, match="sentinel-secret"):
        run_research_worker.run_once()

    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        assert run is not None and run.status == "failed"
        assert job is not None and job.status == "failed"
        assert job.error == "AI operation failed"
        assert "sentinel-secret" not in job.error


def test_worker_stops_on_first_proposal_failure_and_commits_terminal_state_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    from app.ai.client import LLMClient
    from app.models.ledger import (
        AIRun,
        Base,
        CaseDocumentVersion,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        SourceStatement,
        Thesis,
    )
    from app.models.operational import Job, ResearchRun, ResearchTask
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker-proposal-failure-atomic.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="first provider failure is terminal",
            industry_topic="accelerator demand",
            created_by="test",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        first = Thesis(
            research_case_id=case.id,
            statement="accelerator demand growth",
            created_by="test",
            created_at=now,
        )
        second = Thesis(
            research_case_id=case.id,
            statement="accelerator demand backlog",
            created_by="test",
            created_at=now,
        )
        document = DocumentVersion(
            content_sha256="worker-proposal-failure-document",
            source_url="https://example.test/worker-proposal-failure",
            available_at=now,
            acquired_at=now,
            parser_version="test",
        )
        setup.add_all([first, second, document])
        setup.flush()
        span = SourceSpan(
            document_version_id=document.id,
            locator={"page": 1},
            verbatim_text="accelerator demand growth and backlog remain elevated",
        )
        setup.add(span)
        setup.flush()
        setup.add_all(
            [
                CaseDocumentVersion(
                    research_case_id=case.id,
                    document_version_id=document.id,
                    linked_at=now,
                ),
                SourceStatement(
                    source_span_id=span.id,
                    kind="research_opinion",
                    normalized_text=(
                        "accelerator demand growth and backlog remain elevated"
                    ),
                    created_at=now,
                ),
            ]
        )
        repo = AutoResearchRepository(setup)
        run = repo.create_run(
            research_case_id=case.id,
            max_rounds=1,
            budget=10,
            scope_thesis_ids=[str(first.id), str(second.id)],
        )
        first_task = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=first.id,
            task_type="support",
            query="first provider task",
        )
        second_task = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=second.id,
            task_type="support",
            query="must not execute after first failure",
        )
        job = repo.enqueue_run_job(run)
        setup.commit()
        run_id, job_id = run.id, job.id
        first_task_id, second_task_id = first_task.id, second_task.id

    client = LLMClient(model_version="provider-test", mock=True)
    provider_calls = 0

    def fail_provider(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise RuntimeError("provider transport failed")

    monkeypatch.setattr(client, "chat_json", fail_provider)
    monkeypatch.setattr(LLMClient, "from_env", classmethod(lambda cls: client))
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    original_completion = AutoResearchRepository.record_job_completion

    def assert_no_partial_failure_commit(self, claim, **kwargs):
        with Session(engine) as check:
            persisted_run = check.get(ResearchRun, run_id)
            persisted_job = check.get(Job, job_id)
            persisted_first = check.get(ResearchTask, first_task_id)
            assert persisted_run is not None and persisted_run.status == "running"
            assert persisted_job is not None and persisted_job.status == "running"
            assert persisted_first is not None and persisted_first.status == "running"
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(AIRun)
                    .where(AIRun.status == "failed")
                )
                == 0
            )
        return original_completion(self, claim, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository,
        "record_job_completion",
        assert_no_partial_failure_commit,
    )

    assert run_research_worker.run_once()
    assert provider_calls == 1

    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        first_task = check.get(ResearchTask, first_task_id)
        second_task = check.get(ResearchTask, second_task_id)
        assert run is not None and run.status == "failed"
        assert run.stage == "failed" and run.stop_reason == "task_failed"
        assert job is not None and job.status == "failed" and job.step == "failed"
        assert first_task is not None and first_task.status == "failed"
        assert second_task is not None and second_task.status == "queued"
        assert (
            check.scalar(
                select(func.count())
                .select_from(AIRun)
                .where(AIRun.kind == "propose", AIRun.status == "failed")
            )
            == 1
        )


@pytest.mark.parametrize(
    ("terminal_status", "terminal_stage", "terminal_reason"),
    [
        ("succeeded", "stopped", "max_rounds_reached"),
        ("failed", "failed", "task_failed"),
    ],
)
def test_dedicated_cancel_cannot_overwrite_a_committed_terminal_run(
    tmp_path: Path,
    monkeypatch,
    terminal_status,
    terminal_stage,
    terminal_reason,
) -> None:
    from app.models.ledger import Base
    from app.models.operational import Job, ResearchRun
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(
        f"sqlite:///{tmp_path / f'worker-cancel-race-{terminal_status}.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    def complete_run(self, run, *, claim):
        run.status = terminal_status
        run.stage = terminal_stage
        run.stop_reason = terminal_reason
        self.session.commit()

    monkeypatch.setattr(AutoResearchService, "execute", complete_run)
    original_completion = AutoResearchRepository.record_job_completion

    def cancel_before_completion(self, claim, **kwargs):
        with session_local() as cancelling:
            with pytest.raises(RuntimeError, match="terminal"):
                AutoResearchService(cancelling).cancel_run(
                    claim.research_run_id,
                    actor="user:test",
                    change_reason="test terminal cancellation race",
                )
        return original_completion(self, claim, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository,
        "record_job_completion",
        cancel_before_completion,
    )

    assert run_research_worker.run_once()

    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        assert run is not None
        assert run.status == terminal_status
        assert run.stage == terminal_stage
        assert run.stop_reason == terminal_reason
        assert job is not None
        assert job.status == terminal_status
        assert job.cancel_requested is False


def test_worker_discards_outputs_when_another_worker_already_terminalized_job(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.ledger import Base
    from app.models.operational import Job, ResearchRun, TaskItem
    from app.models.research_monitor import ResearchRunEvent
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'worker-lost-race.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    def complete_run(self, run, *, claim):
        run.status = "succeeded"
        run.stage = "stopped"
        run.stop_reason = "max_rounds_reached"
        self.session.commit()

    monkeypatch.setattr(AutoResearchService, "execute", complete_run)
    original_completion = AutoResearchRepository.record_job_completion

    def another_worker_wins(self, claim, **kwargs):
        with session_local() as winner:
            persisted_job = winner.get(Job, claim.job_id)
            assert persisted_job is not None
            persisted_job.status = "succeeded"
            persisted_job.step = "stopped"
            persisted_job.finished_at = datetime.now(timezone.utc)
            winner.commit()

        losing_run = kwargs["run"]
        losing_run.status = "failed"
        losing_run.stage = "failed"
        losing_run.stop_reason = "losing_worker"
        self._session.add_all(
            [
                ResearchRunEvent(
                    run_id=run_id,
                    seq=1,
                    stage="failed",
                    status="failed",
                    message="losing worker event",
                    payload_json={},
                    created_at=datetime.now(timezone.utc),
                ),
                TaskItem(
                    title="losing worker handoff",
                    description="must be rolled back",
                    status="open",
                    priority="normal",
                    task_type="inspect_job",
                    ref_type="research_run",
                    ref_id=run_id,
                    research_case_id=losing_run.research_case_id,
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        )
        return original_completion(self, claim, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository,
        "record_job_completion",
        another_worker_wins,
    )

    assert run_research_worker.run_once()

    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        assert run is not None and run.status == "succeeded"
        assert job is not None and job.status == "succeeded"
        assert (
            check.scalar(
                select(func.count())
                .select_from(ResearchRunEvent)
                .where(ResearchRunEvent.message == "losing worker event")
            )
            == 0
        )
        assert (
            check.scalar(
                select(func.count())
                .select_from(TaskItem)
                .where(TaskItem.title == "losing worker handoff")
            )
            == 0
        )


@pytest.mark.pg_only
def test_postgres_claim_and_cancel_share_case_run_job_lock_order(engine) -> None:
    """Five forced interleavings must finish without a Job/Run deadlock."""
    from app.models.operational import Job, ResearchRun
    from app.repositories.auto_research import AutoResearchRepository
    from app.services.auto_research import AutoResearchService

    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    for _ in range(5):
        run_id, job_id = _seed_terminal_race(sessions)
        claim_has_job_lock = Event()
        release_claim = Event()
        cancel_ready = Event()
        cancel_done = Event()
        errors: list[BaseException] = []
        claims = []
        cancel_pids: list[int] = []

        def pause_after_claim_job_lock(
            connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            normalized = " ".join(statement.casefold().split())
            if not connection.info.get("pause_claim_job_lock"):
                return
            if (
                " from jobs " not in f" {normalized} "
                or " for update" not in normalized
            ):
                return
            connection.info["pause_claim_job_lock"] = False
            claim_has_job_lock.set()
            if not release_claim.wait(timeout=5):
                raise AssertionError("claim lock interleaving was not released")

        event.listen(engine, "after_cursor_execute", pause_after_claim_job_lock)

        def claim_job() -> None:
            try:
                with sessions() as worker:
                    worker.connection().info["pause_claim_job_lock"] = True
                    worker.execute(text("SET LOCAL lock_timeout = '5s'"))
                    claims.append(AutoResearchRepository(worker).claim_next_run_job())
                    worker.commit()
            except BaseException as exc:
                errors.append(exc)

        def cancel_run() -> None:
            try:
                with sessions() as command:
                    command.execute(text("SET LOCAL lock_timeout = '5s'"))
                    cancel_pids.append(command.scalar(text("SELECT pg_backend_pid()")))
                    cancel_ready.set()
                    AutoResearchService(command).cancel_run(
                        run_id,
                        actor="user:lock-order-test",
                        change_reason="deterministic claim/cancel interleaving",
                    )
            except BaseException as exc:
                errors.append(exc)
            finally:
                cancel_done.set()

        claim_thread = Thread(target=claim_job)
        cancel_thread = Thread(target=cancel_run)
        try:
            claim_thread.start()
            assert claim_has_job_lock.wait(timeout=5)
            cancel_thread.start()
            assert cancel_ready.wait(timeout=5)
            assert _postgres_session_is_blocked(
                engine,
                pid=cancel_pids[0],
                done=cancel_done,
            )
            release_claim.set()
            claim_thread.join(timeout=10)
            cancel_thread.join(timeout=10)
        finally:
            release_claim.set()
            event.remove(engine, "after_cursor_execute", pause_after_claim_job_lock)

        assert not claim_thread.is_alive()
        assert not cancel_thread.is_alive()
        assert errors == []
        assert len(claims) == 1 and claims[0] is not None
        with sessions() as verification:
            run = verification.get(ResearchRun, run_id)
            job = verification.get(Job, job_id)
            assert run is not None and run.status == "cancelled"
            assert job is not None and job.status == "cancelled"
            assert job.cancel_requested is True


@pytest.mark.pg_only
@pytest.mark.parametrize("winner", ["cancel", "worker"])
@pytest.mark.parametrize(
    ("terminal_status", "terminal_stage", "terminal_reason"),
    [
        ("succeeded", "stopped", "max_rounds_reached"),
        ("failed", "failed", "task_failed"),
    ],
)
def test_postgres_worker_terminalization_serializes_public_job_cancel(
    engine,
    monkeypatch,
    winner,
    terminal_status,
    terminal_stage,
    terminal_reason,
) -> None:
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.models.operational import Job, ResearchRun, TaskItem
    from app.models.research_monitor import ResearchRunEvent
    from app.repositories.auto_research import AutoResearchRepository
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService
    from app.services.case_monitor import ResearchRunEventRepository
    from app.services.event_research_scope_evidence import lock_event_scope_case

    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)
    worker_at_terminal = Event()
    worker_has_job_lock = Event()
    cancel_started = Event()
    cancel_finished = Event()
    worker_errors: list[BaseException] = []
    cancel_responses = []

    def complete_run(self, run, *, claim):
        if winner == "cancel":
            # The dedicated cancellation command owns Case -> Run -> Job.
            # Let it commit before this worker opens its terminal transaction.
            worker_at_terminal.set()
            assert cancel_finished.wait(timeout=5)
        # Mirror execute()'s short terminal transaction: Case then Run are
        # already locked, while Job remains available to the public cancel.
        lock_event_scope_case(self.session, run.research_case_id)
        run.status = terminal_status
        run.stage = terminal_stage
        run.stop_reason = terminal_reason
        ResearchRunEventRepository(self.session).append(
            run.id,
            stage=terminal_stage,
            status=terminal_status,
            message="losing terminal event",
            payload_json={"must_rollback_if_cancelled": True},
        )
        self.session.add(
            TaskItem(
                title="losing terminal handoff",
                description="must roll back when cancellation wins",
                status="open",
                priority="normal",
                task_type="inspect_job",
                ref_type="research_run",
                ref_id=run.id,
                research_case_id=run.research_case_id,
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.flush()

    monkeypatch.setattr(AutoResearchService, "execute", complete_run)

    if winner == "worker":
        original_append = AutoResearchRepository._append_job_event

        def pause_with_terminal_lock(self, job, *, status, step, message):
            if status == terminal_status:
                worker_has_job_lock.set()
                assert cancel_started.wait(timeout=5)
                assert not cancel_finished.is_set()
            return original_append(
                self,
                job,
                status=status,
                step=step,
                message=message,
            )

        monkeypatch.setattr(
            AutoResearchRepository,
            "_append_job_event",
            pause_with_terminal_lock,
        )

    def override_db():
        with session_local() as request_session:
            yield request_session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team"}')

    def run_worker():
        try:
            run_research_worker.run_once()
        except BaseException as exc:
            worker_errors.append(exc)

    try:
        with TestClient(
            app,
            headers={"Authorization": "Bearer test-tenant-token"},
        ) as client:
            worker_thread = Thread(target=run_worker)
            worker_thread.start()
            if winner == "cancel":
                assert worker_at_terminal.wait(timeout=5)
                cancel_responses.append(
                    client.post(
                        f"/api/v1/research-runs/{run_id}/cancel",
                        json={"change_reason": "PostgreSQL terminal race"},
                    )
                )
                cancel_finished.set()
            else:
                assert worker_has_job_lock.wait(timeout=5)

                def request_cancel():
                    cancel_started.set()
                    cancel_responses.append(
                        client.post(
                            f"/api/v1/research-runs/{run_id}/cancel",
                            json={"change_reason": "PostgreSQL terminal race"},
                        )
                    )
                    cancel_finished.set()

                cancel_thread = Thread(target=request_cancel)
                cancel_thread.start()
                worker_thread.join(timeout=10)
                cancel_thread.join(timeout=10)
                assert not cancel_thread.is_alive()
            worker_thread.join(timeout=10)
            assert not worker_thread.is_alive()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert worker_errors == []
    assert len(cancel_responses) == 1
    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.get(Job, job_id)
        assert run is not None and job is not None
        if winner == "cancel":
            assert cancel_responses[0].status_code == 200
            assert run.status == "cancelled"
            assert run.stage == "stopped"
            assert run.stop_reason == "cancelled"
            assert job.status == "cancelled"
            assert job.cancel_requested is True
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(ResearchRunEvent)
                    .where(ResearchRunEvent.run_id == run_id)
                    .where(ResearchRunEvent.message == "losing terminal event")
                )
                == 0
            )
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(TaskItem)
                    .where(TaskItem.ref_id == run_id)
                    .where(TaskItem.title == "losing terminal handoff")
                )
                == 0
            )
        else:
            assert cancel_responses[0].status_code == 409
            assert run.status == terminal_status
            assert run.stage == terminal_stage
            assert run.stop_reason == terminal_reason
            assert job.status == terminal_status
            assert job.cancel_requested is False
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(ResearchRunEvent)
                    .where(ResearchRunEvent.run_id == run_id)
                    .where(ResearchRunEvent.message == "losing terminal event")
                )
                == 1
            )
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(TaskItem)
                    .where(TaskItem.ref_id == run_id)
                    .where(TaskItem.title == "losing terminal handoff")
                )
                == 1
            )
