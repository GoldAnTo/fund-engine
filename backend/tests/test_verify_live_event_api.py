from __future__ import annotations

import subprocess
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_live_event_api


def test_live_event_api_readiness_probe_handles_empty_http_error_body(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        url="http://127.0.0.1:9999/api/v1/event-research",
        code=503,
        msg="service starting",
        hdrs=None,
        fp=BytesIO(),
    )

    def raise_startup_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(verify_live_event_api.urllib.request, "urlopen", raise_startup_error)

    status, response = verify_live_event_api._request(
        "http://127.0.0.1:9999/api/v1/event-research"
    )

    assert status == 503
    assert response == {}


def test_live_event_api_readiness_probe_handles_timed_out_response(monkeypatch) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("server is still starting")

    monkeypatch.setattr(verify_live_event_api.urllib.request, "urlopen", raise_timeout)

    status, response = verify_live_event_api._request(
        "http://127.0.0.1:9999/api/v1/event-research"
    )

    assert status == 0
    assert response == {}


def test_live_event_api_verifier_creates_and_reads_a_tenant_scoped_case() -> None:
    script = Path(__file__).parents[1] / "scripts" / "verify_live_event_api.py"

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parents[1],
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS: authenticated live API created and listed the same Case" in result.stdout


def test_live_event_api_verifier_reports_server_startup_failure(monkeypatch) -> None:
    popen = subprocess.Popen

    def failed_server(_command, **kwargs):
        return popen(
            [sys.executable, "-c", "import sys; print('startup-failure-sentinel', file=sys.stderr); sys.exit(17)"],
            **kwargs,
        )

    monkeypatch.setattr(verify_live_event_api.subprocess, "Popen", failed_server)
    monkeypatch.setattr(verify_live_event_api.subprocess, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(verify_live_event_api, "_request", lambda *_a, **_k: (0, {}))

    with pytest.raises(RuntimeError, match="startup-failure-sentinel"):
        verify_live_event_api.main()


@pytest.mark.parametrize("ready_after, succeeds", [(40, True), (400, False)])
def test_live_event_api_readiness_uses_a_bounded_startup_deadline(monkeypatch, ready_after, succeeds) -> None:
    clock = [0.0]
    probes = [0]

    def request(_url):
        probes[0] += 1
        return (200 if probes[0] > ready_after else 503), {}

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(verify_live_event_api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(verify_live_event_api.time, "sleep", sleep)
    monkeypatch.setattr(verify_live_event_api, "_request", request)
    process = SimpleNamespace(poll=lambda: None)

    if succeeds:
        verify_live_event_api._wait_until_ready(process, "http://127.0.0.1/api/v1/event-research")
        assert probes[0] == 41
    else:
        with pytest.raises(RuntimeError, match="did not become ready.*503"):
            verify_live_event_api._wait_until_ready(process, "http://127.0.0.1/api/v1/event-research")
        assert 30 <= clock[0] < 30.2
