from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


def _seed_terminal_race(session_local):
    from app.models.ledger import ResearchCase
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
        from tests.tenant_admission import admit_case
        admit_case(setup, case.id)
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
    monkeypatch.setattr(
        run_research_worker.MonitorScheduler, "dispatch_due", lambda _self: []
    )
    monkeypatch.setattr(
        run_research_worker.FundDisclosureSyncScheduler,
        "dispatch_due",
        lambda _self: [],
    )


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
    monkeypatch.setattr(
        run_research_worker.MonitorScheduler, "dispatch_due", lambda _self: []
    )
    monkeypatch.setattr(
        run_research_worker.FundDisclosureSyncScheduler,
        "dispatch_due",
        lambda _self: [],
    )
    original_completion = AutoResearchRepository.record_job_completion

    def assert_no_intermediate_terminal_commit(self, job, **kwargs):
        with Session(engine) as check:
            persisted_run = check.get(ResearchRun, run_id)
            persisted_job = check.get(Job, job.id)
            assert persisted_run is not None and persisted_run.status == "running"
            assert persisted_job is not None and persisted_job.status == "running"
            assert check.scalar(
                select(func.count()).select_from(AIRun).where(AIRun.status == "failed")
            ) == 0
        original_completion(self, job, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository,
        "record_job_completion",
        assert_no_intermediate_terminal_commit,
    )

    assert run_research_worker.run_once()

    with Session(engine) as check:
        persisted_run = check.get(ResearchRun, run_id)
        job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert persisted_run is not None
        assert persisted_run.status == "failed"
        assert persisted_run.stage == "failed"
        assert persisted_run.stop_reason == "task_failed"
        assert job is not None and job.status == "failed" and job.step == "failed"
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(
                AIRun.kind == "extract", AIRun.status == "failed"
            )
        ) == 1
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

    engine = create_engine(f"sqlite:///{tmp_path / 'worker-safe-error.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    run_id, job_id = _seed_terminal_race(session_local)
    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    def fail_execute(self, run):
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


def test_automatic_worker_parks_polls_without_spin_and_resumes_when_sources_finish(
    tmp_path: Path, monkeypatch
) -> None:
    from app.models.acquisition import AcquisitionJob
    from app.models.ledger import Base
    from app.models.operational import Job, ResearchRun
    from app.schemas.v1.event_research import CreateEventResearchRequest
    from app.scripts import run_research_worker
    from app.services.auto_research import AutoResearchService
    from app.services.event_research import EventResearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'automatic-worker.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as setup:
        created = EventResearchService(setup).create(
            CreateEventResearchRequest(
                raw_input="公司订单与产能出现重要变化。",
                source_type="pasted_snapshot",
                source_metadata={
                    "authority_level": "user_supplied",
                    "permissions": {"ai_processing": True, "display": True},
                },
                event_title="自动研究工作器测试",
                research_question="订单变化是否持续？",
                candidate_factors=["需求", "供给", "替代解释"],
                research_protocol_required=False,
                created_by="tenant:worker-team",
            ),
            tenant_id="worker-team",
            workflow_mode="automatic",
        )
        assert created.run_id is not None
        run_id = uuid.UUID(created.run_id)

    _patch_worker_dependencies(monkeypatch, run_research_worker, session_local)

    assert run_research_worker.run_once()
    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        research_job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        source_jobs = list(
            check.scalars(
                select(AcquisitionJob).where(AcquisitionJob.research_run_id == run_id)
            )
        )
        assert run is not None and run.status == "waiting_for_sources"
        assert run.stage == "retrieve"
        assert research_job is not None and research_job.status == "waiting_for_sources"
        assert research_job.attempt == 1
        assert len(source_jobs) == 9

    assert run_research_worker.run_once() is False
    with Session(engine) as check:
        research_job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert research_job is not None
        assert research_job.status == "waiting_for_sources"
        assert research_job.attempt == 1
        for source_job in check.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run_id)
        ):
            source_job.status = "succeeded"
        check.commit()

    resumed_stages: list[str] = []

    def finish_resumed_automatic_run(self, run):
        resumed_stages.append(run.stage)
        run.status = "succeeded"
        run.stage = "stopped"
        run.stop_reason = "test_resume_complete"

    monkeypatch.setattr(AutoResearchService, "execute", finish_resumed_automatic_run)
    assert run_research_worker.run_once()
    assert resumed_stages == ["analyze"]
    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        research_job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert run is not None and run.status == "succeeded"
        assert research_job is not None and research_job.status == "succeeded"
        assert research_job.attempt == 2


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
    from app.services.auto_research import AutoResearchService

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

    def assert_no_partial_failure_commit(self, job, **kwargs):
        with Session(engine) as check:
            persisted_run = check.get(ResearchRun, run_id)
            persisted_job = check.get(Job, job_id)
            persisted_first = check.get(ResearchTask, first_task_id)
            assert persisted_run is not None and persisted_run.status == "running"
            assert persisted_job is not None and persisted_job.status == "running"
            assert persisted_first is not None and persisted_first.status == "running"
            assert check.scalar(
                select(func.count()).select_from(AIRun).where(AIRun.status == "failed")
            ) == 0
        original_completion(self, job, **kwargs)

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
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(
                AIRun.kind == "propose", AIRun.status == "failed"
            )
        ) == 1


@pytest.mark.parametrize(
    ("terminal_status", "terminal_stage", "terminal_reason"),
    [
        ("succeeded", "stopped", "max_rounds_reached"),
        ("failed", "failed", "task_failed"),
    ],
)
def test_worker_terminalization_honors_a_committed_job_cancellation(
    tmp_path: Path,
    monkeypatch,
    terminal_status,
    terminal_stage,
    terminal_reason,
) -> None:
    from app.api.v1.jobs import cancel_job
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

    def complete_run(self, run):
        run.status = terminal_status
        run.stage = terminal_stage
        run.stop_reason = terminal_reason
        self.session.commit()

    monkeypatch.setattr(AutoResearchService, "execute", complete_run)
    original_completion = AutoResearchRepository.record_job_completion

    def cancel_before_completion(self, job, **kwargs):
        with session_local() as cancelling:
            response = cancel_job(job.id, db=cancelling, tenant_id="test-team")
            assert response.cancel_requested is True
        original_completion(self, job, **kwargs)

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
        assert run.status == "cancelled"
        assert run.stage == "stopped"
        assert run.stop_reason == "cancelled"
        assert job is not None
        assert job.status == "cancelled"
        assert job.cancel_requested is True


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

    def complete_run(self, run):
        run.status = "succeeded"
        run.stage = "stopped"
        run.stop_reason = "max_rounds_reached"
        self.session.commit()

    monkeypatch.setattr(AutoResearchService, "execute", complete_run)
    original_completion = AutoResearchRepository.record_job_completion

    def another_worker_wins(self, job, **kwargs):
        with session_local() as winner:
            persisted_job = winner.get(Job, job.id)
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
        original_completion(self, job, **kwargs)

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
        assert check.scalar(
            select(func.count())
            .select_from(ResearchRunEvent)
            .where(ResearchRunEvent.message == "losing worker event")
        ) == 0
        assert check.scalar(
            select(func.count())
            .select_from(TaskItem)
            .where(TaskItem.title == "losing worker handoff")
        ) == 0


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
    session,  # Own fixture teardown for rows committed by independent workers.
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

    def complete_run(self, run):
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

    if winner == "cancel":
        original_completion = AutoResearchRepository.record_job_completion

        def pause_before_terminal_lock(self, job, **kwargs):
            worker_at_terminal.set()
            assert cancel_finished.wait(timeout=5)
            return original_completion(self, job, **kwargs)

        monkeypatch.setattr(
            AutoResearchRepository,
            "record_job_completion",
            pause_before_terminal_lock,
        )
    else:
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
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team"}'
    )

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
                    client.post(f"/api/v1/jobs/{job_id}/cancel")
                )
                cancel_finished.set()
            else:
                assert worker_has_job_lock.wait(timeout=5)

                def request_cancel():
                    cancel_started.set()
                    cancel_responses.append(
                        client.post(f"/api/v1/jobs/{job_id}/cancel")
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
            assert check.scalar(
                select(func.count())
                .select_from(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == run_id)
                .where(ResearchRunEvent.message == "losing terminal event")
            ) == 0
            assert check.scalar(
                select(func.count())
                .select_from(TaskItem)
                .where(TaskItem.ref_id == run_id)
                .where(TaskItem.title == "losing terminal handoff")
            ) == 0
        else:
            assert cancel_responses[0].status_code == 409
            assert run.status == terminal_status
            assert run.stage == terminal_stage
            assert run.stop_reason == terminal_reason
            assert job.status == terminal_status
            assert job.cancel_requested is False
            assert check.scalar(
                select(func.count())
                .select_from(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == run_id)
                .where(ResearchRunEvent.message == "losing terminal event")
            ) == 1
            assert check.scalar(
                select(func.count())
                .select_from(TaskItem)
                .where(TaskItem.ref_id == run_id)
                .where(TaskItem.title == "losing terminal handoff")
            ) == 1
