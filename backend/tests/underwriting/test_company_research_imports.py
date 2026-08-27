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


def test_company_research_repository_barrel_exports_in_a_clean_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    script = """
from app.underwriting.persistence import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError as ConcreteIntegrityError,
    CompanyResearchRepository as ConcreteRepository,
)
assert CompanyResearchRepository is ConcreteRepository
assert CompanyResearchIntegrityError is ConcreteIntegrityError
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
