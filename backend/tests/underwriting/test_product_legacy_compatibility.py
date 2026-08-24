"""Characterize the legacy research-revision boundary before product work."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.persistence.repository import UnderwritingRepository
from app.underwriting.services.catl_baseline import CatlBaselineService
from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)
_CATL_EVIDENCE_ONLY_CONTENT_HASH = (
    "0d0c88563feff55c09df7dbc5d3c473cd7f8102adc653b1a8cd545f994280119"
)
_CATL_ANSWERABILITY_CONTENT_HASH = (
    "d1ae2c14341477aaaa375dd52eef63293182ad821498a3ce3daa53ac589f201b"
)
_LEGACY_SERVICE_MODULES = (
    "__init__.py",
    "kernel.py",
    "research_revision_diff.py",
    "revision_parent_seal.py",
)
_SERVICES_PACKAGE = "app.underwriting.services"
_FORBIDDEN_PRODUCT_IMPORTS = frozenset({
    "app.underwriting.services.revision_publisher",
    "app.underwriting.services.workspace_draft",
})


@pytest.fixture
def catl_revision(session: Session):
    return CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())


def test_legacy_catl_revision_replays_deterministically_without_product_boundaries(
    session: Session,
    api_client,
    catl_revision,
) -> None:
    """The evidence-only fixture keeps its frozen legacy read boundary."""
    reader = ResearchRevisionDiffService(session)
    revision_id = catl_revision.research_version.id

    before_summary = reader.revision_summary(revision_id)
    before_boundary = reader.revision_boundary(revision_id)
    before_summary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}",
    )
    before_boundary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/boundary",
    )

    for response in (before_summary_response, before_boundary_response):
        assert response.status_code == 200, (
            f"{response.request.url} returned {response.status_code}: {response.text}"
        )

    successor = UnderwritingRepository(session).append_research_version(
        object_id=catl_revision.company.id,
        basis_id=catl_revision.basis.id,
        version_kind=catl_revision.research_version.version_kind,
        content_hash=catl_revision.research_version.content_hash,
        parent_ids=list(catl_revision.research_version.parent_ids),
        expected_parent_id=revision_id,
        created_at=NOW,
    )
    after_summary = reader.revision_summary(revision_id)
    repeated_summary = reader.revision_summary(revision_id)
    after_boundary = reader.revision_boundary(revision_id)
    repeated_boundary = reader.revision_boundary(revision_id)
    first_diff = reader.revision_diff(revision_id, successor.id)
    second_diff = reader.revision_diff(revision_id, successor.id)

    assert before_summary == after_summary == repeated_summary
    assert before_boundary == after_boundary == repeated_boundary
    assert first_diff == second_diff
    assert (
        before_summary.id,
        before_summary.object_id,
        before_summary.basis_id,
        before_summary.version_kind,
        before_summary.sequence,
        before_summary.content_hash,
        before_summary.cutoff,
        before_summary.source_manifest_hash,
    ) == (
        revision_id,
        catl_revision.company.id,
        catl_revision.basis.id,
        "catl_economic_model_evidence_only",
        1,
        _CATL_EVIDENCE_ONLY_CONTENT_HASH,
        NOW,
        catl_revision.basis.source_manifest_hash,
    )
    assert catl_revision.basis.price_as_of is None
    assert before_summary.parent_refs == tuple(sorted(
        before_summary.parent_refs,
        key=lambda ref: (ref.artifact_type, ref.identity, ref.reference),
    ))
    assert {
        (ref.artifact_type, ref.identity, ref.status)
        for ref in before_summary.parent_refs
    } >= {
        ("answerability", "answerability", "not_answerable"),
        ("semantic_snapshot", f"semantic_snapshot:{catl_revision.snapshot_hash}", "semantic_snapshot"),
        ("source_manifest", "catl-2024-economic-basis|1", "frozen"),
    }
    assert len(before_summary.parent_refs) == 48
    assert next(
        ref for ref in before_summary.parent_refs if ref.artifact_type == "source_manifest"
    ).reference == str(catl_revision.source_manifest.id)
    assert before_boundary.answerability is not None
    assert (
        before_boundary.revision,
        before_boundary.answerability.reference,
        before_boundary.answerability.content_hash,
        before_boundary.answerability.state,
        before_boundary.answerability.blockers,
        before_boundary.answerability.research_debt_keys,
        before_boundary.answerability.resolvable_within_mandate,
        before_boundary.answerability.resolution_requirements,
        before_boundary.unknown_evidence_gaps,
    ) == (
        before_summary,
        str(catl_revision.answerability.id),
        _CATL_ANSWERABILITY_CONTENT_HASH,
        "not_answerable",
        ("missing_key_baseline", "mechanism_unidentified"),
        (
            "industry.capacity_utilization_price_cost_baseline",
            "formal_mechanism_review",
        ),
        True,
        (
            "collect comparable capacity, utilization, price, and cost evidence",
            "complete independent mechanism review before formalization",
        ),
        (),
    )
    assert first_diff.entries == ()

    after_summary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}",
    )
    repeated_summary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}",
    )
    after_boundary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/boundary",
    )
    repeated_boundary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/boundary",
    )
    first_diff_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/diff/{successor.id}",
    )
    second_diff_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/diff/{successor.id}",
    )

    responses = (
        after_summary_response,
        repeated_summary_response,
        after_boundary_response,
        repeated_boundary_response,
        first_diff_response,
        second_diff_response,
    )
    for response in responses:
        assert response.status_code == 200, (
            f"{response.request.url} returned {response.status_code}: {response.text}"
        )

    assert before_summary_response.json() == after_summary_response.json() == repeated_summary_response.json()
    assert before_boundary_response.json() == after_boundary_response.json() == repeated_boundary_response.json()
    assert first_diff_response.json() == second_diff_response.json()
    summary_body = before_summary_response.json()
    boundary_body = before_boundary_response.json()
    assert list(summary_body) == [
        "schema_version", "id", "object_id", "basis_id", "version_kind", "sequence",
        "content_hash", "cutoff", "source_manifest_hash", "parent_refs",
    ]
    assert list(boundary_body) == [
        "schema_version", "revision_id", "object_id", "basis_id", "version_kind",
        "content_hash", "cutoff", "source_manifest_hash", "answerability",
        "unknown_evidence_gaps",
    ]
    assert summary_body["schema_version"] == "underwriting.v1"
    assert summary_body["id"] == str(revision_id)
    assert summary_body["object_id"] == str(catl_revision.company.id)
    assert summary_body["basis_id"] == str(catl_revision.basis.id)
    assert summary_body["version_kind"] == "catl_economic_model_evidence_only"
    assert summary_body["sequence"] == 1
    assert summary_body["content_hash"] == _CATL_EVIDENCE_ONLY_CONTENT_HASH
    assert summary_body["cutoff"] == "2025-05-15T15:59:59Z"
    assert summary_body["source_manifest_hash"] == catl_revision.basis.source_manifest_hash
    assert len(summary_body["parent_refs"]) == len(before_summary.parent_refs)
    assert [
        (parent["reference"], parent["artifact_type"], parent["identity"], parent["content_hash"])
        for parent in summary_body["parent_refs"]
    ] == [
        (ref.reference, ref.artifact_type, ref.identity, ref.content_hash)
        for ref in before_summary.parent_refs
    ]
    assert all(list(parent) == [
        "schema_version", "reference", "artifact_type", "identity", "content_hash",
        "source_locators", "unit", "period_start", "period_end", "available_at", "status",
    ] for parent in summary_body["parent_refs"])
    assert next(
        parent for parent in summary_body["parent_refs"]
        if parent["artifact_type"] == "source_manifest"
    ) == {
        "schema_version": "underwriting.v1",
        "reference": str(catl_revision.source_manifest.id),
        "artifact_type": "source_manifest",
        "identity": "catl-2024-economic-basis|1",
        "content_hash": catl_revision.source_manifest.content_hash,
        "source_locators": sorted(
            source["locator"] for source in catl_revision.source_manifest.manifest["sources"]
        ),
        "unit": None,
        "period_start": None,
        "period_end": None,
        "available_at": None,
        "status": "frozen",
    }
    assert boundary_body == {
        "schema_version": "underwriting.v1",
        "revision_id": str(revision_id),
        "object_id": str(catl_revision.company.id),
        "basis_id": str(catl_revision.basis.id),
        "version_kind": "catl_economic_model_evidence_only",
        "content_hash": _CATL_EVIDENCE_ONLY_CONTENT_HASH,
        "cutoff": "2025-05-15T15:59:59Z",
        "source_manifest_hash": catl_revision.basis.source_manifest_hash,
        "answerability": {
            "schema_version": "underwriting.v1",
            "reference": str(catl_revision.answerability.id),
            "content_hash": before_boundary.answerability.content_hash,
            "state": "not_answerable",
            "blockers": ["missing_key_baseline", "mechanism_unidentified"],
            "research_debt_keys": [
                "industry.capacity_utilization_price_cost_baseline",
                "formal_mechanism_review",
            ],
            "resolvable_within_mandate": True,
            "resolution_requirements": [
                "collect comparable capacity, utilization, price, and cost evidence",
                "complete independent mechanism review before formalization",
            ],
        },
        "unknown_evidence_gaps": [],
    }
    assert list(first_diff_response.json()) == [
        "schema_version", "from_revision_id", "to_revision_id", "from_content_hash",
        "to_content_hash", "entries", "diff_hash",
    ]


def _imports_from_source(source: str) -> set[str]:
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package_parts = _SERVICES_PACKAGE.split(".")
                package_parts = package_parts[:len(package_parts) - node.level + 1]
                resolved_module = ".".join(
                    (*package_parts, *(node.module.split(".") if node.module else ())),
                )
            else:
                resolved_module = node.module or ""
            if resolved_module:
                imported.add(resolved_module)
                imported.update(f"{resolved_module}.{alias.name}" for alias in node.names)
    return imported


def _imports_from(module_path: Path) -> set[str]:
    return _imports_from_source(module_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("source", "forbidden"),
    [
        ("from app.underwriting.services.revision_publisher import Publisher", "revision_publisher"),
        ("from .revision_publisher import Publisher", "revision_publisher"),
        ("from . import workspace_draft", "workspace_draft"),
    ],
)
def test_product_import_guard_canonicalizes_absolute_and_relative_imports(
    source: str,
    forbidden: str,
) -> None:
    assert f"{_SERVICES_PACKAGE}.{forbidden}" in _imports_from_source(source)


def test_legacy_revision_services_do_not_depend_on_future_product_write_services() -> None:
    services_dir = Path(__file__).resolve().parents[2] / "app" / "underwriting" / "services"

    for module_name in _LEGACY_SERVICE_MODULES:
        imports = _imports_from(services_dir / module_name)
        for forbidden in _FORBIDDEN_PRODUCT_IMPORTS:
            assert not any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for imported in imports
            ), f"{module_name} must not import {forbidden}"
