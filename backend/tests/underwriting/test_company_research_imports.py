from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_company_research_domain_imports_in_a_clean_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.underwriting.domain.company_research",
        ],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
