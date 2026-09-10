"""Current Gateway HTTP/SSE acceptance; legacy Case browser UI is retired."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_gateway_delivery_streams_authenticated_http_without_legacy_routes() -> None:
    frontend = Path(__file__).parents[2] / "frontend"
    node = shutil.which("node")
    assert node, "Node.js is required for the Gateway HTTP acceptance"
    result = subprocess.run(
        [node, "--test", "--test-reporter=tap", "server/gatewayServer.test.mjs"],
        cwd=frontend,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SSE sends a live frame" in result.stdout
    assert "deny legacy" in result.stdout
    assert "fail 0" in result.stdout
