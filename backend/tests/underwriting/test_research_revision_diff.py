"""Read-only summaries of persisted underwriting research revisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.types import (
    BlockerCode, EligibleAction, HistoricalBasisInput, LedgerEntryInput,
    LedgerKind, ResearchObjectKind,
)
from app.underwriting.domain.metrics import MetricObservation
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingLedgerEntry,
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
    UnderwritingMetricDefinitionVersion,
    UnderwritingSourceManifestVersion,
)
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.services.catl_baseline import CatlBaselineService
from app.underwriting.persistence.repository import UnderwritingRepository
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash
from app.underwriting.services.revision_parent_seal import (
    CATL_PARENT_SET_ENTRY_TYPE,
    CATL_PARENT_SET_FAMILY,
    answerability_content_hash,
    catl_parent_set_seal_payload,
    catl_revision_content_hash,
    parent_set_semantic_hash,
)


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


def test_foundation_replays_the_original_catl_revision_after_successor_artifacts_exist(
    session: Session, catl_revision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    service = ResearchRevisionDiffService(session)
    original = service.revision_summary(catl_revision.research_version.id)
    original_manifest = catl_revision.source_manifest
    successor_manifest_payload = dict(original_manifest.manifest)
    successor_manifest = type(original_manifest)(
        manifest_key=original_manifest.manifest_key, version=original_manifest.version + 1,
        basis_id=original_manifest.basis_id, manifest=successor_manifest_payload,
        manifest_hash=original_manifest.manifest_hash,
        content_hash=canonical_hash(successor_manifest_payload),
        supersedes_id=original_manifest.id, created_at=NOW,
    )
    session.add(successor_manifest)
    session.flush()
    observation = catl_revision.metric_observations[0]
    successor_definition = UnderwritingMetricDefinitionVersion(
        metric_key=observation.metric_key, version=observation.definition_version + 1,
        basis_id=catl_revision.basis.id, source_manifest_id=successor_manifest.id,
        definition={"successor": True}, unit=observation.unit, period_semantics="flow",
        source_role="reported", aggregation="none", reconciliation_tolerance=Decimal("0"),
        content_hash=canonical_hash({"successor": True}), supersedes_id=observation.definition_id,
        created_at=NOW,
    )
    original_mechanism = catl_revision.candidate_mechanisms[0]
    successor_mechanism = UnderwritingMechanismPackVersion(
        mechanism_key=original_mechanism.mechanism_key, version=original_mechanism.version + 1,
        object_id=catl_revision.company.id, basis_id=catl_revision.basis.id,
        source_manifest_id=original_manifest.id, status="adapted", payload=dict(original_mechanism.payload),
        source_ids=list(original_mechanism.source_ids), definition_ids=list(original_mechanism.definition_ids),
        content_hash=canonical_hash(dict(original_mechanism.payload)), supersedes_id=original_mechanism.id,
        created_at=NOW,
    )
    session.add_all((successor_definition, successor_mechanism))
    session.flush()

    assert service.revision_summary(catl_revision.research_version.id) == original


def test_foundation_rejects_an_equal_content_parent_with_a_different_identity(
    session: Session, catl_revision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    original_manifest = catl_revision.source_manifest
    substitute = UnderwritingSourceManifestVersion(
        manifest_key="substituted-but-equal-manifest", version=1,
        basis_id=catl_revision.basis.id, manifest=dict(original_manifest.manifest),
        manifest_hash=original_manifest.manifest_hash, content_hash=original_manifest.content_hash,
        supersedes_id=None, created_at=NOW,
    )
    session.add(substitute)
    session.flush()
    seal = next(
        row for parent in catl_revision.research_version.parent_ids
        if (row := session.get(UnderwritingLedgerEntry, UUID(parent))) is not None
        and row.family_key.endswith("parent_set")
    )
    changed_payload = dict(seal.payload)
    changed_refs = [dict(item) for item in changed_payload["parent_refs"]]
    for item in changed_refs:
        if item["reference"] == str(original_manifest.id):
            item["reference"] = str(substitute.id)
            item["identity"] = f"{substitute.manifest_key}|{substitute.version}"
    changed_payload["parent_refs"] = changed_refs
    changed_parent_ids = [
        str(substitute.id) if parent == str(original_manifest.id) else parent
        for parent in catl_revision.research_version.parent_ids
    ]
    rehashed_seal = canonical_hash({
        "ledger_kind": seal.ledger_kind, "family_key": seal.family_key,
        "entry_type": seal.entry_type, "payload": changed_payload,
        "effective_at": NOW, "available_at": NOW,
        "source_boundary": seal.source_boundary,
    })
    session.connection().exec_driver_sql(
        "UPDATE uw_ledger_entries SET payload = ?, content_hash = ? WHERE id = ?",
        (json.dumps(changed_payload), rehashed_seal, seal.id.hex),
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps(changed_parent_ids), catl_revision.research_version.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="CATL semantic snapshot.*stored seal"):
        ResearchRevisionDiffService(session).revision_summary(catl_revision.research_version.id)


def test_diff_is_typed_deterministic_and_uses_only_selected_frozen_parents(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    """A successor changes frozen evidence and adds a mechanism, nothing else."""
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    repository = UnderwritingResearchRepository(session)
    mechanism_payload = {"key": "price_cost_transmission", "status": "candidate"}
    mechanism = repository.append_mechanism(
        mechanism_key="price_cost_transmission", object_id=seeded_revision.company.id,
        basis_id=seeded_revision.basis.id, source_manifest_id=seeded_revision.manifest.id,
        status="candidate", payload=mechanism_payload,
        content_hash=canonical_hash(mechanism_payload), expected_parent_id=None,
        created_at=NOW, source_ids=[], definition_ids=[],
    )
    unrelated_manifest, _ = _append_observation(
        repository=repository, company=seeded_revision.company, basis=seeded_revision.basis,
        source_locator="unreferenced-source:p1", metric_key="unreferenced.metric",
    )
    successor = UnderwritingKernelService(session, now=lambda: NOW).publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.manifest.id), str(mechanism.id)],
        seeded_revision.first.id,
    )

    service = ResearchRevisionDiffService(session)
    first = service.revision_diff(seeded_revision.first.id, successor.id)
    second = service.revision_diff(seeded_revision.first.id, successor.id)

    assert first == second
    assert [(item.group, item.change_type, item.identity) for item in first.entries] == [
        ("evidence", "removed", next(ref.identity for ref in service.revision_summary(seeded_revision.first.id).parent_refs if ref.artifact_type == "metric_observation")),
        ("mechanism", "added", "price_cost_transmission|1"),
    ]
    assert all(str(unrelated_manifest.id) not in {ref.reference for ref in entry.refs()} for entry in first.entries)
    evidence = first.entries[0]
    assert evidence.before is not None and evidence.after is None
    assert evidence.before.source_locators == ("annual-report:p18",)
    assert evidence.before.unit == "CNY"
    assert evidence.before.period_end == datetime(2024, 12, 31, tzinfo=UTC)


def test_diff_represents_a_sealed_answerability_revision_as_a_replacement(
    session: Session, catl_revision,
) -> None:
    """The special CATL seal still permits a strictly historical replacement."""
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    service = ResearchRevisionDiffService(session)
    original = service.revision_summary(catl_revision.research_version.id)
    old_answerability = next(ref for ref in original.parent_refs if ref.artifact_type == "answerability")
    old_seal = next(
        session.get(UnderwritingLedgerEntry, UUID(ref.reference))
        for ref in original.parent_refs
        if ref.artifact_type == "ledger"
        and (row := session.get(UnderwritingLedgerEntry, UUID(ref.reference))) is not None
        and row.family_key == CATL_PARENT_SET_FAMILY
    )
    answerability = kernel.record_answerability(
        catl_revision.company.id, catl_revision.basis.id,
        (BlockerCode.MISSING_KEY_BASELINE, BlockerCode.MECHANISM_UNIDENTIFIED),
        ("industry.capacity_utilization_price_cost_baseline", "formal_mechanism_review"),
        True, EligibleAction.OBSERVE,
        (
            "collect comparable capacity, utilization, price, and cost evidence",
            "complete independent mechanism review before formalization",
        ),
        old_answerability.reference and UUID(old_answerability.reference),
    )
    replacement_ref = {
        "reference": str(answerability.id), "artifact_type": "answerability",
        "identity": "answerability",
        "content_hash": answerability_content_hash(
            object_id=answerability.object_id, basis_id=answerability.basis_id,
            version=answerability.version, state=answerability.state,
            blockers=answerability.blockers, research_debt_keys=answerability.research_debt_keys,
            resolvable_within_mandate=answerability.resolvable_within_mandate,
            allowed_action=answerability.allowed_action,
            resolution_requirements=answerability.resolution_requirements,
        ),
    }
    refs = [
        {"reference": ref.reference, "artifact_type": ref.artifact_type,
         "identity": ref.identity, "content_hash": ref.content_hash}
        for ref in original.parent_refs
        if ref.artifact_type != "semantic_snapshot"
        and ref.reference not in {old_answerability.reference, str(old_seal.id)}
    ] + [replacement_ref]
    token = next(ref.reference for ref in original.parent_refs if ref.artifact_type == "semantic_snapshot")
    seal = kernel.append_ledger_entry(
        catl_revision.company.id, catl_revision.basis.id,
        LedgerEntryInput(
            LedgerKind.CALIBRATION, CATL_PARENT_SET_FAMILY, CATL_PARENT_SET_ENTRY_TYPE,
            catl_parent_set_seal_payload(semantic_snapshot_token=token, refs=refs),
            NOW, NOW, "frozen_revision_parent_set",
        ),
        old_seal.id,
    )
    parent_ids = [
        parent for parent in catl_revision.research_version.parent_ids
        if parent not in {old_answerability.reference, str(old_seal.id)}
    ] + [str(answerability.id), str(seal.id)]
    successor = UnderwritingRepository(session).append_research_version(
        object_id=catl_revision.company.id, basis_id=catl_revision.basis.id,
        version_kind=catl_revision.research_version.version_kind,
        content_hash=catl_revision_content_hash(
            semantic_snapshot_token=token, parent_set_semantic_hash=parent_set_semantic_hash(refs),
        ),
        parent_ids=parent_ids, expected_parent_id=catl_revision.research_version.id,
        created_at=NOW,
    )

    result = service.revision_diff(catl_revision.research_version.id, successor.id)

    answerability_change = next(item for item in result.entries if item.group == "answerability")
    assert answerability_change.change_type == "replaced"
    assert answerability_change.identity == "answerability"
    assert answerability_change.before == old_answerability
    assert answerability_change.after is not None
    assert answerability_change.after.reference == str(answerability.id)


@pytest.mark.parametrize("pair", ["reverse", "sibling", "cross_family"])
def test_diff_rejects_non_ancestor_pairs(
    session: Session, seeded_revision: SeededRevision, pair: str,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    second = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.manifest.id), str(seeded_revision.observation.id)], seeded_revision.first.id,
    )
    if pair == "reverse":
        left, right = second, seeded_revision.first
    elif pair == "sibling":
        sibling = kernel.publish_research_version(
            seeded_revision.company.id, seeded_revision.basis.id, "other_economic_model",
            [str(seeded_revision.manifest.id)], None,
        )
        left, right = seeded_revision.first, sibling
    else:
        other = kernel.publish_research_version(
            seeded_revision.company.id, seeded_revision.basis.id, "another_model",
            [str(seeded_revision.manifest.id)], None,
        )
        left, right = seeded_revision.first, other

    with pytest.raises(ValidationError, match="ancestor"):
        ResearchRevisionDiffService(session).revision_diff(left.id, right.id)


def test_diff_replays_direct_and_multi_hop_ancestors_when_parent_order_changes(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    second = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.observation.id), str(seeded_revision.manifest.id)], seeded_revision.first.id,
    )
    third = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.manifest.id), str(seeded_revision.observation.id)], second.id,
    )
    service = ResearchRevisionDiffService(session)
    direct = service.revision_diff(seeded_revision.first.id, second.id)
    multi_hop = service.revision_diff(seeded_revision.first.id, third.id)
    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps(list(reversed(second.parent_ids))), second.id.hex),
    )
    session.expire_all()

    reordered = ResearchRevisionDiffService(session).revision_diff(seeded_revision.first.id, second.id)

    assert direct.entries == multi_hop.entries == reordered.entries == ()
    assert direct.diff_hash == reordered.diff_hash


def test_diff_fails_closed_for_ungoverned_semantic_token_and_corrupt_cycle(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    token_revision = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model_with_token",
        ["semantic_snapshot:" + "a" * 64], None,
    )
    with pytest.raises(ValidationError, match="semantic snapshot"):
        ResearchRevisionDiffService(session).revision_summary(token_revision.id)

    successor = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.manifest.id), str(seeded_revision.observation.id)], seeded_revision.first.id,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET supersedes_id = ? WHERE id = ?",
        (successor.id.hex, seeded_revision.first.id.hex),
    )
    session.expire_all()
    with pytest.raises(ValidationError, match="history is corrupt|ancestor chain is cyclic"):
        ResearchRevisionDiffService(session).revision_diff(seeded_revision.first.id, successor.id)


def test_diff_does_not_recompute_an_old_revision_from_current_effective_ledger(
    session: Session, seeded_revision: SeededRevision,
) -> None:
    """An unreferenced later ledger fact must not invalidate frozen v1/v2."""
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    successor = kernel.publish_research_version(
        seeded_revision.company.id, seeded_revision.basis.id, "economic_model",
        [str(seeded_revision.manifest.id), str(seeded_revision.observation.id)],
        seeded_revision.first.id,
    )
    service = ResearchRevisionDiffService(session)
    before = service.revision_diff(seeded_revision.first.id, successor.id)
    kernel.append_ledger_entry(
        seeded_revision.company.id, seeded_revision.basis.id,
        LedgerEntryInput(
            LedgerKind.REALITY, "unreferenced_later_fact", "reported",
            {"source_locator": "later:p1"}, NOW, NOW, "public",
        ),
        None,
    )
    session.expire_all()

    after = ResearchRevisionDiffService(session).revision_diff(seeded_revision.first.id, successor.id)

    assert after == before


def test_legacy_generic_revision_replays_from_its_frozen_ledger_parents(
    session: Session,
) -> None:
    """Pre-parent-set revisions use only their selected ledger snapshot."""
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.COMPANY, "CN:legacy:COMPANY", "Legacy")
    basis = kernel.add_basis(HistoricalBasisInput(NOW, None, "b" * 64))
    selected = kernel.append_ledger_entry(
        company.id, basis.id,
        LedgerEntryInput(LedgerKind.REALITY, "reported_revenue", "reported", {}, NOW, NOW, "public"),
        None,
    )
    late_available_at = NOW + timedelta(days=1)
    late_payload: dict[str, object] = {"excluded_at_legacy_cutoff": True}
    later_candidate = UnderwritingLedgerEntry(
        object_id=company.id, basis_id=basis.id, ledger_kind="reality",
        family_key="later_unavailable_candidate", entry_type="reported", version=1,
        payload=late_payload, effective_at=NOW, available_at=late_available_at,
        source_boundary="public",
        content_hash=canonical_hash({
            "ledger_kind": "reality", "family_key": "later_unavailable_candidate",
            "entry_type": "reported", "payload": late_payload,
            "effective_at": NOW, "available_at": late_available_at,
            "source_boundary": "public",
        }),
        supersedes_id=None, created_at=NOW,
    )
    session.add(later_candidate)
    session.flush()
    legacy_parent_ids = [str(selected.id), str(later_candidate.id)]
    legacy_snapshot_hash = canonical_hash({
        "object_id": company.id, "basis_cutoff": NOW, "source_manifest_hash": "b" * 64,
        "entries": [{"id": selected.id, "content_hash": selected.content_hash}],
    })
    legacy = UnderwritingRepository(session).append_research_version(
        object_id=company.id, basis_id=basis.id, version_kind="legacy_generic",
        content_hash=canonical_hash({
            "snapshot_hash": legacy_snapshot_hash,
            "version_kind": "legacy_generic",
            "parent_ids": sorted(legacy_parent_ids),
        }),
        parent_ids=legacy_parent_ids, expected_parent_id=None, created_at=NOW,
    )
    successor = kernel.publish_research_version(
        company.id, basis.id, "legacy_generic", legacy_parent_ids, legacy.id,
    )
    kernel.append_ledger_entry(
        company.id, basis.id,
        LedgerEntryInput(LedgerKind.REALITY, "later_unreferenced", "reported", {}, NOW, NOW, "public"),
        None,
    )
    session.expire_all()
    service = ResearchRevisionDiffService(session)

    assert service.revision_summary(legacy.id).id == legacy.id
    assert service.revision_diff(legacy.id, successor.id).entries == ()

    session.connection().exec_driver_sql(
        "UPDATE uw_research_versions SET parent_ids = ? WHERE id = ?",
        (json.dumps([]), legacy.id.hex),
    )
    session.expire_all()
    with pytest.raises(ValidationError, match="research revision content hash"):
        ResearchRevisionDiffService(session).revision_summary(legacy.id)


def test_legacy_generic_revision_rejects_a_tampered_frozen_ledger_payload(
    session: Session,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.COMPANY, "CN:legacy-tamper:COMPANY", "Legacy Tamper")
    basis = kernel.add_basis(HistoricalBasisInput(NOW, None, "c" * 64))
    selected = kernel.append_ledger_entry(
        company.id, basis.id,
        LedgerEntryInput(LedgerKind.REALITY, "reported_revenue", "reported", {}, NOW, NOW, "public"),
        None,
    )
    parent_ids = [str(selected.id)]
    snapshot_hash = canonical_hash({
        "object_id": company.id, "basis_cutoff": NOW, "source_manifest_hash": "c" * 64,
        "entries": [{"id": selected.id, "content_hash": selected.content_hash}],
    })
    legacy = UnderwritingRepository(session).append_research_version(
        object_id=company.id, basis_id=basis.id, version_kind="legacy_tamper",
        content_hash=canonical_hash({
            "snapshot_hash": snapshot_hash, "version_kind": "legacy_tamper", "parent_ids": parent_ids,
        }),
        parent_ids=parent_ids, expected_parent_id=None, created_at=NOW,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_ledger_entries SET payload = ? WHERE id = ?",
        (json.dumps({"tampered": True}), selected.id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="ledger content hash"):
        ResearchRevisionDiffService(session).revision_summary(legacy.id)


def test_diff_follows_direct_and_multi_hop_successors_across_historical_bases(
    session: Session,
) -> None:
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.COMPANY, "CN:cross-basis:COMPANY", "Cross Basis")
    bases = tuple(
        kernel.add_basis(HistoricalBasisInput(NOW + timedelta(days=index), None, chr(100 + index) * 64))
        for index in range(3)
    )
    revisions: list[UnderwritingResearchVersion] = []
    revision_parent_id = None
    ledger_parent_id = None
    for index, basis in enumerate(bases, start=1):
        entry = kernel.append_ledger_entry(
            company.id, basis.id,
            LedgerEntryInput(LedgerKind.REALITY, "reported_revenue", "reported", {"basis": index}, NOW, NOW, "public"),
            ledger_parent_id,
        )
        revisions.append(kernel.publish_research_version(
            company.id, basis.id, "cross_basis_model", [str(entry.id)], revision_parent_id,
        ))
        revision_parent_id = revisions[-1].id
        ledger_parent_id = entry.id
    session.expire_all()
    service = ResearchRevisionDiffService(session)

    direct = service.revision_diff(revisions[0].id, revisions[1].id)
    multi_hop = service.revision_diff(revisions[0].id, revisions[2].id)

    assert service.revision_summary(revisions[0].id).basis_id == bases[0].id
    assert service.revision_summary(revisions[2].id).basis_id == bases[2].id
    assert direct.entries and multi_hop.entries
    assert {item.group for item in direct.entries} == {"evidence"}
