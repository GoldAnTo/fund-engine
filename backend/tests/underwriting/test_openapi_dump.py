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
CANDIDATE_EVIDENCE_PATH = (
    "/api/underwriting/v1/research-versions/{revision_id}/candidate-evidence"
)
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
    "CandidateEvidenceDossierCanonicalPayloadResponse",
    "CandidateEvidenceDossierCanonicalItemResponse",
    "CandidateEvidenceParentResponse",
    "CandidateEvidenceItemResponse",
    "CandidateEvidenceReviewResponse",
    "CandidateEvidenceAnswerabilityResponse",
}
FORBIDDEN_RESEARCH_FIELDS = {
    "pe",
    "pb",
    "dcf",
    "price",
    "target",
    "buy",
    "sell",
    "stop",
    "position",
    "return",
    "valuation",
    "recommend",
    "action",
}
FORBIDDEN_BOUNDARY_FIELD_NAMES = {
    "action",
    "allowed_action",
    "requested_action",
    "research_disposition",
}
FORBIDDEN_BOUNDARY_ENUMS = {
    "eligible_for_probe_entry",
    "eligible_for_staged_entry",
    "do_not_enter",
}
PRODUCT_OPERATIONS = {
    "/api/underwriting/v1/product/objects": {"get"},
    "/api/underwriting/v1/product/projects": {"get", "post"},
    "/api/underwriting/v1/product/projects/{project_id}": {"get"},
    "/api/underwriting/v1/product/projects/{project_id}/mandates": {"post"},
    "/api/underwriting/v1/product/projects/{project_id}/scopes": {"post"},
    "/api/underwriting/v1/product/projects/{project_id}/agendas": {"post"},
    "/api/underwriting/v1/product/historical-bases": {"post"},
    "/api/underwriting/v1/product/market/price-snapshots": {"post"},
    "/api/underwriting/v1/product/market/fx-snapshots": {"post"},
    "/api/underwriting/v1/product/market/capital-structure-snapshots": {"post"},
    "/api/underwriting/v1/product/market/security-rights": {"post"},
    "/api/underwriting/v1/product/projects/{project_id}/draft": {"get", "patch"},
    "/api/underwriting/v1/product/projects/{project_id}/publication-preview": {"post"},
    "/api/underwriting/v1/product/projects/{project_id}/publish": {"post"},
    "/api/underwriting/v1/product/revisions/{revision_id}": {"get"},
}
PRODUCT_REQUEST_SCHEMAS = {
    "CreateResearchProjectRequest",
    "CreateProductMandateRequest",
    "CreateResearchScopeRequest",
    "CreateResearchAgendaRequest",
    "CreateProductHistoricalBasisRequest",
    "CreatePriceSnapshotRequest",
    "CreateFXSnapshotRequest",
    "CreateCapitalStructureSnapshotRequest",
    "CreateSecurityRightsRequest",
    "PatchWorkspaceDraftRequest",
    "PreviewProductRevisionRequest",
    "PublishProductRevisionRequest",
}
FORBIDDEN_PRODUCT_DECISION_FIELDS = {
    "target_price",
    "target",
    "action",
    "position",
    "recommendation",
    "recommend",
    "buy",
    "sell",
    "stop",
}


def _property_names(
    schema: object, schemas: dict[str, object], seen: set[str] | None = None
) -> set[str]:
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


def _enum_values(
    schema: object, schemas: dict[str, object], seen: set[str] | None = None
) -> set[str]:
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
    values = {value for value in schema.get("enum", []) if isinstance(value, str)}
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


def test_product_openapi_is_exact_strict_and_has_idempotency_header() -> None:
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]

    for path, methods in PRODUCT_OPERATIONS.items():
        assert path in openapi["paths"]
        assert set(openapi["paths"][path]) == methods
    for name in PRODUCT_REQUEST_SCHEMAS:
        assert schemas[name]["additionalProperties"] is False

    publish = openapi["paths"][
        "/api/underwriting/v1/product/projects/{project_id}/publish"
    ]["post"]
    idempotency = next(
        parameter
        for parameter in publish["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True
    assert idempotency["schema"]["minLength"] == 1
    assert idempotency["schema"]["maxLength"] == 120

    names: set[str] = set()
    for name in PRODUCT_REQUEST_SCHEMAS | {
        "PublicationPreviewResponse",
        "ProductRevisionResponse",
    }:
        names.update(_property_names(schemas[name], schemas))
    assert not ({name.lower() for name in names} & FORBIDDEN_PRODUCT_DECISION_FIELDS)
    assert {
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "capital_structure_snapshot_id",
        "security_rights_ids",
    } <= set(schemas["ProductRevisionResponse"]["properties"])


def test_product_openapi_paths_follow_all_legacy_underwriting_paths() -> None:
    paths = tuple(app.openapi()["paths"])
    product_indexes = tuple(
        index
        for index, path in enumerate(paths)
        if path.startswith("/api/underwriting/v1/product/")
    )
    legacy_indexes = tuple(
        index
        for index, path in enumerate(paths)
        if path.startswith("/api/underwriting/v1/")
        and not path.startswith("/api/underwriting/v1/product/")
    )

    assert product_indexes
    assert legacy_indexes
    assert min(product_indexes) > max(legacy_indexes)


def test_agenda_generator_response_openapi_preserves_sha256_constraint() -> None:
    response_schema = app.openapi()["components"]["schemas"]["AgendaGeneratorResponse"]
    input_summary_hash = response_schema["properties"]["input_summary_hash"]

    assert {"type": "string", "pattern": r"^[0-9a-f]{64}$"} in input_summary_hash[
        "anyOf"
    ]
    assert "input_summary_hash" in response_schema["required"]


def test_publication_preview_manifest_openapi_is_closed_and_typed() -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert schemas["PublicationPreviewResponse"]["properties"]["manifest"] == {
        "$ref": "#/components/schemas/ProductManifestPreviewResponse"
    }
    for name in (
        "ProductManifestPreviewResponse",
        "ProductManifestProjectRefResponse",
        "ProductManifestMembershipRefResponse",
    ):
        assert schemas[name]["additionalProperties"] is False
    assert set(schemas["ProductManifestProjectRefResponse"]["properties"]) == {
        "project_id",
        "content_hash",
    }
    assert set(schemas["ProductManifestMembershipRefResponse"]["properties"]) == {
        "membership_id",
        "security_id",
        "content_hash",
    }
    assert set(schemas["ProductManifestPreviewResponse"]["properties"]) == {
        "schema_version",
        "project_id",
        "project_ref",
        "project_membership_refs",
        "primary_object_id",
        "boundary_ref",
        "mandate_id",
        "scope_id",
        "agenda_id",
        "historical_basis_id",
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "capital_structure_snapshot_id",
        "security_rights_ids",
        "market_snapshot_refs",
        "model_refs",
        "assessment_ref",
        "memo_ref",
        "parent_revision_id",
    }


def test_revision_read_contract_has_only_get_operations_and_no_decision_fields() -> (
    None
):
    openapi = app.openapi()

    assert REVISION_PATHS | {ARCHIVE_PATH, BOUNDARY_PATH} <= set(openapi["paths"])
    assert all(set(openapi["paths"][path]) == {"get"} for path in REVISION_PATHS)
    assert set(openapi["paths"][ARCHIVE_PATH]) == {"get"}
    assert set(openapi["paths"][BOUNDARY_PATH]) == {"get"}
    schemas = openapi["components"]["schemas"]
    for name in REVISION_SCHEMAS | ARCHIVE_SCHEMAS | BOUNDARY_SCHEMAS:
        properties = schemas[name]["properties"]
        assert not (
            set(field.lower() for field in properties) & FORBIDDEN_RESEARCH_FIELDS
        )
    assert schemas["ResearchArchiveItemResponse"]["properties"]["object_kind"] == {
        "type": "string",
        "enum": ["industry", "company", "security"],
        "title": "Object Kind",
    }


def test_boundary_contract_binds_identity_and_exposes_only_typed_frozen_fields() -> (
    None
):
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]
    boundary = schemas["ResearchRevisionBoundaryResponse"]

    assert {
        "revision_id",
        "object_id",
        "basis_id",
        "version_kind",
        "content_hash",
        "cutoff",
        "source_manifest_hash",
        "answerability",
        "unknown_evidence_gaps",
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


def test_candidate_evidence_contract_is_get_only_and_excludes_formal_outputs_recursively() -> (
    None
):
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
        "reviewer_identity",
        "reviewer_role",
        "decision",
        "rationale",
        "reviewed_at",
    }
    assert set(schemas["CandidateEvidenceAnswerabilityResponse"]["properties"]) == {
        "schema_version",
        "reference",
        "content_hash",
        "state",
        "research_debt_keys",
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
    assert dossier["properties"]["supersedes_id"] == {
        "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}],
        "title": "Supersedes Id",
    }
    assert {"supersedes_id", "canonical_payload"} <= set(dossier["required"])
    canonical_payload = schemas["CandidateEvidenceDossierCanonicalPayloadResponse"]
    assert canonical_payload["additionalProperties"] is False
    assert set(canonical_payload["properties"]) == {
        "object_id",
        "basis_id",
        "source_manifest_id",
        "dossier_key",
        "version",
        "scope_statement",
        "status",
        "purpose",
        "items",
        "rejected_calculations",
        "source_manifest_hash",
        "created_at",
        "supersedes_id",
    }
    assert canonical_payload["properties"]["items"] == {
        "items": {
            "$ref": "#/components/schemas/CandidateEvidenceDossierCanonicalItemResponse"
        },
        "type": "array",
        "minItems": 1,
        "title": "Items",
    }
    parent = schemas["CandidateEvidenceParentResponse"]
    assert set(parent["properties"]) == {
        "schema_version",
        "reference",
        "artifact_type",
        "identity",
        "content_hash",
        "descriptor_preimage",
    }
    assert parent["properties"]["descriptor_preimage"] == {
        "anyOf": [
            {
                "$ref": "#/components/schemas/CandidateEvidenceDossierParentPreimageResponse"
            },
            {
                "$ref": "#/components/schemas/CandidateEvidenceReviewParentPreimageResponse"
            },
            {
                "$ref": "#/components/schemas/CandidateEvidenceManifestParentPreimageResponse"
            },
        ],
        "title": "Descriptor Preimage",
    }
    assert schemas["CandidateEvidenceResponse"]["properties"]["parent_refs"] == {
        "items": {"$ref": "#/components/schemas/CandidateEvidenceParentResponse"},
        "type": "array",
        "title": "Parent Refs",
    }


def test_revision_history_identity_contract_includes_research_object_identity() -> None:
    """A history identifies its immutable persisted research object."""
    openapi = app.openapi()
    properties = openapi["components"]["schemas"]["ResearchRevisionHistoryResponse"][
        "properties"
    ]

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
        "市盈率",
        "市净率",
        "目标价",
        "买入",
        "卖出",
        "止损",
        "仓位",
        "估值",
        "推荐",
        "操作",
    }
    assert not {term for term in forbidden_copy if term in ui_source}
