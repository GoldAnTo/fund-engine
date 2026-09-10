from __future__ import annotations

import os
import signal
import subprocess
import sys
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest

from scripts import verify_live_event_api


class _FakeMonotonicClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


class _RunningProcess:
    def poll(self) -> None:
        return None


def _run_verifier(script: Path) -> tuple[int, str, str]:
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=script.parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate(timeout=3)
        pytest.fail(
            "live verifier exceeded 45 seconds; process group was terminated\n"
            f"stdout tail: {stdout[-2000:]}\n"
            f"stderr tail: {stderr[-2000:]}"
        )
    return process.returncode, stdout, stderr


def test_live_event_api_readiness_probe_handles_empty_http_error_body(
    monkeypatch,
) -> None:
    error = urllib.error.HTTPError(
        url="http://127.0.0.1:9999/api/v1/event-research",
        code=503,
        msg="service starting",
        hdrs=None,
        fp=BytesIO(),
    )

    observed: dict[str, float] = {}

    def raise_startup_error(*_args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        raise error

    monkeypatch.setattr(
        verify_live_event_api.urllib.request, "urlopen", raise_startup_error
    )

    status, response = verify_live_event_api._request(
        "http://127.0.0.1:9999/api/v1/event-research",
        token="signed-test-token",
        timeout=0.25,
    )

    assert status == 503
    assert response == {}
    assert observed["timeout"] == 0.25


def test_live_event_api_readiness_probe_handles_timed_out_response(monkeypatch) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("server is still starting")

    monkeypatch.setattr(verify_live_event_api.urllib.request, "urlopen", raise_timeout)

    status, response = verify_live_event_api._request(
        "http://127.0.0.1:9999/api/v1/event-research",
        token="signed-test-token",
        timeout=0.25,
    )

    assert status == 0
    assert response == {}


def test_live_event_api_readiness_uses_an_absolute_bounded_deadline() -> None:
    clock = _FakeMonotonicClock()
    started_at = clock()
    request_timeouts: list[float] = []

    def unavailable_request(
        _url: str,
        *,
        token: str,
        timeout: float,
    ) -> tuple[int, dict]:
        assert token == "signed-test-token"
        request_timeouts.append(timeout)
        clock.advance(timeout)
        raise urllib.error.URLError("not listening")

    status = verify_live_event_api._wait_until_ready(
        base_url="http://127.0.0.1:9999/api/v1",
        token="signed-test-token",
        process=_RunningProcess(),
        startup_timeout=1.25,
        monotonic_clock=clock,
        request_func=unavailable_request,
        sleep_func=clock.sleep,
    )

    elapsed = clock() - started_at
    assert status == 0
    assert elapsed == pytest.approx(1.25)
    assert request_timeouts
    assert max(request_timeouts) <= 0.5
    assert all(timeout > 0 for timeout in request_timeouts)
    assert all(0 < seconds <= 0.1 for seconds in clock.sleeps)
    assert verify_live_event_api.STARTUP_TIMEOUT_SECONDS <= 10
    assert verify_live_event_api.STARTUP_TIMEOUT_SECONDS < 45


def test_live_event_api_verifier_creates_and_reads_a_tenant_scoped_case(
    monkeypatch,
) -> None:
    script = Path(__file__).parents[1] / "scripts" / "verify_live_event_api.py"
    source = script.read_text()

    assert 'env.pop("RESEARCH_TENANT_TOKENS", None)' in source
    assert '"RESEARCH_TENANT_TOKENS":' not in source
    assert "ThreadingHTTPServer" in source
    assert "jwt.encode" in source

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"ambient":"must-be-removed"}')
    child_env = verify_live_event_api._child_environment(
        database_url="sqlite:///ignored.db",
        issuer="http://127.0.0.1:12345",
        jwks_url="http://127.0.0.1:12345/jwks",
    )
    assert "RESEARCH_TENANT_TOKENS" not in child_env

    returncode, stdout, stderr = _run_verifier(script)

    assert returncode == 0, stderr
    assert "PASS: authenticated live API created and listed the same Case" in stdout
