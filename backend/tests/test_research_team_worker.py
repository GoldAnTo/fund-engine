"""Offline professional DAG execution against real admitted evidence and SDK models."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from openai.types.chat import ChatCompletion
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.ai.client import LLMClient
from app.models.ledger import Base
from app.models.operational import ResearchRun
from app.models.research_team import (
    ProfessionalAttempt,
    ProfessionalEvent,
    ProfessionalOutput,
    ProfessionalTask,
    ResearchTeam,
)
from app.services.research_team import ResearchTeamService
from app.services.research_team_generation import QUALITY_CHECKS
from tests.test_research_gateway_artifact_authorization import (
    complete_authorized_evidence,
)


@pytest.fixture
def worker_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'professional-worker.sqlite'}",
                           connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as session:
        spec, run, links = complete_authorized_evidence(session)
        ResearchTeamService(session).ensure_team(spec)
        session.commit()
        ids = SimpleNamespace(spec=spec.id, run=run.id, links=[link.id for link in links])
    sessions = []

    def factory():
        session = maker()
        sessions.append(session)
        return session

    yield SimpleNamespace(factory=factory, ids=ids, sessions=sessions, engine=engine)
    engine.dispose()


class RoleProvider:
    def __init__(self, callback=None, mutate=None):
        self.chat = SimpleNamespace(completions=self)
        self.calls = []
        self.callback, self.mutate = callback, mutate

    def create(self, **kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        self.calls.append(payload)
        if self.callback:
            self.callback(payload)
        evidence = payload["evidence"][0]
        body = {
            "summary": f"{payload['role']}：材料形成的研究草案",
            "findings": [{"statement": "公开材料描述了经营情况", "basis": "supported",
                          "citations": [{"evidence_link_id": evidence["evidence_link_id"],
                                         "quote": evidence["quote"]}]}],
            "gaps": ["仍需核验来源独立性"], "limitations": ["未经人工审核"],
            "checks": ([{"kind": kind, "status": "warning", "detail": "仍需人工核验",
                        "task_ids": [item["task_id"] for item in payload["dependencies"]]}
                        for kind in sorted(QUALITY_CHECKS)] if payload["role"] == "quality" else []),
        }
        if self.mutate:
            self.mutate(body)
        response = ChatCompletion.model_validate({
            "id": f"chatcmpl-{len(self.calls)}", "model": "offline-professional-v1", "created": 0,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                                                       "message": {"role": "assistant", "content": json.dumps(body)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        })
        response._request_id = f"req-{payload['role']}"
        return response


def make_worker(db, provider=None, **kwargs):
    from app.services.research_team_worker import ProfessionalWorker

    provider = provider or RoleProvider()
    client = LLMClient(model_version="offline", client=provider, max_attempts=1)
    return ProfessionalWorker(db.factory, client_factory=lambda: client, **kwargs), provider


def rows(db, model):
    with db.factory() as session:
        return list(session.scalars(select(model)))


def test_four_roles_execute_distinct_tasks_with_frozen_dependencies_and_usage(worker_db):
    worker, provider = make_worker(worker_db)
    assert all(worker.run_once() for _ in range(4))
    assert not worker.run_once()
    tasks, outputs, attempts = rows(worker_db, ProfessionalTask), rows(worker_db, ProfessionalOutput), rows(worker_db, ProfessionalAttempt)
    assert {task.role for task in tasks if task.status == "succeeded"} == {"industry", "finance", "strategy", "quality"}
    assert len(outputs) == len({output.task_id for output in outputs}) == 4
    assert len(attempts) == 4
    assert all(attempt.details["total_tokens"] == 30 for attempt in attempts)
    assert all(attempt.details["model"] == "offline-professional-v1" for attempt in attempts)
    assert all(attempt.details["provider_request_id"].startswith("req-") for attempt in attempts)
    for payload in provider.calls:
        assert payload["frozen_scope"] and payload["frozen_cutoff"]
        dependencies = {item["role"] for item in payload["dependencies"]}
        expected = {"strategy": {"industry", "finance"}, "quality": {"industry", "finance", "strategy"}}.get(payload["role"], set())
        assert dependencies == expected
        assert all(item["output_id"] and item["content"] for item in payload["dependencies"])
    assert all(output.input_sha256 and output.evidence_manifest for output in outputs)
    events = sorted(rows(worker_db, ProfessionalEvent), key=lambda event: event.sequence)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_provider_runs_after_claim_transaction_is_closed(worker_db):
    def check(payload):
        assert not any(session.in_transaction() for session in worker_db.sessions)
        with worker_db.factory() as session:
            task = session.get(ProfessionalTask, UUID(payload["task_id"]))
            assert task.status == "running" and task.lease_token

    worker, _ = make_worker(worker_db, RoleProvider(callback=check))
    assert worker.run_once()


def test_two_workers_claim_independent_roles_concurrently(worker_db):
    barrier = Barrier(2, timeout=10)
    provider = RoleProvider(callback=lambda _: barrier.wait())
    workers = [make_worker(worker_db, provider)[0] for _ in range(2)]
    results, failures = [], []

    def execute(worker):
        try:
            results.append(worker.run_once())
        except Exception as exc:  # noqa: BLE001 - collect concurrent worker failures for the assertion below
            failures.append(exc)

    threads = [Thread(target=execute, args=(worker,)) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not failures
    assert results == [True, True]
    assert {payload["role"] for payload in provider.calls} == {"industry", "finance"}
    assert len({payload["task_id"] for payload in provider.calls}) == 2
    assert len(rows(worker_db, ProfessionalOutput)) == 2


@pytest.mark.parametrize("native_status", ["queued", "running", "waiting_for_sources", "waiting_for_review"])
def test_native_nonterminal_status_waits_before_professional_claim(worker_db, native_status):
    with worker_db.factory() as session:
        run = session.get(ResearchRun, worker_db.ids.run)
        run.status = native_status
        session.commit()
    worker, provider = make_worker(worker_db)
    assert worker.run_once()
    assert provider.calls == []
    tasks = rows(worker_db, ProfessionalTask)
    assert any(
        task.status == "blocked" and task.reason_code == "waiting_for_native_completion"
        for task in tasks
    )
    assert rows(worker_db, ProfessionalOutput) == []


def test_stale_lease_is_reclaimed_with_new_token_and_attempt(worker_db):
    expired = datetime.now(UTC) - timedelta(seconds=1)
    with worker_db.factory() as session:
        task = session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == "industry"))
        task.status, task.attempt, task.lease_token, task.lease_expires_at = "running", 1, uuid4(), expired
        task_id = task.id
        session.commit()
    worker, _ = make_worker(worker_db)
    for _ in range(2):
        worker.run_once()
    with worker_db.factory() as session:
        task = session.get(ProfessionalTask, task_id)
        assert task.status == "succeeded"
        assert task.attempt == 2
        attempt = session.scalar(select(ProfessionalAttempt).where(ProfessionalAttempt.task_id == task_id))
        assert attempt.task_attempt == 2


@pytest.mark.parametrize("state", ["paused", "cancelled"])
def test_control_during_provider_fences_late_output_but_keeps_usage(worker_db, state):
    def change_team(payload):
        with worker_db.factory() as session:
            session.get(ResearchTeam, worker_db.ids.spec).status = state
            task = session.get(ProfessionalTask, UUID(payload["task_id"]))
            task.status = "queued" if state == "paused" else "cancelled"
            task.lease_token = task.lease_expires_at = None
            session.commit()

    worker, _ = make_worker(worker_db, RoleProvider(callback=change_team))
    assert worker.run_once()
    assert rows(worker_db, ProfessionalOutput) == []
    assert len(rows(worker_db, ProfessionalAttempt)) == 1
    assert not worker.run_once()


def test_native_cancel_during_publish_cancels_claimed_task_immediately(worker_db):
    def cancel_native(_):
        with worker_db.factory() as session:
            session.get(ResearchRun, worker_db.ids.run).status = "cancelled"
            session.commit()

    worker, _ = make_worker(worker_db, RoleProvider(callback=cancel_native))
    assert worker.run_once()
    tasks = rows(worker_db, ProfessionalTask)
    assert any(
        task.status == "cancelled" and task.reason_code == "native_cancelled"
        for task in tasks
    )
    assert rows(worker_db, ProfessionalOutput) == []
    assert len(rows(worker_db, ProfessionalAttempt)) == 1


def test_source_revoked_during_model_call_cannot_publish_output(worker_db):

    def revoke(_):
        with worker_db.factory() as session:
            session.execute(text("UPDATE source_contracts SET allow_ai_processing = 0"))
            session.commit()

    worker, _ = make_worker(worker_db, RoleProvider(callback=revoke))
    assert worker.run_once()
    assert rows(worker_db, ProfessionalOutput) == []
    assert len(rows(worker_db, ProfessionalAttempt)) == 1
    assert any(task.reason_code == "input_authorization_changed" for task in rows(worker_db, ProfessionalTask))


def test_fake_citation_fails_role_and_is_metered(worker_db):
    def invent(body):
        body["findings"][0]["citations"][0]["evidence_link_id"] = str(uuid4())

    worker, _ = make_worker(worker_db, RoleProvider(mutate=invent))
    assert worker.run_once()
    assert rows(worker_db, ProfessionalOutput) == []
    assert any(task.status == "failed" and task.reason_code == "invalid_model_result" for task in rows(worker_db, ProfessionalTask))
    assert rows(worker_db, ProfessionalAttempt)[0].details["outcome"] == "schema_error"


def test_latest_revision_only_is_eligible(worker_db):
    with worker_db.factory() as session:
        old = session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == "industry"))
        old_id = old.id
        latest = ProfessionalTask(run_spec_id=worker_db.ids.spec, revision=2, role="industry", instruction="复核需求")
        session.add(latest)
        session.get(ResearchTeam, worker_db.ids.spec).revision = 2
        session.commit()
        latest_id = latest.id
    worker, provider = make_worker(worker_db)
    for _ in range(2):
        worker.run_once()
    assert str(old_id) not in {payload["task_id"] for payload in provider.calls}
    assert str(latest_id) in {payload["task_id"] for payload in provider.calls}


@pytest.mark.parametrize("native_status", ["failed", "cancelled"])
def test_native_terminal_failure_never_generates_professional_success(worker_db, native_status):
    with worker_db.factory() as session:
        session.get(ResearchRun, worker_db.ids.run).status = native_status
        session.commit()
    worker, provider = make_worker(worker_db)
    worker.run_once()
    assert provider.calls == []
    assert rows(worker_db, ProfessionalOutput) == []
    assert not worker.run_once()  # unchanged blocking does not churn events


def test_no_authorized_evidence_blocks_without_model_call_and_recovers(worker_db):

    with worker_db.factory() as session:
        session.execute(text("UPDATE source_contracts SET allow_ai_processing = 0"))
        session.commit()
    worker, provider = make_worker(worker_db)
    assert worker.run_once()
    assert provider.calls == []
    assert not worker.run_once()
    with worker_db.factory() as session:
        session.execute(text("UPDATE source_contracts SET allow_ai_processing = 1"))
        session.commit()
    assert worker.run_once()
    assert len(provider.calls) == 1


def test_directed_followup_uses_immutable_prior_same_role_output(worker_db):
    from app.api.v1.tenant_context import ResearchActor
    from app.models.research_gateway import ResearchRunSpec

    worker, provider = make_worker(worker_db)
    assert all(worker.run_once() for _ in range(4))
    with worker_db.factory() as session:
        old = session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == "industry"))
        old_id = old.id
        spec = session.get(ResearchRunSpec, worker_db.ids.spec)
        ResearchTeamService(session).message(
            ResearchActor(spec.tenant_id, frozenset(), spec.subject_id),
            spec.conversation_id, spec.id, text="上一版结论依据是什么", recipient="industry",
            expected_revision=1, idempotency_key="followup-old-output",
        )
    assert all(worker.run_once() for _ in range(3))
    assert len(provider.calls) == 7
    followup = provider.calls[4]
    assert followup["role"] == "industry"
    assert followup["dependencies"][0]["task_id"] == str(old_id)
    assert followup["dependencies"][0]["content"]["summary"]


def test_quality_payload_describes_each_parent_dependency_contract(worker_db):
    from app.api.v1.tenant_context import ResearchActor
    from app.models.research_gateway import ResearchRunSpec
    from app.models.research_team import ProfessionalDependency

    worker, provider = make_worker(worker_db)
    assert all(worker.run_once() for _ in range(4))
    with worker_db.factory() as session:
        spec = session.get(ResearchRunSpec, worker_db.ids.spec)
        original_quality = session.scalar(
            select(ProfessionalTask).where(ProfessionalTask.role == "quality")
        )
        ResearchTeamService(session).message(
            ResearchActor(spec.tenant_id, frozenset(), spec.subject_id),
            spec.conversation_id, spec.id, text="复核上一版质控判断", recipient="quality",
            expected_revision=1, idempotency_key="followup-quality-context",
        )
        expected_parent_deps = {
            str(task.id): sorted(
                str(value)
                for value in session.scalars(
                    select(ProfessionalDependency.parent_task_id)
                    .where(ProfessionalDependency.task_id == task.id)
                )
            )
            for task in session.scalars(
                select(ProfessionalTask).where(ProfessionalTask.run_spec_id == spec.id)
            )
        }
        original_quality_id = str(original_quality.id)

    assert worker.run_once()
    quality_payload = provider.calls[4]
    assert quality_payload["role"] == "quality"
    dependencies = {
        item["task_id"]: item for item in quality_payload["dependencies"]
    }
    assert set(dependencies) == set(quality_payload["dependency_ids"])
    assert original_quality_id in dependencies
    for task_id, item in dependencies.items():
        assert item["dependency_ids"] == expected_parent_deps[task_id]
        contract = item["check_contract"]
        if item["role"] == "quality":
            assert contract == {
                "requires_checks": True,
                "required_check_kinds": sorted(QUALITY_CHECKS),
                "task_ids_must_be_nonempty": True,
                "task_ids_must_cover_dependency_ids": True,
            }
        else:
            assert contract == {
                "requires_checks": False,
                "required_check_kinds": [],
                "task_ids_must_be_nonempty": False,
                "task_ids_must_cover_dependency_ids": False,
            }


def test_retried_directed_quality_payload_keeps_old_successful_output(worker_db):
    from app.api.v1.tenant_context import ResearchActor
    from app.models.research_gateway import ResearchRunSpec

    worker, provider = make_worker(worker_db)
    assert all(worker.run_once() for _ in range(4))
    with worker_db.factory() as session:
        spec = session.get(ResearchRunSpec, worker_db.ids.spec)
        original_quality = session.scalar(
            select(ProfessionalTask).where(ProfessionalTask.role == "quality")
        )
        original_output = session.scalar(
            select(ProfessionalOutput).where(ProfessionalOutput.task_id == original_quality.id)
        )
        ResearchTeamService(session).message(
            ResearchActor(spec.tenant_id, frozenset(), spec.subject_id),
            spec.conversation_id, spec.id, text="复核上一版质控判断", recipient="quality",
            expected_revision=1, idempotency_key="quality-directed-retry-context",
        )
        failed = session.scalar(
            select(ProfessionalTask).where(ProfessionalTask.role == "quality", ProfessionalTask.revision == 2)
        )
        failed.status = "failed"
        session.commit()
        ResearchTeamService(session).command(
            ResearchActor(spec.tenant_id, frozenset(), spec.subject_id),
            spec.conversation_id, spec.id, kind="retry", expected_revision=2,
            task_id=failed.id, idempotency_key="quality-directed-retry",
        )
        original_quality_id = str(original_quality.id)
        original_output_id = str(original_output.id)
        failed_id = str(failed.id)

    assert worker.run_once()
    payload = provider.calls[4]
    dependencies = {item["task_id"]: item for item in payload["dependencies"]}
    assert original_quality_id in dependencies
    assert dependencies[original_quality_id]["output_id"] == original_output_id
    assert failed_id not in dependencies
    assert payload["instruction"] == "复核上一版质控判断"


def test_lease_heartbeat_keeps_slow_provider_from_becoming_stale(worker_db):
    import time

    observed = []

    def slow(payload):
        time.sleep(1.1)
        with worker_db.factory() as session:
            task = session.get(ProfessionalTask, UUID(payload["task_id"]))
            expiry = task.lease_expires_at.replace(tzinfo=UTC)
            observed.append(expiry > datetime.now(UTC))

    worker, _ = make_worker(worker_db, RoleProvider(callback=slow), lease_seconds=1)
    assert worker.run_once()
    assert observed == [True]
    assert len(rows(worker_db, ProfessionalOutput)) == 1


def test_library_heartbeat_failure_fences_output_without_killing_host(worker_db, monkeypatch):
    import time

    worker, _ = make_worker(worker_db, RoleProvider(callback=lambda _: time.sleep(0.45)), lease_seconds=1)
    original = worker._renew
    calls = []

    def fail_after_initial(claim):
        calls.append(claim)
        if len(calls) > 1:
            raise RuntimeError("sentinel-private-db-failure")
        return original(claim)

    monkeypatch.setattr(worker, "_renew", fail_after_initial)
    assert worker.run_once()
    assert len(calls) == 2
    assert rows(worker_db, ProfessionalOutput) == []
    assert any(task.reason_code == "lease_renewal_failed" for task in rows(worker_db, ProfessionalTask))


def test_provider_failure_is_fixed_safe_reason_with_unknown_usage(worker_db):
    import httpx

    def fail(_):
        raise httpx.ConnectError("sentinel-private-provider-secret")

    worker, _ = make_worker(worker_db, RoleProvider(callback=fail))
    assert worker.run_once()
    tasks, attempts = rows(worker_db, ProfessionalTask), rows(worker_db, ProfessionalAttempt)
    assert any(task.reason_code == "llm_provider_error" for task in tasks)
    assert len(attempts) == 1
    assert attempts[0].details["usage_status"] == "unknown"
    assert attempts[0].details["total_tokens"] is None
    assert "sentinel" not in json.dumps(attempts[0].details)


def test_failed_parent_blocks_downstream_without_additional_model_call(worker_db):
    with worker_db.factory() as session:
        task = session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == "industry"))
        task.status, task.reason_code = "failed", "llm_provider_error"
        session.commit()
    worker, provider = make_worker(worker_db)
    assert worker.run_once()  # finance is independently runnable
    worker.run_once()
    assert len(provider.calls) == 1
    tasks = rows(worker_db, ProfessionalTask)
    assert all(task.status == "blocked" for task in tasks if task.role in {"strategy", "quality"})


def test_expired_inflight_result_is_discarded_and_usage_survives(worker_db):
    current = datetime.now(UTC)

    def finish_late(_):
        nonlocal current
        current += timedelta(seconds=5)

    worker, _ = make_worker(worker_db, RoleProvider(callback=finish_late), lease_seconds=2, clock=lambda: current)
    assert worker.run_once()
    assert rows(worker_db, ProfessionalOutput) == []
    assert len(rows(worker_db, ProfessionalAttempt)) == 1


def test_cli_once_dispatches_without_loop_and_only_cli_injects_fatal_exit(monkeypatch):
    from app.scripts import run_professional_worker as script

    constructed, calls = [], []

    class Worker:
        def __init__(self, factory, **kwargs):
            constructed.append(kwargs)

        def run_once(self):
            calls.append(True)
            return True

    monkeypatch.setattr(script, "ProfessionalWorker", Worker)
    assert script.main(["--once", "--lease-seconds", "120"]) == 0
    assert calls == [True]
    assert constructed[0]["lease_seconds"] == 120
    assert callable(constructed[0]["fatal_handler"])


def test_professional_loop_heartbeat_reports_normal_and_expired_health(tmp_path, monkeypatch):
    from app.models.ledger import Base
    from app.scripts import check_worker_heartbeat
    from app.scripts import run_professional_worker as script
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    engine = create_engine(f"sqlite:///{tmp_path / 'professional-loop-health.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(script, "SessionLocal", sessions)
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    monkeypatch.setenv("PROFESSIONAL_WORKER_ID", "professional-container")

    calls = []

    class Worker:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self):
            calls.append(True)
            if len(calls) == 1:
                return False
            raise KeyboardInterrupt

    monkeypatch.setattr(script, "ProfessionalWorker", Worker)

    assert script.main(["--loop", "--lease-seconds", "120", "--poll-seconds", "0.1"]) == 0
    assert calls == [True, True]
    arguments = [
        "--worker-kind",
        "professional_team",
        "--worker-id",
        "professional-container",
        "--max-age-seconds",
        "30",
    ]
    assert check_worker_heartbeat.main(arguments) == 0

    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="professional-container",
            worker_kind="professional_team",
            mode="loop",
            state="polling",
            seen_at=datetime.now(UTC) - timedelta(seconds=31),
        )
        session.commit()

    assert check_worker_heartbeat.main(arguments) == 1


def test_professional_loop_marks_sustained_failures_unhealthy(tmp_path, monkeypatch, caplog):
    from app.models.ledger import Base
    from app.scripts import check_worker_heartbeat
    from app.scripts import run_professional_worker as script
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    engine = create_engine(f"sqlite:///{tmp_path / 'professional-loop-failure.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(script, "SessionLocal", sessions)
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    monkeypatch.setenv("PROFESSIONAL_WORKER_ID", "professional-container")

    calls = []

    class Worker:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self):
            calls.append(True)
            raise RuntimeError("sentinel private provider detail")

    monkeypatch.setattr(script, "ProfessionalWorker", Worker)

    assert script.main([
        "--loop",
        "--lease-seconds",
        "120",
        "--poll-seconds",
        "0.1",
        "--max-consecutive-failures",
        "2",
    ]) == 1
    assert calls == [True, True]
    assert "sentinel" not in caplog.text

    with sessions() as session:
        status = WorkerHeartbeatService(session).status(
            worker_kind="professional_team",
            worker_id="professional-container",
        )
    assert status["status"] != "available"

    assert check_worker_heartbeat.main([
        "--worker-kind",
        "professional_team",
        "--worker-id",
        "professional-container",
        "--max-age-seconds",
        "30",
    ]) == 1
