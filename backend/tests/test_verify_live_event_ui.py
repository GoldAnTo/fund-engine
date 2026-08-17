from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_live_event_ui_verifier_keeps_reviewed_case_pages_working_through_default_http_client() -> None:
    """Use live HTTP/browser adapters with an explicit external-provider fake."""
    frontend = Path(__file__).parents[2] / "frontend"
    script = frontend / "scripts" / "verify-live-event-ui.mjs"
    node = shutil.which("node")
    assert node, "Node.js is required for the live frontend verifier"

    result = subprocess.run(
        [node, "scripts/with-project-node.mjs", "scripts/verify-live-event-ui.mjs"],
        cwd=frontend,
        env={**os.environ, "PYTHON": sys.executable, "PW_BROWSER_CHANNEL": "chrome"},
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS: default frontend operated a seeded reviewed Case, configured and registered a market factor and reviewed company-stock-fund chain, replayed a transparent fund-disclosure failure, ran, paused its future schedule, and listed the same Case through the live API" in result.stdout
