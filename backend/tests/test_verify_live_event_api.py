from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
