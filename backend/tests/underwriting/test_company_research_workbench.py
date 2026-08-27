from datetime import UTC, datetime
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from app.models.ledger import ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
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
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)


NOW = datetime(2026, 8, 26, tzinfo=UTC)
MARKET_CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _prepared(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    initializer = CompanyResearchInitializer(session, now=lambda: NOW)
    preview = initializer.preview(
        company_id=loaded.objects["US:ALPHABET:COMPANY"].id, cutoff_at=NOW
    )
    initialized = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=preview.company.object_id,
        cutoff_at=NOW,
        idempotency_key="company-workbench-alphabet",
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    return initialized


def _model_workspace(session):
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
    source_refs = tuple(current.source_refs)
    prior_gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert prior_gaps is not None
    governed = CompanyResearchInitializer(session, now=lambda: NOW).governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=MARKET_CUTOFF,
    )
    assert governed.market_context is not None
    bundle = CompanyResearchPersistedBundle(
        evidence_artifact_id=current.id,
        evidence_content_hash=current.content_hash,
        research_gaps_artifact_id=prior_gaps.id,
        research_gaps_content_hash=prior_gaps.content_hash,
        business_map={
            "modules": [{"key": "search", "fact_keys": ["revenue"]}],
        },
        driver_map={"drivers": [{"driver_key": "queries"}]},
        financial_bridge={"scenario_id": "base", "rows": []},
        scenario_set={"scenarios": [{"scenario_id": "base"}]},
        valuation_set={"security_value_ranges": []},
        judgment_context={"assessment_status": "answerable"},
        research_gaps=dict(prior_gaps.payload),
        memo={"candidate_status": "machine_draft"},
        source_refs=source_refs,
        market_snapshot_bindings=governed.market_context.snapshot_bindings,
    )
    repository.complete_model_bundle(
        preparation.id,
        bundle=bundle,
        expected_claim_token="model-claim",
        expected_request_hash=preparation.request_hash,
        expected_strategy_version=preparation.strategy_version,
        created_at=NOW,
    )
    return initialized, workbench, repository


def _rewrite_payload(session, row, payload: dict) -> None:
    content_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=row.project_id,
        kind=row.kind,
        version=row.version,
        supersedes_id=row.supersedes_id,
        parent_content_hash=row.parent_content_hash,
        input_hash=row.input_hash,
        payload=payload,
        source_refs=row.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET payload = ?, content_hash = ? WHERE id = ?",
        (json.dumps(payload), content_hash, row.id.hex),
    )
    session.expire_all()


def test_workspace_is_a_closed_snapshot_of_the_review_gate(session) -> None:
    initialized = _prepared(session)

    workspace = CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
        project_id=initialized.project.id
    )

    assert workspace.project_id == initialized.project.id
    assert workspace.company.external_key == "US:ALPHABET:COMPANY"
    assert workspace.preparation.status == "awaiting_evidence_review"
    assert len(workspace.modules) == 9
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


def test_last_evidence_review_enqueues_business_map_for_the_worker(session) -> None:
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

    assert claim is not None
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
            }
        )


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
        payload={"modules": []},
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
