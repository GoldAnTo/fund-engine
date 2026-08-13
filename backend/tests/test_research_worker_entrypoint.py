from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


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
