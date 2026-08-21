"""The OpenAPI dump must import this worktree's application package."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_dump_openapi_includes_underwriting_routes() -> None:
    backend = Path(__file__).parents[2]
    subprocess.run(
        [sys.executable, "scripts/dump_openapi.py"],
        cwd=backend,
        check=True,
    )
    openapi = json.loads((backend.parent / "frontend" / "openapi.json").read_text())
    assert "/api/underwriting/v1/objects" in openapi["paths"]
    assert "UnderwritingErrorEnvelope" in openapi["components"]["schemas"]
