"""The OpenAPI dump must import this worktree's application package."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.main import app


REVISION_PATHS = {
    "/api/underwriting/v1/objects/{object_id}/research-versions/{version_kind}",
    "/api/underwriting/v1/research-versions/{revision_id}",
    "/api/underwriting/v1/research-versions/{from_revision_id}/diff/{to_revision_id}",
}
REVISION_SCHEMAS = {
    "ResearchRevisionArtifactResponse",
    "ResearchRevisionResponse",
    "ResearchRevisionHistoryResponse",
    "ResearchRevisionChangeResponse",
    "ResearchRevisionDiffResponse",
}
FORBIDDEN_RESEARCH_FIELDS = {
    "pe", "pb", "dcf", "price", "target", "buy", "sell", "stop", "position",
    "return", "valuation", "recommend", "action",
}


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


def test_revision_read_contract_has_only_get_operations_and_no_decision_fields() -> None:
    openapi = app.openapi()

    assert REVISION_PATHS <= set(openapi["paths"])
    assert all(set(openapi["paths"][path]) == {"get"} for path in REVISION_PATHS)
    schemas = openapi["components"]["schemas"]
    for name in REVISION_SCHEMAS:
        properties = schemas[name]["properties"]
        assert not (set(field.lower() for field in properties) & FORBIDDEN_RESEARCH_FIELDS)
