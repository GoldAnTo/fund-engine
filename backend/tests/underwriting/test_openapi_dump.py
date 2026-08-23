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
BOUNDARY_PATH = "/api/underwriting/v1/research-versions/{revision_id}/boundary"
CANDIDATE_EVIDENCE_PATH = "/api/underwriting/v1/research-versions/{revision_id}/candidate-evidence"
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
BOUNDARY_SCHEMAS = {
    "ResearchRevisionBoundaryResponse",
    "FrozenAnswerabilityResponse",
    "FrozenUnknownEvidenceGapResponse",
}
CANDIDATE_EVIDENCE_SCHEMAS = {
    "CandidateEvidenceResponse",
    "CandidateEvidenceDossierResponse",
    "CandidateEvidenceItemResponse",
    "CandidateEvidenceReviewResponse",
    "CandidateEvidenceAnswerabilityResponse",
}
FORBIDDEN_RESEARCH_FIELDS = {
    "pe", "pb", "dcf", "price", "target", "buy", "sell", "stop", "position",
    "return", "valuation", "recommend", "action",
}
FORBIDDEN_BOUNDARY_FIELD_NAMES = {
    "action", "allowed_action", "requested_action", "research_disposition",
}
FORBIDDEN_BOUNDARY_ENUMS = {
    "eligible_for_probe_entry", "eligible_for_staged_entry", "do_not_enter",
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


def _enum_values(schema: object, schemas: dict[str, object], seen: set[str] | None = None) -> set[str]:
    """Collect enum literals through the public frozen-boundary response tree."""
    if not isinstance(schema, dict):
        return set()
    seen = seen if seen is not None else set()
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        if name in seen:
            return set()
        seen.add(name)
        return _enum_values(schemas[name], schemas, seen)
    values = {
        value for value in schema.get("enum", [])
        if isinstance(value, str)
    }
    for key in ("properties", "items", "allOf", "anyOf", "oneOf"):
        child = schema.get(key)
        if isinstance(child, dict):
            for item in child.values() if key == "properties" else (child,):
                values.update(_enum_values(item, schemas, seen))
        elif isinstance(child, list):
            for item in child:
                values.update(_enum_values(item, schemas, seen))
    return values


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

    assert REVISION_PATHS | {ARCHIVE_PATH, BOUNDARY_PATH} <= set(openapi["paths"])
    assert all(set(openapi["paths"][path]) == {"get"} for path in REVISION_PATHS)
    assert set(openapi["paths"][ARCHIVE_PATH]) == {"get"}
    assert set(openapi["paths"][BOUNDARY_PATH]) == {"get"}
    schemas = openapi["components"]["schemas"]
    for name in REVISION_SCHEMAS | ARCHIVE_SCHEMAS | BOUNDARY_SCHEMAS:
        properties = schemas[name]["properties"]
        assert not (set(field.lower() for field in properties) & FORBIDDEN_RESEARCH_FIELDS)
    assert schemas["ResearchArchiveItemResponse"]["properties"]["object_kind"] == {
        "type": "string",
        "enum": ["industry", "company", "security"],
        "title": "Object Kind",
    }


def test_boundary_contract_binds_identity_and_exposes_only_typed_frozen_fields() -> None:
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]
    boundary = schemas["ResearchRevisionBoundaryResponse"]

    assert {
        "revision_id", "object_id", "basis_id", "version_kind", "content_hash",
        "cutoff", "source_manifest_hash", "answerability", "unknown_evidence_gaps",
    } <= set(boundary["properties"])
    assert boundary["additionalProperties"] is False
    assert schemas["FrozenAnswerabilityResponse"]["additionalProperties"] is False
    assert schemas["FrozenUnknownEvidenceGapResponse"]["additionalProperties"] is False
    names: set[str] = set()
    for name in BOUNDARY_SCHEMAS:
        names.update(_property_names(schemas[name], schemas))
    assert not (set(name.lower() for name in names) & FORBIDDEN_RESEARCH_FIELDS)
    assert not (set(name.lower() for name in names) & FORBIDDEN_BOUNDARY_FIELD_NAMES)
    enum_values: set[str] = set()
    for name in BOUNDARY_SCHEMAS:
        enum_values.update(_enum_values(schemas[name], schemas))
    assert not (enum_values & FORBIDDEN_BOUNDARY_ENUMS)


def test_candidate_evidence_contract_is_get_only_and_excludes_formal_outputs_recursively() -> None:
    """A reviewed candidate stays research evidence, not a decision surface."""
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]

    assert set(openapi["paths"][CANDIDATE_EVIDENCE_PATH]) == {"get"}
    for name in CANDIDATE_EVIDENCE_SCHEMAS:
        assert schemas[name]["additionalProperties"] is False
    names: set[str] = set()
    for name in CANDIDATE_EVIDENCE_SCHEMAS:
        names.update(_property_names(schemas[name], schemas))
    forbidden = {"action", "entry", "price", "valuation", "recommendation"}
    assert not (set(name.lower() for name in names) & forbidden)
    assert "decision" not in set(schemas["CandidateEvidenceResponse"]["properties"])
    assert set(schemas["CandidateEvidenceReviewResponse"]["properties"]) >= {
        "reviewer_identity", "reviewer_role", "decision", "rationale", "reviewed_at",
    }
    assert set(schemas["CandidateEvidenceAnswerabilityResponse"]["properties"]) == {
        "schema_version", "reference", "content_hash", "state", "research_debt_keys",
        "resolution_requirements",
    }
    dossier = schemas["CandidateEvidenceDossierResponse"]
    assert dossier["properties"]["rejected_calculations"] == {
        "items": {"type": "string"},
        "type": "array",
        "minItems": 1,
        "title": "Rejected Calculations",
    }
    assert "rejected_calculations" in dossier["required"]


def test_revision_history_identity_contract_includes_research_object_identity() -> None:
    """A history identifies its immutable persisted research object."""
    openapi = app.openapi()
    properties = openapi["components"]["schemas"]["ResearchRevisionHistoryResponse"]["properties"]

    assert {"object_kind", "canonical_name", "external_key"} <= set(properties)


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
