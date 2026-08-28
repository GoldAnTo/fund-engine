from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sys
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from app.models.ledger import ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchPersistedBundle,
    CompanyResearchRepository,
)
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import (
    UnderwritingMarketCaptureEnvelope,
    UnderwritingPriceSnapshot,
    UnderwritingResearchProject,
)

from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelBuilder,
    FrozenMarketSnapshotRole,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.market_snapshots import (
    market_capture_envelope_hash,
    price_snapshot_hash,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
from tests.underwriting.test_company_research_model_builder import _build_input


NOW = datetime(2026, 8, 26, tzinfo=UTC)
MARKET_CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _expected_model_source_refs(evidence_refs, gap_refs, bindings):
    values = [
        *(dict(value) for value in evidence_refs),
        *(dict(value) for value in gap_refs),
    ]
    values.extend(
        {
            "raw_hash": binding.source_ref.raw_hash,
            "source_locator": binding.source_ref.source_locator,
            "source_role": binding.source_ref.source_role,
            "source_url": binding.source_ref.source_url,
        }
        for binding in bindings
    )
    unique = {
        (
            value["source_role"],
            value["source_url"],
            value["source_locator"],
            value["raw_hash"],
        ): value
        for value in values
    }
    return tuple(dict(unique[key]) for key in sorted(unique))


def _prepared(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    initializer = CompanyResearchInitializer(session, now=lambda: NOW)
    preview = initializer.preview(
        company_id=loaded.objects["US:ALPHABET:COMPANY"].id, cutoff_at=MARKET_CUTOFF
    )
    initialized = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=preview.company.object_id,
        cutoff_at=MARKET_CUTOFF,
        idempotency_key="company-workbench-alphabet",
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    return initialized


def _model_workspace(
    session, *, binding_mutation=None, bindings_mutation=None, before_commit=None
):
    initialized = _prepared(session)
    workbench = CompanyResearchWorkbench(session, now=lambda: NOW)
    workspace = workbench.workspace(project_id=initialized.project.id)
    evidence = next(
        item.artifact for item in workspace.modules if item.key == "evidence_and_gaps"
    )
    assert evidence is not None
    current = evidence
    for fact in evidence.payload["facts"]:
        current = workbench.review_evidence(
            project_id=initialized.project.id,
            evidence_artifact_id=current.id,
            fact_key=fact["fact_key"],
            decision="confirmed",
            expected_head_id=current.id,
        ).evidence_artifact
    repository = CompanyResearchRepository(session)
    preparation = repository.preparation_for_project(initialized.project.id)
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None
    preparation.current_step = "model_bundle"
    job.status = "running"
    job.step = "model_bundle"
    job.claim_token = "model-claim"
    prior_gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert prior_gaps is not None
    governed = CompanyResearchInitializer(session, now=lambda: NOW).governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=MARKET_CUTOFF,
    )
    assert governed.market_context is not None
    bindings = governed.market_context.snapshot_bindings
    if binding_mutation is not None:
        bindings = (binding_mutation(bindings[0]), *bindings[1:])
    if bindings_mutation is not None:
        bindings = bindings_mutation(bindings, initialized)
    source_refs = _expected_model_source_refs(
        current.source_refs,
        prior_gaps.source_refs,
        bindings,
    )
    result = CompanyResearchModelBuilder().build(_build_input())
    payloads = {
        kind: CompanyResearchArtifactCodec.encode(kind, artifact)
        for kind, artifact in {
            "business_map": result.business_map,
            "driver_map": result.driver_map,
            "financial_bridge": result.financial_bridge,
            "scenario_set": result.scenario_set,
            "valuation_set": result.valuation_set,
            "judgment_context": result.judgment_context,
            "research_gaps": result.gaps,
            "memo": result.memo,
        }.items()
    }
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(initialized.project.id)
    assert draft is not None
    bundle = CompanyResearchPersistedBundle(
        evidence_artifact_id=current.id,
        evidence_content_hash=current.content_hash,
        research_gaps_artifact_id=prior_gaps.id,
        research_gaps_content_hash=prior_gaps.content_hash,
        workspace_draft_id=draft.id,
        workspace_draft_lock_version=draft.lock_version,
        business_map=payloads["business_map"],
        driver_map=payloads["driver_map"],
        financial_bridge=payloads["financial_bridge"],
        scenario_set=payloads["scenario_set"],
        valuation_set=payloads["valuation_set"],
        judgment_context=payloads["judgment_context"],
        research_gaps=payloads["research_gaps"],
        memo=payloads["memo"],
        source_refs=source_refs,
        market_snapshot_bindings=bindings,
    )
    if before_commit is not None:
        replacement = before_commit(initialized, bundle)
        if replacement is not None:
            bundle = replacement
    repository.complete_model_bundle(
        preparation.id,
        bundle=bundle,
        expected_claim_token="model-claim",
        expected_request_hash=preparation.request_hash,
        expected_strategy_version=preparation.strategy_version,
        created_at=NOW,
    )
    return initialized, workbench, repository


def _rewrite_payload(
    session, row, payload: dict, *, input_hash: str | None = None
) -> None:
    rewritten_input_hash = input_hash or row.input_hash
    content_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=row.project_id,
        kind=row.kind,
        version=row.version,
        supersedes_id=row.supersedes_id,
        parent_content_hash=row.parent_content_hash,
        input_hash=rewritten_input_hash,
        payload=payload,
        source_refs=row.source_refs,
    )
    set_committed_value(row, "payload", payload)
    set_committed_value(row, "input_hash", rewritten_input_hash)
    set_committed_value(row, "content_hash", content_hash)


def _rewrite_source_refs(row, source_refs) -> None:
    copied = [dict(value) for value in source_refs]
    content_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=row.project_id,
        kind=row.kind,
        version=row.version,
        supersedes_id=row.supersedes_id,
        parent_content_hash=row.parent_content_hash,
        input_hash=row.input_hash,
        payload=row.payload,
        source_refs=copied,
    )
    set_committed_value(row, "source_refs", copied)
    set_committed_value(row, "content_hash", content_hash)


def test_workspace_is_a_closed_snapshot_of_the_review_gate(session) -> None:
    initialized = _prepared(session)

    workspace = CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
        project_id=initialized.project.id
    )

    assert workspace.project_id == initialized.project.id
    assert workspace.company.external_key == "US:ALPHABET:COMPANY"
    assert workspace.preparation.status == "awaiting_evidence_review"
    assert tuple(item.key for item in workspace.modules) == (
        "overview",
        "business_map",
        "operating_drivers",
        "evidence_and_gaps",
        "industry_competition_regulation",
        "financials_cash_flow_capital_allocation",
        "scenarios_valuation_implied_expectations",
        "counterevidence_risks_next_checks",
        "versions_changes_memo",
    )
    evidence = next(
        item for item in workspace.modules if item.key == "evidence_and_gaps"
    )
    # A generated source artifact is visible, but it cannot be presented as a
    # usable research conclusion until every fact has a human decision.
    assert evidence.state == "needs_review"
    assert evidence.artifact is not None and evidence.artifact.kind == "evidence_index"
    # Evidence and gaps reuse the same three frozen source references; the
    # summary counts canonical source records, never per-artifact copies.
    assert workspace.source_count == 3
    assert workspace.gap_count >= 0
    assert workspace.draft.lock_version == 1
    assert workspace.selected_revision is None


def test_alphabet_model_workspace_counts_each_governed_source_once(session) -> None:
    initialized, workbench, repository = _model_workspace(session)
    workspace = workbench.workspace(project_id=initialized.project.id)
    business_map = repository.current_artifact(initialized.project.id, "business_map")
    assert business_map is not None

    assert workspace.source_count == len(business_map.source_refs)
    assert workspace.source_count == 9


def test_workbench_fails_closed_when_artifact_history_exceeds_its_bound(
    session, monkeypatch
) -> None:
    initialized = _prepared(session)
    workbench_module = sys.modules[CompanyResearchWorkbench.__module__]
    monkeypatch.setattr(workbench_module, "_MAX_ARTIFACT_HISTORY", 1)

    with pytest.raises(ValidationError, match="artifact history limit exceeded"):
        CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
            project_id=initialized.project.id
        )


def test_evidence_review_appends_exact_successor_and_is_idempotent(session) -> None:
    initialized = _prepared(session)
    workbench = CompanyResearchWorkbench(session, now=lambda: NOW)
    workspace = workbench.workspace(project_id=initialized.project.id)
    artifact = next(
        item.artifact for item in workspace.modules if item.key == "evidence_and_gaps"
    )
    assert artifact is not None
    fact_key = artifact.payload["facts"][0]["fact_key"]

    first = workbench.review_evidence(
        project_id=initialized.project.id,
        evidence_artifact_id=artifact.id,
        fact_key=fact_key,
        decision="confirmed",
        expected_head_id=artifact.id,
    )
    second = workbench.review_evidence(
        project_id=initialized.project.id,
        evidence_artifact_id=artifact.id,
        fact_key=fact_key,
        decision="confirmed",
        expected_head_id=artifact.id,
    )

    assert first.evidence_artifact.id == second.evidence_artifact.id
    assert first.evidence_artifact.version == 2
    assert first.evidence_artifact.payload["facts"][0]["review_decision"] == "confirmed"


def test_last_evidence_review_enqueues_model_bundle_for_the_worker(session) -> None:
    initialized = _prepared(session)
    workbench = CompanyResearchWorkbench(session, now=lambda: NOW)
    artifact = next(
        item.artifact
        for item in workbench.workspace(project_id=initialized.project.id).modules
        if item.key == "evidence_and_gaps"
    )
    assert artifact is not None

    current = artifact
    for fact in artifact.payload["facts"]:
        result = workbench.review_evidence(
            project_id=initialized.project.id,
            evidence_artifact_id=current.id,
            fact_key=fact["fact_key"],
            decision="confirmed",
            expected_head_id=current.id,
        )
        current = result.evidence_artifact

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "awaiting_judgment_review"
    workspace = workbench.workspace(project_id=initialized.project.id)
    assert workspace.preparation.current_step == "judgment_context"
    assert (
        next(item for item in workspace.modules if item.key == "business_map").state
        == "ready"
    )


def test_workbench_accepts_one_closed_model_bundle(session) -> None:
    initialized, workbench, repository = _model_workspace(session)

    workspace = workbench.workspace(project_id=initialized.project.id)

    assert workspace.preparation.status == "awaiting_judgment_review"
    assert all(module.state == "ready" for module in workspace.modules)
    heads = {
        kind: repository.current_artifact(initialized.project.id, kind)
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "research_gaps",
            "memo",
        )
    }
    assert all(row is not None for row in heads.values())
    market_ids = heads["valuation_set"].payload["_lineage"]["market_snapshot_ids"]
    assert market_ids
    preparation = repository.preparation_for_project(initialized.project.id)
    assert preparation is not None
    for row in heads.values():
        assert row.payload["_lineage"]["market_snapshot_ids"] == market_ids
        assert row.input_hash == canonical_hash(
            {
                "request_hash": preparation.request_hash,
                "artifact_refs": row.payload["_lineage"]["artifact_refs"],
                "market_snapshot_ids": market_ids,
                "market_snapshot_bindings": row.payload["_lineage"][
                    "market_snapshot_bindings"
                ],
            }
        )


def test_optional_valuation_tracks_the_current_model_epoch_across_rebuilds(
    session,
) -> None:
    initialized, workbench, repository = _model_workspace(session)
    project_id = initialized.project.id
    first_valuation = repository.current_artifact(project_id, "valuation_set")
    assert first_valuation is not None
    lineage = first_valuation.payload["_lineage"]
    bindings = tuple(
        CompanyResearchRepository.market_binding_from_payload(value)
        for value in lineage["market_snapshot_bindings"]
    )
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    original_draft = drafts.read(project_id)
    assert original_draft is not None
    original_refs = original_draft.content

    def publish(*, with_valuation: bool, epoch_bindings=()) -> None:
        preparation = repository.preparation_for_project(project_id)
        evidence = repository.current_artifact(project_id, "evidence_index")
        gaps = repository.current_artifact(project_id, "research_gaps")
        assert preparation is not None and preparation.job_id is not None
        assert evidence is not None and gaps is not None
        job = session.get(Job, preparation.job_id)
        assert job is not None
        preparation.status = "building_model"
        preparation.current_step = "model_bundle"
        preparation.progress = 25
        job.status = "running"
        job.step = "model_bundle"
        job.progress = 25
        job.claim_token = "model-claim"
        session.flush([preparation, job])
        build_input = _build_input()
        if not with_valuation:
            build_input = replace(build_input, market_context=None)
        result = CompanyResearchModelBuilder().build(build_input)
        values = {
            "business_map": result.business_map,
            "driver_map": result.driver_map,
            "financial_bridge": result.financial_bridge,
            "scenario_set": result.scenario_set,
            "judgment_context": result.judgment_context,
            "research_gaps": result.gaps,
            "memo": result.memo,
        }
        if result.valuation_set is not None:
            values["valuation_set"] = result.valuation_set
        payloads = {
            kind: CompanyResearchArtifactCodec.encode(kind, artifact)
            for kind, artifact in values.items()
        }
        draft = drafts.read(project_id)
        assert draft is not None
        repository.complete_model_bundle(
            preparation.id,
            bundle=CompanyResearchPersistedBundle(
                evidence_artifact_id=evidence.id,
                evidence_content_hash=evidence.content_hash,
                research_gaps_artifact_id=gaps.id,
                research_gaps_content_hash=gaps.content_hash,
                workspace_draft_id=draft.id,
                workspace_draft_lock_version=draft.lock_version,
                business_map=payloads["business_map"],
                driver_map=payloads["driver_map"],
                financial_bridge=payloads["financial_bridge"],
                scenario_set=payloads["scenario_set"],
                valuation_set=payloads.get("valuation_set"),
                judgment_context=payloads["judgment_context"],
                research_gaps=payloads["research_gaps"],
                memo=payloads["memo"],
                source_refs=_expected_model_source_refs(
                    evidence.source_refs,
                    gaps.source_refs,
                    epoch_bindings,
                ),
                market_snapshot_bindings=tuple(epoch_bindings),
            ),
            expected_claim_token="model-claim",
            expected_request_hash=preparation.request_hash,
            expected_strategy_version=preparation.strategy_version,
            created_at=NOW,
        )

    drafts.save(
        project_id,
        expected_lock_version=original_draft.lock_version,
        patch=WorkspaceDraftPatch(
            price_snapshot_ids=(),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=None,
            security_rights_ids=(),
        ),
    )
    publish(with_valuation=False)
    no_valuation_preparation = repository.preparation_for_project(project_id)
    assert no_valuation_preparation is not None
    no_valuation_preparation.status = "completed"
    session.flush([no_valuation_preparation])

    without_valuation = workbench.workspace(project_id=project_id)
    valuation_module = next(
        item
        for item in without_valuation.modules
        if item.key == "scenarios_valuation_implied_expectations"
    )
    assert valuation_module.state == "ready"
    assert tuple(item.kind for item in valuation_module.artifacts) == ("scenario_set",)
    assert valuation_module.valuation_state == "blocked"
    assert "valuation_set" not in without_valuation.change_summary["artifact_versions"]
    historical_valuation = repository.current_artifact(project_id, "valuation_set")
    assert historical_valuation is not None
    assert historical_valuation.id == first_valuation.id

    cleared = drafts.read(project_id)
    assert cleared is not None
    drafts.save(
        project_id,
        expected_lock_version=cleared.lock_version,
        patch=WorkspaceDraftPatch(
            price_snapshot_ids=original_refs.price_snapshot_ids,
            fx_snapshot_ids=original_refs.fx_snapshot_ids,
            capital_structure_snapshot_id=original_refs.capital_structure_snapshot_id,
            security_rights_ids=original_refs.security_rights_ids,
        ),
    )
    publish(with_valuation=True, epoch_bindings=bindings)

    restored = workbench.workspace(project_id=project_id)
    restored_module = next(
        item
        for item in restored.modules
        if item.key == "scenarios_valuation_implied_expectations"
    )
    assert restored_module.state == "ready"
    assert tuple(item.kind for item in restored_module.artifacts) == (
        "scenario_set",
        "valuation_set",
    )
    assert restored_module.valuation_state == "ready"
    assert restored_module.artifacts[-1].version == first_valuation.version + 1


def test_model_bundle_rolls_back_when_valuation_append_fails(
    session, monkeypatch
) -> None:
    original = CompanyResearchRepository.append_artifact

    def fail_valuation(self, *args, kind, **kwargs):
        if kind == "valuation_set":
            raise RuntimeError("injected valuation failure")
        return original(self, *args, kind=kind, **kwargs)

    monkeypatch.setattr(CompanyResearchRepository, "append_artifact", fail_valuation)
    with pytest.raises(RuntimeError, match="injected valuation failure"):
        _model_workspace(session)

    model_rows = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.kind.in_(
                    (
                        "business_map",
                        "driver_map",
                        "financial_bridge",
                        "scenario_set",
                        "valuation_set",
                        "judgment_context",
                        "memo",
                    )
                )
            )
        )
    )
    assert model_rows == ()


@pytest.mark.parametrize("tamper", ("snapshot", "capture"))
def test_workbench_rejects_market_binding_when_immutable_source_changed(
    session, tamper: str
) -> None:
    initialized, workbench, _repository = _model_workspace(session)
    if tamper == "snapshot":
        row = session.scalar(select(UnderwritingPriceSnapshot).limit(1))
    else:
        row = session.scalar(
            select(UnderwritingMarketCaptureEnvelope)
            .where(UnderwritingMarketCaptureEnvelope.snapshot_kind == "price")
            .limit(1)
        )
    assert row is not None
    set_committed_value(row, "content_hash", "f" * 64)

    with pytest.raises(ValidationError, match="market snapshot binding"):
        workbench.workspace(project_id=initialized.project.id)


def test_market_binding_validation_requires_the_exact_governed_role_set(
    session,
) -> None:
    initialized = _prepared(session)
    governed = CompanyResearchInitializer(session, now=lambda: NOW).governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=MARKET_CUTOFF,
    )
    assert governed.market_context is not None

    with pytest.raises(ValidationError, match="market snapshot binding"):
        CompanyResearchRepository(session).validate_market_snapshot_bindings(
            project_id=initialized.project.id,
            bindings=governed.market_context.snapshot_bindings[:1],
        )


@pytest.mark.parametrize("field", ("source_role", "fact_key", "source_url"))
def test_model_bundle_rejects_non_governed_source_identity_before_writing(
    session, field: str
) -> None:
    def mutate(binding):
        value = {
            "source_role": "third_party_snapshot",
            "fact_key": "wrong_market_fact",
            "source_url": "https://attacker.invalid/source",
        }[field]
        return replace(
            binding,
            source_ref=replace(binding.source_ref, **{field: value}),
        )

    with pytest.raises(ValidationError, match="market snapshot binding"):
        _model_workspace(session, binding_mutation=mutate)

    assert (
        tuple(
            session.scalars(
                select(CompanyResearchArtifactVersion).where(
                    CompanyResearchArtifactVersion.kind == "business_map"
                )
            )
        )
        == ()
    )


@pytest.mark.parametrize("replacement", ((), (uuid.UUID(int=999),)))
def test_model_bundle_rejects_cleared_or_replaced_draft_market_refs_without_writing(
    session, replacement: tuple[uuid.UUID, ...]
) -> None:
    def change_draft(initialized, _bundle) -> None:
        drafts = WorkspaceDraftService(session, now=lambda: NOW)
        draft = drafts.read(initialized.project.id)
        assert draft is not None
        drafts.save(
            initialized.project.id,
            expected_lock_version=draft.lock_version,
            patch=WorkspaceDraftPatch(price_snapshot_ids=replacement),
        )

    with pytest.raises(ValidationError, match="workspace draft is stale"):
        _model_workspace(session, before_commit=change_draft)

    assert (
        session.scalar(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.kind == "business_map"
            )
        )
        is None
    )


def test_model_bundle_rejects_a_newer_draft_mandate_as_a_cutoff_substitute(
    session,
) -> None:
    later_cutoff = datetime(2026, 8, 28, tzinfo=UTC)

    def substitute_mandate(initialized, bundle):
        drafts = WorkspaceDraftService(session, now=lambda: later_cutoff)
        current = drafts.read(initialized.project.id)
        assert current is not None and current.content.mandate_id is not None
        later = ResearchProjectService(
            session, now=lambda: later_cutoff
        ).append_product_mandate(
            project_id=initialized.project.id,
            value=InvestmentMandateInput(
                mandate_key="company-research-default",
                horizon_years=5,
                base_currency="CNY",
                required_return=Decimal("0.12"),
                permanent_loss_limit=Decimal("0.25"),
                comparison_set=("absolute_intrinsic_value",),
            ),
            benchmark_key=None,
            required_excess_return=None,
            effective_at=later_cutoff,
            expires_at=None,
            expected_parent_id=current.content.mandate_id,
        )
        changed = drafts.save(
            initialized.project.id,
            expected_lock_version=current.lock_version,
            patch=WorkspaceDraftPatch(mandate_id=later.id),
        )
        return replace(
            bundle,
            workspace_draft_id=changed.id,
            workspace_draft_lock_version=changed.lock_version,
        )

    with pytest.raises(
        ValidationError, match="cutoff does not match reviewed evidence"
    ):
        _model_workspace(session, before_commit=substitute_mandate)

    assert (
        session.scalar(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.kind == "business_map"
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "timestamp_field", ("market_at", "available_at", "authenticated_available_at")
)
def test_model_bundle_rejects_market_data_after_the_preparation_cutoff(
    session, timestamp_field: str
) -> None:
    after_cutoff = datetime(2026, 8, 27, tzinfo=UTC)

    def move_after_cutoff(bindings, initialized):
        values = list(bindings)
        price_index = next(
            index
            for index, binding in enumerate(values)
            if binding.role is FrozenMarketSnapshotRole.PRICE
        )
        binding = values[price_index]
        row = session.get(UnderwritingPriceSnapshot, binding.snapshot_id)
        capture = session.get(
            UnderwritingMarketCaptureEnvelope, binding.capture_envelope_id
        )
        assert row is not None and capture is not None
        market_at = (
            after_cutoff
            if timestamp_field == "market_at"
            else row.market_at - timedelta(seconds=1)
        )
        available_at = (
            after_cutoff
            if timestamp_field in {"market_at", "available_at"}
            else row.available_at
        )
        successor = UnderwritingPriceSnapshot(
            security_identity_id=row.security_identity_id,
            price=row.price,
            currency=row.currency,
            price_type=row.price_type,
            adjustment_basis=row.adjustment_basis,
            market_at=market_at,
            available_at=available_at,
            source_id=row.source_id,
            raw_hash=row.raw_hash,
            content_hash="0" * 64,
            created_at=NOW,
            legacy_business_conflict=False,
        )
        successor.content_hash = price_snapshot_hash(successor)
        session.add(successor)
        session.flush([successor])
        successor_capture = UnderwritingMarketCaptureEnvelope(
            snapshot_kind=capture.snapshot_kind,
            snapshot_id=successor.id,
            provenance_role=capture.provenance_role,
            source_url=capture.source_url,
            source_locator=capture.source_locator,
            provider_policy_version=capture.provider_policy_version,
            raw_hash=capture.raw_hash,
            raw_components=capture.raw_components,
            content_hash="0" * 64,
            authenticated_available_at=(
                after_cutoff
                if timestamp_field == "authenticated_available_at"
                else capture.authenticated_available_at
            ),
            acquired_at=capture.acquired_at,
        )
        successor_capture.content_hash = market_capture_envelope_hash(successor_capture)
        session.add(successor_capture)
        session.flush([successor_capture])
        values[price_index] = replace(
            binding,
            snapshot_id=successor.id,
            snapshot_content_hash=successor.content_hash,
            capture_envelope_id=successor_capture.id,
            capture_content_hash=successor_capture.content_hash,
        )
        drafts = WorkspaceDraftService(session, now=lambda: NOW)
        draft = drafts.read(initialized.project.id)
        assert draft is not None
        drafts.save(
            initialized.project.id,
            expected_lock_version=draft.lock_version,
            patch=WorkspaceDraftPatch(
                price_snapshot_ids=tuple(
                    sorted(
                        (
                            successor.id if value == row.id else value
                            for value in draft.content.price_snapshot_ids
                        ),
                        key=str,
                    )
                )
            ),
        )
        return tuple(sorted(values, key=lambda value: str(value.snapshot_id)))

    with pytest.raises(ValidationError, match="exceeds preparation cutoff"):
        _model_workspace(session, bindings_mutation=move_after_cutoff)

    assert (
        session.scalar(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.kind == "business_map"
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "field",
    (
        "snapshot_content_hash",
        "snapshot_id",
        "snapshot_kind",
        "security_external_key",
        "capture_envelope_id",
        "capture_content_hash",
        "provider_policy_version",
        "source_role",
        "fact_key",
        "source_url",
        "source_locator",
        "raw_hash",
        "raw_components",
    ),
)
def test_model_artifact_input_hash_is_sensitive_to_every_market_binding_field(
    session, field: str
) -> None:
    initialized = _prepared(session)
    governed = CompanyResearchInitializer(session, now=lambda: NOW).governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=MARKET_CUTOFF,
    )
    assert governed.market_context is not None
    binding = (
        next(
            value
            for value in governed.market_context.snapshot_bindings
            if value.role is FrozenMarketSnapshotRole.PRICE
        )
        if field == "security_external_key"
        else governed.market_context.snapshot_bindings[0]
    )
    original = CompanyResearchRepository._model_artifact_input_hash(
        request_hash="a" * 64,
        artifact_refs=(),
        market_snapshot_bindings=(binding,),
    )
    if field in {
        "source_role",
        "fact_key",
        "source_url",
        "source_locator",
        "raw_hash",
    }:
        changed = replace(
            binding,
            source_ref=replace(
                binding.source_ref,
                **{
                    field: {
                        "source_role": "other_role",
                        "fact_key": "other_fact",
                        "source_url": "https://other.invalid/source",
                        "source_locator": "other locator",
                        "raw_hash": "c" * 64,
                    }[field]
                },
            ),
        )
    elif field == "snapshot_kind":
        changed_role = (
            FrozenMarketSnapshotRole.SECURITY_RIGHTS
            if binding.role is not FrozenMarketSnapshotRole.SECURITY_RIGHTS
            else FrozenMarketSnapshotRole.PRICE
        )
        changed = replace(
            binding,
            role=changed_role,
            security_external_key=binding.security_external_key or "NASDAQ:OTHER",
        )
    elif field == "raw_components":
        assert binding.raw_components
        changed = replace(
            binding,
            raw_components=(
                replace(binding.raw_components[0], raw_file="other.raw.gz"),
                *binding.raw_components[1:],
            ),
        )
    else:
        changed = replace(
            binding,
            **{
                field: {
                    "snapshot_content_hash": "e" * 64,
                    "snapshot_id": uuid.uuid4(),
                    "security_external_key": "NASDAQ:OTHER",
                    "capture_envelope_id": uuid.uuid4(),
                    "capture_content_hash": "d" * 64,
                    "provider_policy_version": "other-provider.v1",
                }[field]
            },
        )
    mutated = CompanyResearchRepository._model_artifact_input_hash(
        request_hash="a" * 64,
        artifact_refs=(),
        market_snapshot_bindings=(changed,),
    )

    assert mutated != original


def test_workbench_rejects_source_identity_tamper_even_with_rehashed_artifact(
    session,
) -> None:
    initialized, workbench, repository = _model_workspace(session)
    driver = repository.current_artifact(initialized.project.id, "driver_map")
    preparation = repository.preparation_for_project(initialized.project.id)
    assert driver is not None and preparation is not None
    payload = dict(driver.payload)
    lineage = dict(payload["_lineage"])
    bindings = [dict(value) for value in lineage["market_snapshot_bindings"]]
    bindings[0] = dict(bindings[0])
    bindings[0]["source_ref"] = {
        **bindings[0]["source_ref"],
        "source_role": "third_party_snapshot",
    }
    lineage["market_snapshot_bindings"] = bindings
    payload["_lineage"] = lineage
    input_hash = canonical_hash(
        {
            "request_hash": preparation.request_hash,
            "artifact_refs": lineage["artifact_refs"],
            "market_snapshot_ids": lineage["market_snapshot_ids"],
            "market_snapshot_bindings": bindings,
        }
    )
    _rewrite_payload(session, driver, payload, input_hash=input_hash)

    with pytest.raises(ValidationError, match="cross-artifact lineage"):
        workbench.workspace(project_id=initialized.project.id)


@pytest.mark.parametrize(
    "mutation",
    ("extra", "omitted", "fabricated", "source_role", "source_locator", "raw_hash"),
)
def test_workbench_rejects_rehashed_model_source_refs_not_derived_from_governed_heads(
    session, mutation: str
) -> None:
    initialized, workbench, repository = _model_workspace(session)
    memo = repository.current_artifact(initialized.project.id, "memo")
    assert memo is not None
    refs = [dict(value) for value in memo.source_refs]
    fabricated = {
        "raw_hash": "9" * 64,
        "source_locator": "fabricated:source",
        "source_role": "third_party",
        "source_url": "https://attacker.invalid/source",
    }
    if mutation == "extra":
        refs.append(fabricated)
    elif mutation == "omitted":
        refs.pop()
    elif mutation == "fabricated":
        refs[0] = fabricated
    else:
        refs[0][mutation] = {
            "source_role": "third_party",
            "source_locator": "fabricated:locator",
            "raw_hash": "8" * 64,
        }[mutation]
    _rewrite_source_refs(memo, refs)

    with pytest.raises(ValidationError, match="source refs"):
        workbench.workspace(project_id=initialized.project.id)


def test_workbench_rejects_a_rehashed_malformed_domain_artifact(session) -> None:
    initialized, workbench, repository = _model_workspace(session)
    memo = repository.current_artifact(initialized.project.id, "memo")
    assert memo is not None
    payload = dict(memo.payload)
    payload.pop("candidate_status")
    _rewrite_payload(session, memo, payload)

    with pytest.raises(ValidationError, match="memo payload is invalid"):
        workbench.workspace(project_id=initialized.project.id)


def test_workbench_rejects_a_rehashed_memo_ref_to_the_wrong_domain_payload(
    session,
) -> None:
    initialized, workbench, repository = _model_workspace(session)
    memo = repository.current_artifact(initialized.project.id, "memo")
    assert memo is not None
    payload = dict(memo.payload)
    payload["business_map_ref"] = {
        **payload["business_map_ref"],
        "content_hash": "a" * 64,
    }
    _rewrite_payload(session, memo, payload)

    with pytest.raises(ValidationError, match="memo references are invalid"):
        workbench.workspace(project_id=initialized.project.id)


@pytest.mark.parametrize("mutation", ("tampered", "missing", "duplicate"))
def test_workbench_rejects_tampered_missing_or_duplicate_cross_artifact_refs(
    session, mutation: str
) -> None:
    initialized, workbench, repository = _model_workspace(session)
    driver = repository.current_artifact(initialized.project.id, "driver_map")
    assert driver is not None
    payload = dict(driver.payload)
    lineage = dict(payload["_lineage"])
    refs = list(lineage["artifact_refs"])
    if mutation == "tampered":
        refs[0] = {**refs[0], "content_hash": "f" * 64}
    elif mutation == "missing":
        refs = []
    else:
        refs.append(dict(refs[0]))
    lineage["artifact_refs"] = refs
    payload["_lineage"] = lineage
    _rewrite_payload(session, driver, payload)

    with pytest.raises(ValidationError, match="cross-artifact lineage"):
        workbench.workspace(project_id=initialized.project.id)


def test_workbench_rejects_a_cross_artifact_ref_to_a_foreign_project(session) -> None:
    initialized, workbench, repository = _model_workspace(session)
    driver = repository.current_artifact(initialized.project.id, "driver_map")
    business = repository.current_artifact(initialized.project.id, "business_map")
    assert driver is not None and business is not None
    foreign_company = UnderwritingResearchObject(
        kind="company",
        external_key=f"US:FOREIGN:{uuid.uuid4().hex}",
        canonical_name="Foreign Company",
        created_at=NOW,
    )
    session.add(foreign_company)
    session.flush()
    foreign_project = UnderwritingResearchProject(
        primary_company_id=foreign_company.id,
        content_hash="f" * 64,
        created_at=NOW,
    )
    session.add(foreign_project)
    session.flush()
    foreign_business = repository.append_artifact(
        project_id=foreign_project.id,
        kind="business_map",
        input_hash="e" * 64,
        payload={
            key: value for key, value in business.payload.items() if key != "_lineage"
        },
        source_refs=driver.source_refs,
        expected_parent_id=None,
        created_at=NOW,
    )
    payload = dict(driver.payload)
    lineage = dict(payload["_lineage"])
    lineage["artifact_refs"] = [
        {
            "artifact_id": str(foreign_business.id),
            "artifact_kind": "business_map",
            "content_hash": foreign_business.content_hash,
        }
    ]
    payload["_lineage"] = lineage
    _rewrite_payload(session, driver, payload)

    with pytest.raises(ValidationError, match="cross-artifact lineage"):
        workbench.workspace(project_id=initialized.project.id)


def test_workbench_rejects_current_head_substitution(session) -> None:
    initialized, workbench, repository = _model_workspace(session)
    business = repository.current_artifact(initialized.project.id, "business_map")
    assert business is not None
    repository.append_artifact(
        project_id=initialized.project.id,
        kind="business_map",
        input_hash=canonical_hash({"replacement": business.content_hash}),
        payload=dict(business.payload),
        source_refs=business.source_refs,
        expected_parent_id=business.id,
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="cross-artifact lineage"):
        workbench.workspace(project_id=initialized.project.id)
