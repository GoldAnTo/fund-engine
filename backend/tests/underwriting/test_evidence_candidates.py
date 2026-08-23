"""Repository governance for append-only evidence candidate dossiers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceItem,
    CandidateEvidenceStatus,
)
from app.underwriting.domain.types import HistoricalBasisInput
from app.underwriting.persistence.repository import StaleParentError, UnderwritingRepository
from app.underwriting.persistence.research_models import UnderwritingSourceManifestVersion
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_freeze import freeze_manifest


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def _manifest_payload(*, locator: str = "https://example.test/catl-2024-ar", available_at: datetime = NOW) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "sources": [{
            "source_id": "catl-2024-ar",
            "title": "CATL annual report",
            "locator": locator,
            "published_at": NOW.isoformat(),
            "first_available_at": available_at.isoformat(),
            "retrieved_at": NOW.isoformat(),
            "content_sha256": "a" * 64,
            "authority": "issuer_filing",
            "authorization": "authorized",
            "display_policy": "derived_only",
            "provider_capability": "public_http",
            "retention": "hash_locator_and_derived_observations",
        }],
    }


@pytest.fixture
def repository(session: Session) -> UnderwritingResearchRepository:
    return UnderwritingResearchRepository(session)


@pytest.fixture
def kernel(session: Session) -> UnderwritingRepository:
    return UnderwritingRepository(session)


@pytest.fixture
def company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)


@pytest.fixture
def other_company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "US:GOOGL:COMPANY", "Alphabet", NOW)


@pytest.fixture
def basis(kernel: UnderwritingRepository):
    manifest = freeze_manifest(_manifest_payload(), NOW)
    return kernel.add_basis(HistoricalBasisInput(NOW, NOW, manifest.manifest_hash), NOW)


@pytest.fixture
def manifest(repository: UnderwritingResearchRepository, basis):
    payload = _manifest_payload()
    frozen = freeze_manifest(payload, NOW)
    return repository.add_source_manifest(
        manifest_key="catl-evidence", basis_id=basis.id, manifest=payload,
        manifest_hash=frozen.manifest_hash, content_hash=canonical_hash(payload),
        expected_parent_id=None, created_at=NOW,
    )


def _dossier_payload(*, object_id: UUID, basis_id: UUID, source_manifest_id: UUID, source_manifest_hash: str, version: int = 1, supersedes_id: UUID | None = None, source_id: str = "catl-2024-ar", source_locator: str = "https://example.test/catl-2024-ar", available_at: datetime = NOW) -> dict[str, object]:
    item = CandidateEvidenceItem(
        metric_key="industry.capacity", status=CandidateEvidenceStatus.SOURCE_REPORTED,
        value=Decimal("100"), unit="GWh", observed_start=NOW - timedelta(days=365),
        observed_end=NOW, available_at=available_at, source_id=source_id,
        source_locator=source_locator, scope_statement="Global battery capacity.",
        exclusions=("No investment conclusion.",), methodology="Direct transcription.",
        prohibited_splicing_declaration="No source splicing.",
    )
    return {
        "object_id": str(object_id), "basis_id": str(basis_id),
        "source_manifest_id": str(source_manifest_id), "dossier_key": "industry-capacity",
        "version": version, "scope_statement": "Candidate industry evidence only.",
        "status": "candidate", "purpose": "evidence_candidate",
        "items": [item.canonical_payload], "rejected_calculations": ["No valuation model."],
        "source_manifest_hash": source_manifest_hash, "created_at": NOW.isoformat(),
        "supersedes_id": None if supersedes_id is None else str(supersedes_id),
    }


def _append_dossier(repository: UnderwritingResearchRepository, company, basis, manifest, *, payload: dict[str, object] | None = None, expected_parent_id: UUID | None = None):
    dossier_payload = payload or _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    )
    return repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="industry-capacity", payload=dossier_payload, created_at=NOW,
        expected_parent_id=expected_parent_id,
    )


def _review_payload(*, dossier_id: UUID, dossier_content_hash: str, reviewer_identity: str, reviewer_role: str, decision: str = "approve") -> dict[str, object]:
    return {
        "dossier_id": str(dossier_id), "dossier_content_hash": dossier_content_hash,
        "reviewer_identity": reviewer_identity, "reviewer_role": reviewer_role,
        "decision": decision, "rationale": "Evidence is bounded and traceable.",
        "reviewed_at": NOW.isoformat(),
    }


def _append_review(repository: UnderwritingResearchRepository, dossier, *, reviewer_identity: str, reviewer_role: str, dossier_content_hash: str | None = None):
    return repository.append_candidate_review(
        dossier_id=dossier.id, dossier_content_hash=dossier_content_hash or dossier.content_hash,
        reviewer_identity=reviewer_identity, reviewer_role=reviewer_role, decision="approve",
        payload=_review_payload(
            dossier_id=dossier.id, dossier_content_hash=dossier_content_hash or dossier.content_hash,
            reviewer_identity=reviewer_identity, reviewer_role=reviewer_role,
        ), created_at=NOW,
    )


def test_same_identity_cannot_fill_both_approval_roles(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")

    with pytest.raises(ValidationError, match="different reviewer identities"):
        _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="methodology")


def test_same_role_cannot_be_filled_twice(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")

    with pytest.raises(ValidationError, match="already has a review for this role"):
        _append_review(repository, dossier, reviewer_identity="reviewer:b", reviewer_role="provenance")


def test_successor_cannot_reuse_prior_dossier_review(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    successor = _append_dossier(
        repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id,
    )

    assert repository.effective_candidate_reviews(successor.id) == []


def test_review_rejects_dossier_that_has_been_superseded(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    successor_payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    _append_dossier(
        repository, company, basis, manifest, payload=successor_payload,
        expected_parent_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="dossier is no longer current"):
        _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")


def test_dossier_rejects_stale_parent(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    _append_dossier(repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id)

    with pytest.raises(StaleParentError, match="expected parent"):
        _append_dossier(repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id)


def test_dossier_successor_rejects_cross_object(repository, company, other_company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    payload = _dossier_payload(
        object_id=other_company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="successor must share object and basis"):
        repository.append_candidate_dossier(
            object_id=other_company.id, basis_id=basis.id, source_manifest_id=manifest.id,
            dossier_key="industry-capacity", payload=payload, created_at=NOW,
            expected_parent_id=dossier.id,
        )


def test_dossier_successor_rejects_cross_basis(repository, kernel, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    later_cutoff = NOW + timedelta(days=1)
    later_payload = _manifest_payload()
    later_frozen = freeze_manifest(later_payload, later_cutoff)
    later_basis = kernel.add_basis(
        HistoricalBasisInput(later_cutoff, later_cutoff, later_frozen.manifest_hash), later_cutoff,
    )
    later_manifest = repository.add_source_manifest(
        manifest_key="catl-evidence", basis_id=later_basis.id, manifest=later_payload,
        manifest_hash=later_frozen.manifest_hash, content_hash=canonical_hash(later_payload),
        expected_parent_id=manifest.id, created_at=later_cutoff,
    )
    payload = _dossier_payload(
        object_id=company.id, basis_id=later_basis.id, source_manifest_id=later_manifest.id,
        source_manifest_hash=later_manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="successor must share object and basis"):
        repository.append_candidate_dossier(
            object_id=company.id, basis_id=later_basis.id, source_manifest_id=later_manifest.id,
            dossier_key="industry-capacity", payload=payload, created_at=NOW,
            expected_parent_id=dossier.id,
        )


def test_dossier_rejects_foreign_manifest_source_locator(repository, company, basis, manifest) -> None:
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, source_locator="https://example.test/catl-2024-ar#p18",
    )

    with pytest.raises(ValidationError, match="source_locator does not match"):
        _append_dossier(repository, company, basis, manifest, payload=payload)


def test_dossier_rejects_future_manifest_source(repository, session, company, basis) -> None:
    future_manifest = UnderwritingSourceManifestVersion(
        manifest_key="future-evidence", version=1, basis_id=basis.id,
        manifest=_manifest_payload(available_at=NOW + timedelta(seconds=1)),
        manifest_hash="b" * 64, content_hash="c" * 64, supersedes_id=None,
        created_at=NOW,
    )
    session.add(future_manifest)
    session.flush()
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=future_manifest.id,
        source_manifest_hash=future_manifest.manifest_hash,
    )

    with pytest.raises(ValidationError, match="source is unavailable at basis cutoff"):
        _append_dossier(repository, company, basis, future_manifest, payload=payload)


def test_dossier_rejects_future_candidate_item(repository, company, basis, manifest) -> None:
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, available_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValidationError, match="item available_at must not exceed"):
        _append_dossier(repository, company, basis, manifest, payload=payload)


def test_review_rejects_hash_from_another_dossier(repository, company, basis, manifest) -> None:
    first = _append_dossier(repository, company, basis, manifest)
    second_payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    ) | {"dossier_key": "other-industry-capacity"}
    second = repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="other-industry-capacity", payload=second_payload, created_at=NOW,
        expected_parent_id=None,
    )

    with pytest.raises(ValidationError, match="dossier_content_hash does not match"):
        _append_review(repository, second, reviewer_identity="reviewer:a", reviewer_role="provenance", dossier_content_hash=first.content_hash)
