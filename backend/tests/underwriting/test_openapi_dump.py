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
ARCHIVE_PATH = "/api/underwriting/v1/research-archives"
REVISION_SCHEMAS = {
    "ResearchRevisionArtifactResponse",
    "ResearchRevisionResponse",
    "ResearchRevisionHistoryResponse",
    "ResearchRevisionChangeResponse",
    "ResearchRevisionDiffResponse",
}
ARCHIVE_SCHEMAS = {
    "ResearchArchiveItemResponse",
    "ResearchArchiveListResponse",
}
FORBIDDEN_RESEARCH_FIELDS = {
    "pe", "pb", "dcf", "price", "target", "buy", "sell", "stop", "position",
    "return", "valuation", "recommend", "action",
}


def _property_names(schema: object, schemas: dict[str, object], seen: set[str] | None = None) -> set[str]:
    """Collect response-object field names through archive read-model refs only."""
    if not isinstance(schema, dict):
        return set()
    seen = seen if seen is not None else set()
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        if name in seen:
            return set()
        seen.add(name)
        return _property_names(schemas[name], schemas, seen)
    names = set(schema.get("properties", {}))
    for key in ("items", "allOf", "anyOf", "oneOf"):
        child = schema.get(key)
        if isinstance(child, list):
            for item in child:
                names.update(_property_names(item, schemas, seen))
        else:
            names.update(_property_names(child, schemas, seen))
    return names


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

    assert REVISION_PATHS | {ARCHIVE_PATH} <= set(openapi["paths"])
    assert all(set(openapi["paths"][path]) == {"get"} for path in REVISION_PATHS)
    assert set(openapi["paths"][ARCHIVE_PATH]) == {"get"}
    schemas = openapi["components"]["schemas"]
    for name in REVISION_SCHEMAS | ARCHIVE_SCHEMAS:
        properties = schemas[name]["properties"]
        assert not (set(field.lower() for field in properties) & FORBIDDEN_RESEARCH_FIELDS)
    assert schemas["ResearchArchiveItemResponse"]["properties"]["object_kind"] == {
        "type": "string",
        "enum": ["industry", "company", "security"],
        "title": "Object Kind",
    }


def test_archive_contract_and_ui_sources_do_not_introduce_investment_fields() -> None:
    """The archive is evidence infrastructure, never an action or valuation surface."""
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]
    names: set[str] = set()
    for name in REVISION_SCHEMAS | ARCHIVE_SCHEMAS:
        names.update(_property_names(schemas[name], schemas))
    assert not (set(name.lower() for name in names) & FORBIDDEN_RESEARCH_FIELDS)

    frontend = Path(__file__).parents[3] / "frontend" / "src"
    sources = [
        frontend / "data" / "underwritingResearchApi.ts",
        frontend / "features" / "underwriting" / "ResearchArchivePage.tsx",
    ]
    ui_source = "\n".join(source.read_text(encoding="utf-8") for source in sources)
    forbidden_copy = {
        "市盈率", "市净率", "目标价", "买入", "卖出", "止损", "仓位", "估值", "推荐", "操作",
    }
    assert not {term for term in forbidden_copy if term in ui_source}
