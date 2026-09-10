from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "scripts" / "live-stack-control.mjs"
TOKEN = "test-control-token-1234567890"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(port: int, path: str, *, token: str | None = TOKEN):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST",
        headers={} if token is None else {"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@contextmanager
def _control_server(tmp_path: Path, *, timeout_ms: int, sleep_seconds: float):
    stub = tmp_path / "research-stack-stub.sh"
    stub.write_text(
        f'#!/bin/sh\nprintf started > "$LIVE_STACK_STARTED_FILE"\nsleep {sleep_seconds}\n',
        encoding="utf-8",
    )
    stub.chmod(0o700)
    port = _free_port()
    environment = {
        **os.environ,
        "LIVE_CONTROL_TOKEN": TOKEN,
        "LIVE_CONTROL_PORT": str(port),
        "LIVE_CONTROL_COMMAND_TIMEOUT_MS": str(timeout_ms),
        "LIVE_STACK_SCRIPT": str(stub),
        "LIVE_STACK_STARTED_FILE": str(tmp_path / "stack-started"),
    }
    process = subprocess.Popen(
        ["node", str(CONTROL)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=0.2
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("live control did not start")
        yield port
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_live_runner_uses_an_isolated_stack_and_always_collects_evidence() -> None:
    runner = (ROOT / "scripts" / "run-live-acceptance.sh").read_text(
        encoding="utf-8"
    )

    assert "COMPOSE_PROJECT_NAME" in runner
    assert "fund-engine-live-" in runner
    assert 'export COMPOSE_PROJECT_NAME="fund-engine-live-' in runner
    assert "${COMPOSE_PROJECT_NAME:-" not in runner
    assert "umask 077" in runner
    assert "LIVE_CONTROL_URL" in runner
    assert "live-stack-control.mjs" in runner
    assert "docker compose" in runner
    assert "logs --no-color" in runner
    assert "down --volumes" in runner
    assert "trap cleanup EXIT" in runner
    assert "teardown_status=$?" in runner
    assert "summary_status=1" in runner
    assert "final_status=1" in runner
    assert "Final runner status" in runner
    assert "psql" not in runner.lower()
    assert "DATABASE_URL" not in runner


def test_restart_control_accepts_only_the_four_declared_services() -> None:
    control = (ROOT / "scripts" / "live-stack-control.mjs").read_text(
        encoding="utf-8"
    )

    for service in ("api", "acquisition-worker", "research-worker", "scheduler"):
        assert f'"{service}"' in control
    assert "LIVE_CONTROL_TOKEN" in control
    assert "research-stack.sh" in control
    assert '"ps", "--all", "-q", service' in control
    assert "psql" not in control.lower()
    assert "DATABASE_URL" not in control


def test_restart_control_enforces_auth_allowlist_and_single_flight(tmp_path) -> None:
    with _control_server(tmp_path, timeout_ms=2_000, sleep_seconds=0.5) as port:
        assert _request(port, "/restart/api", token=None)[0] == 401
        assert _request(port, "/restart/postgres")[0] == 404
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_request, port, "/restart/acquisition-worker")
            for _ in range(50):
                if (tmp_path / "stack-started").exists():
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("first restart never entered the stack command")
            second_status, _second = _request(
                port, "/restart/acquisition-worker"
            )
            first_status, first_body = first.result(timeout=5)
        assert second_status == 409
        assert first_status == 200
        assert first_body["result"] == "container_healthy_after_restart"


def test_restart_control_only_allows_scheduler_to_be_stopped(tmp_path) -> None:
    with _control_server(tmp_path, timeout_ms=2_000, sleep_seconds=0) as port:
        assert _request(port, "/stop/api")[0] == 404
        status, body = _request(port, "/stop/scheduler")

    assert status == 200
    assert body["operation"] == "stop"
    assert body["service"] == "scheduler"
    assert body["result"] == "stopped"


def test_restart_control_timeout_is_redacted(tmp_path) -> None:
    with _control_server(tmp_path, timeout_ms=100, sleep_seconds=1) as port:
        status, body = _request(port, "/restart/research-worker")

    assert status == 500
    assert body["error"] == "restart failed; inspect the server-side live-control log"
    assert "Command failed" not in json.dumps(body)
