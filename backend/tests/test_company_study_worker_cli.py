"""Dedicated worker loops publish the existing durable heartbeat and fail safely."""

from pathlib import Path


def test_company_worker_once_and_loop_have_bounded_failure(monkeypatch):
    from app.scripts import run_company_study_worker as cli

    calls = []

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            calls.append("run")
            raise RuntimeError("provider detail must stay private")

    class Heartbeat:
        def __init__(self, **kwargs):
            assert kwargs["worker_kind"] == "company_study"

        def start(self):
            calls.append("heartbeat-start")

        def stop(self):
            calls.append("heartbeat-stop")

    monkeypatch.setattr(cli, "CompanyStudyWorker", Worker)
    monkeypatch.setattr(cli, "WorkerHeartbeatPublisher", Heartbeat)
    monkeypatch.setattr(cli, "_mark_loop_failed", lambda *args: calls.append("failed"))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    assert cli.main(["--once"]) == 1
    assert cli.main(["--loop", "--max-consecutive-failures", "2"]) == 1
    assert calls == ["run", "heartbeat-start", "run", "run", "heartbeat-stop", "failed"]


def test_gateway_compose_starts_private_dossier_worker():
    compose = (Path(__file__).parents[2] / "docker-compose.gateway.yml").read_text()
    assert "company-study-worker:" in compose
    assert "app.scripts.run_company_study_worker" in compose
    assert "--worker-kind company_study" in compose
