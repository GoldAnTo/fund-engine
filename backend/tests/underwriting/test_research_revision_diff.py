"""Read-only summaries of persisted underwriting research revisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.types import HistoricalBasisInput, ResearchObjectKind
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeededRevision:
    company: object
    basis: object
    manifest: object
    observation: object
    first: object


def _append_observation(
    *,
    repository: UnderwritingResearchRepository,
    company: object,
    basis: object,
    source_locator: str = "annual-report:p18",
    metric_key: str = "company.revenue",
):
    manifest_payload = {
        "sources": [{
            "source_id": "annual-report",
            "locator": source_locator,
            "first_available_at": NOW.isoformat(),
        }],
    }
    manifest = repository.add_source_manifest(
        manifest_key=f"manifest-{uuid4().hex}",
        basis_id=basis.id,
        manifest=manifest_payload,
        manifest_hash="a" * 64,
        content_hash=canonical_hash(manifest_payload),
        expected_parent_id=None,
        created_at=NOW,
    )
    definition = repository.append_metric_definition(
        metric_key=metric_key,
        basis_id=basis.id,
        source_manifest_id=manifest.id,
        definition={"metric_key": metric_key},
        unit="CNY",
        period_semantics="flow",
        source_role="reported",
        aggregation="none",
        reconciliation_tolerance=Decimal("0"),
        content_hash="b" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    observation = repository.add_metric_observation(
        metric_key=metric_key,
        definition_version=definition.version,
        basis_id=basis.id,
        definition_id=definition.id,
        source_manifest_id=manifest.id,
        source_id="annual-report",
        value=Decimal("362012554000"),
        unit="CNY",
        observed_start=datetime(2024, 1, 1, tzinfo=UTC),
        observed_end=datetime(2024, 12, 31, tzinfo=UTC),
        effective_at=NOW,
        available_at=NOW,
        source_locator=source_locator,
        dimensions={"scope": "company"},
        dimension_hash=canonical_hash({"scope": "company"}),
        content_hash="c" * 64,
        created_at=NOW,
    )
    return manifest, observation


@pytest.fixture
def seeded_revision(session: Session) -> SeededRevision:
    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.COMPANY, "CN:300750:COMPANY", "CATL")
    basis = kernel.add_basis(HistoricalBasisInput(NOW, None, "a" * 64))
    manifest, observation = _append_observation(
        repository=UnderwritingResearchRepository(session), company=company, basis=basis,
    )
    first = kernel.publish_research_version(
        company.id,
        basis.id,
        "economic_model",
        [str(manifest.id), str(observation.id)],
        None,
    )
    return SeededRevision(company, basis, manifest, observation, first)


def test_foundation_summary_reads_only_the_revision_frozen_parents(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    summary = ResearchRevisionDiffService(session).revision_summary(
        seeded_revision.first.id
    )

    assert (summary.object_id, summary.basis_id, summary.version_kind, summary.sequence) == (
        seeded_revision.company.id,
        seeded_revision.basis.id,
        "economic_model",
        1,
    )
    assert summary.content_hash == seeded_revision.first.content_hash
    assert summary.cutoff == NOW
    assert summary.source_manifest_hash == "a" * 64
    observation = next(ref for ref in summary.parent_refs if ref.reference == str(seeded_revision.observation.id))
    assert observation.artifact_type == "metric_observation"
    assert observation.identity == (
        "company.revenue|1|2024-12-31T00:00:00+00:00|"
        f"{canonical_hash({'scope': 'company'})}|annual-report"
    )
    assert observation.source_locators == ("annual-report:p18",)
    assert observation.unit == "CNY"
    assert observation.period_start == datetime(2024, 1, 1, tzinfo=UTC)
    assert observation.period_end == datetime(2024, 12, 31, tzinfo=UTC)
    assert observation.available_at == NOW
    assert observation.status == "reported"


@pytest.mark.parametrize("parent_kind", ["malformed", "foreign_basis"])
def test_foundation_summary_fails_closed_for_unresolvable_or_cross_basis_parent(
    session: Session, seeded_revision: SeededRevision, parent_kind: str,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    if parent_kind == "malformed":
        parent = "not-a-parent"
    else:
        kernel = UnderwritingKernelService(session, now=lambda: NOW)
        foreign_basis = kernel.add_basis(HistoricalBasisInput(NOW, None, "d" * 64))
        _, foreign_observation = _append_observation(
            repository=UnderwritingResearchRepository(session),
            company=seeded_revision.company,
            basis=foreign_basis,
            source_locator="foreign:p1",
            metric_key="foreign.company.revenue",
        )
        parent = str(foreign_observation.id)
    revision = UnderwritingKernelService(session, now=lambda: NOW).publish_research_version(
        seeded_revision.company.id,
        seeded_revision.basis.id,
        f"economic-model-{parent_kind}",
        [parent],
        None,
    )

    with pytest.raises(ValidationError, match="research revision parent"):
        ResearchRevisionDiffService(session).revision_summary(revision.id)


def test_foundation_history_is_persisted_ordered_and_read_only(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    second = UnderwritingKernelService(session, now=lambda: NOW).publish_research_version(
        seeded_revision.company.id,
        seeded_revision.basis.id,
        "economic_model",
        [str(seeded_revision.manifest.id), str(seeded_revision.observation.id)],
        seeded_revision.first.id,
    )
    service = ResearchRevisionDiffService(session)

    history = service.revision_history(seeded_revision.company.id, "economic_model")

    assert [summary.id for summary in history.revisions] == [seeded_revision.first.id, second.id]
    assert service.effective_revision(seeded_revision.company.id, "economic_model").id == second.id
    assert not any(name.startswith(("add_", "append_", "publish_", "update_", "delete_")) for name in dir(service))
