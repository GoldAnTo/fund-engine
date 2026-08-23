"""Read-only summaries of persisted underwriting research revisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.types import HistoricalBasisInput, ResearchObjectKind
from app.underwriting.domain.metrics import MetricObservation
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingFalsifierVersion,
    UnderwritingForecastInputVersion,
    UnderwritingIndustryScenarioVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
)
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.services.catl_baseline import CatlBaselineService
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


@pytest.fixture
def catl_revision(session: Session):
    return CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())


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
        content_hash=canonical_hash({"metric_key": metric_key}),
        expected_parent_id=None,
        created_at=NOW,
    )
    dimensions = {"scope": "company"}
    observation_hash = MetricObservation(
        metric_key,
        definition.version,
        Decimal("362012554000"),
        "CNY",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 12, 31, tzinfo=UTC),
        NOW,
        NOW,
        "annual-report",
        source_locator,
        tuple(dimensions.items()),
    ).content_hash
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
        dimensions=dimensions,
        dimension_hash=canonical_hash(dimensions),
        content_hash=observation_hash,
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


def test_foundation_read_does_not_flush_pending_writes(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    pending = UnderwritingResearchObject(
        kind="company",
        external_key=seeded_revision.company.external_key,
        canonical_name="duplicate pending object",
        created_at=NOW,
    )
    session.add(pending)

    assert ResearchRevisionDiffService(session).revision_summary(seeded_revision.first.id).id == seeded_revision.first.id
    assert pending in session.new


def test_foundation_rejects_a_parent_owned_by_another_object(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService
    from app.underwriting.domain.types import LedgerEntryInput, LedgerKind

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    other = kernel.add_object(ResearchObjectKind.COMPANY, "US:GOOGL:COMPANY", "Alphabet")
    foreign_entry = kernel.append_ledger_entry(
        other.id,
        seeded_revision.basis.id,
        LedgerEntryInput(LedgerKind.REALITY, "revenue", "reported", {}, NOW, NOW, "public"),
        None,
    )
    revision = kernel.publish_research_version(
        seeded_revision.company.id,
        seeded_revision.basis.id,
        "cross-object-parent",
        [str(foreign_entry.id)],
        None,
    )

    with pytest.raises(ValidationError, match="research revision parent.*object"):
        ResearchRevisionDiffService(session).revision_summary(revision.id)


def test_foundation_rejects_a_database_tampered_parent_set(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps([str(seeded_revision.manifest.id)]), seeded_revision.first.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="research revision content hash"):
        ResearchRevisionDiffService(session).revision_summary(seeded_revision.first.id)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_locator", "tampered:p99"),
        ("dimensions", {"scope": "tampered"}),
    ],
)
def test_foundation_rejects_a_database_tampered_observation_payload(
    session: Session, seeded_revision: SeededRevision, column: str, value: object,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    serialized = json.dumps(value) if column == "dimensions" else value
    session.connection().exec_driver_sql(
        f"UPDATE uw_metric_observations SET {column} = ? WHERE id = ?",
        (serialized, seeded_revision.observation.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="research revision parent.*content hash"):
        ResearchRevisionDiffService(session).revision_summary(seeded_revision.first.id)


@pytest.mark.parametrize("mutation", ["remove", "inject"])
def test_foundation_rejects_a_semantic_snapshot_parent_set_change(
    session: Session, catl_revision, mutation: str,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService
    from app.underwriting.domain.types import LedgerEntryInput, LedgerKind

    parent_ids = list(catl_revision.research_version.parent_ids)
    if mutation == "remove":
        parent_ids.remove(str(catl_revision.metric_observations[0].id))
    else:
        injected = UnderwritingKernelService(session, now=lambda: NOW).append_ledger_entry(
            catl_revision.company.id,
            catl_revision.basis.id,
            LedgerEntryInput(LedgerKind.REALITY, "injected", "reported", {}, NOW, NOW, "public"),
            None,
        )
        parent_ids.append(str(injected.id))
    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps(parent_ids), catl_revision.research_version.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="CATL semantic snapshot parent set"):
        ResearchRevisionDiffService(session).revision_summary(catl_revision.research_version.id)


def test_foundation_rejects_nested_parent_ids_before_set_normalization(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps([["not-a-reference"]]), seeded_revision.first.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="research revision parents are malformed"):
        ResearchRevisionDiffService(session).revision_summary(seeded_revision.first.id)


@pytest.mark.parametrize(
    "artifact_type",
    [
        "industry_state", "industry_scenario", "company_exposure", "earnings_engine",
        "forecast_input", "falsifier", "answerability",
    ],
)
def test_foundation_fails_closed_for_an_unsealable_artifact_type(
    session: Session, seeded_revision: SeededRevision, artifact_type: str,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    state = UnderwritingIndustryStateVersion(
        object_id=seeded_revision.company.id, version=1, basis_id=seeded_revision.basis.id,
        mechanism_id=uuid4(), payload={}, content_hash="a" * 64, supersedes_id=None, created_at=NOW,
    )
    mechanism = UnderwritingMechanismPackVersion(
        mechanism_key=f"unsealable-{artifact_type}", version=1,
        object_id=seeded_revision.company.id, basis_id=seeded_revision.basis.id,
        source_manifest_id=seeded_revision.manifest.id, status="candidate", payload={},
        source_ids=[], definition_ids=[], content_hash="a" * 64, supersedes_id=None, created_at=NOW,
    )
    session.add_all((state, mechanism))
    session.flush()
    rows = {
        "industry_state": state,
        "industry_scenario": UnderwritingIndustryScenarioVersion(
            scenario_key="base", version=1, basis_id=seeded_revision.basis.id,
            industry_state_id=state.id, payload={}, content_hash="a" * 64,
            supersedes_id=None, created_at=NOW,
        ),
        "company_exposure": UnderwritingCompanyExposureVersion(
            company_id=seeded_revision.company.id, industry_state_id=state.id,
            exposure_key="unsealable", version=1, basis_id=seeded_revision.basis.id,
            payload={}, content_hash="a" * 64, supersedes_id=None, created_at=NOW,
        ),
        "earnings_engine": UnderwritingEarningsEngineVersion(
            company_id=seeded_revision.company.id, version=1, basis_id=seeded_revision.basis.id,
            industry_state_id=state.id, payload={}, content_hash="a" * 64,
            supersedes_id=None, created_at=NOW,
        ),
        "forecast_input": UnderwritingForecastInputVersion(
            company_id=seeded_revision.company.id, input_key="unsealable", version=1,
            basis_id=seeded_revision.basis.id, earnings_engine_id=None, payload={},
            content_hash="a" * 64, supersedes_id=None, created_at=NOW,
        ),
        "falsifier": UnderwritingFalsifierVersion(
            mechanism_id=mechanism.id, falsifier_key="unsealable", version=1,
            basis_id=seeded_revision.basis.id, payload={}, content_hash="a" * 64,
            supersedes_id=None, created_at=NOW,
        ),
        "answerability": UnderwritingAnswerabilityEvaluation(
            object_id=seeded_revision.company.id, basis_id=seeded_revision.basis.id,
            version=1, state="not_answerable", blockers=[], research_debt_keys=[],
            resolvable_within_mandate=True, allowed_action="wait_for_validation",
            resolution_requirements=["unsealable"], supersedes_id=None, created_at=NOW,
        ),
    }
    parent = rows[artifact_type]
    session.add(parent)
    session.flush()
    revision = UnderwritingKernelService(session, now=lambda: NOW).publish_research_version(
        seeded_revision.company.id,
        seeded_revision.basis.id,
        f"unsealable-{artifact_type}",
        [str(parent.id)],
        None,
    )

    with pytest.raises(ValidationError, match="research revision parent.*unsealable"):
        ResearchRevisionDiffService(session).revision_summary(revision.id)
