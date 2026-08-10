from __future__ import annotations

from io import BytesIO
import subprocess
import sys
import urllib.error
from pathlib import Path

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
