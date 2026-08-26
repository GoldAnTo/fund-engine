from datetime import UTC, datetime

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
