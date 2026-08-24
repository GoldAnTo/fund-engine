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
_LEGACY_SERVICE_MODULES = (
    "kernel.py",
    "research_revision_diff.py",
    "revision_parent_seal.py",
)
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

    first_summary = reader.revision_summary(revision_id)
    second_summary = reader.revision_summary(revision_id)
    first_boundary = reader.revision_boundary(revision_id)
    second_boundary = reader.revision_boundary(revision_id)

    successor = UnderwritingRepository(session).append_research_version(
        object_id=catl_revision.company.id,
        basis_id=catl_revision.basis.id,
        version_kind=catl_revision.research_version.version_kind,
        content_hash=catl_revision.research_version.content_hash,
        parent_ids=list(catl_revision.research_version.parent_ids),
        expected_parent_id=revision_id,
        created_at=NOW,
    )
    first_diff = reader.revision_diff(revision_id, successor.id)
    second_diff = reader.revision_diff(revision_id, successor.id)

    assert first_summary == second_summary
    assert first_boundary == second_boundary
    assert first_diff == second_diff
    assert first_summary.content_hash == _CATL_EVIDENCE_ONLY_CONTENT_HASH
    assert first_summary.content_hash == catl_revision.research_version.content_hash
    assert first_summary.parent_refs == tuple(sorted(
        first_summary.parent_refs,
        key=lambda ref: (ref.artifact_type, ref.identity, ref.reference),
    ))
    assert first_diff.entries == ()

    first_summary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}",
    )
    second_summary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}",
    )
    first_boundary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/boundary",
    )
    second_boundary_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/boundary",
    )
    first_diff_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/diff/{successor.id}",
    )
    second_diff_response = api_client.get(
        f"/api/underwriting/v1/research-versions/{revision_id}/diff/{successor.id}",
    )

    responses = (
        first_summary_response,
        second_summary_response,
        first_boundary_response,
        second_boundary_response,
        first_diff_response,
        second_diff_response,
    )
    assert all(response.status_code == 200 for response in responses)
    assert first_summary_response.json() == second_summary_response.json()
    assert first_boundary_response.json() == second_boundary_response.json()
    assert first_diff_response.json() == second_diff_response.json()
    assert first_summary_response.json()["content_hash"] == _CATL_EVIDENCE_ONLY_CONTENT_HASH


def _imports_from(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def test_legacy_revision_services_do_not_depend_on_future_product_write_services() -> None:
    services_dir = Path(__file__).resolve().parents[2] / "app" / "underwriting" / "services"

    for module_name in _LEGACY_SERVICE_MODULES:
        imports = _imports_from(services_dir / module_name)
        for forbidden in _FORBIDDEN_PRODUCT_IMPORTS:
            assert not any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for imported in imports
            ), f"{module_name} must not import {forbidden}"
