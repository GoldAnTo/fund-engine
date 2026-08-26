from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.operational import Job
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.services.company_research_initializer import CompanyResearchInitializer
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_sources import CompanyResearchSourceService
from app.underwriting.services.product_foundation_fixture import ProductFoundationFixtureService


NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _initialized(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    initializer = CompanyResearchInitializer(session, now=lambda: NOW)
    preview = initializer.preview(
        company_id=loaded.objects["US:ALPHABET:COMPANY"].id, cutoff_at=NOW
    )
    return initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=preview.company.object_id,
        cutoff_at=NOW,
        idempotency_key="company-worker-alphabet",
    )


def test_claim_is_exclusive_and_success_stops_at_evidence_review(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    claim = worker.claim_next()

    assert claim is not None
    assert worker.claim_next() is None
    assert claim.claim_token
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    session.flush()
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and preparation.status == "awaiting_evidence_review"
    assert job is not None and job.status == "waiting_for_review"
    assert {
        artifact.kind
        for artifact in session.scalars(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    } == {"evidence_index", "research_gaps"}


def test_stale_claim_discards_provider_output_without_artifact(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    job = session.get(Job, initialized.job.id)
    assert job is not None
    job.cancel_requested = True
    session.flush()

    assert worker.run_claim(claim) == "discarded"
    assert session.scalar(
        select(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.project_id == initialized.project.id
        )
    ) is None
    assert session.get(Job, initialized.job.id).status == "cancelled"


def test_recoverable_provider_failure_is_requeued_with_bounded_backoff(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        provider=lambda _preparation: (_ for _ in ()).throw(OSError("secret")),
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "recoverable_failure"
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "queued" and job.error == "provider_unavailable"
    assert preparation is not None and preparation.status == "recoverable_failure"
    assert preparation.next_attempt_at == NOW + timedelta(seconds=30)


def test_strategy_drift_after_provider_work_discards_the_output(session) -> None:
    initialized = _initialized(session)

    def provider(preparation):
        output = CompanyResearchSourceService(
            session, now=lambda: NOW
        ).compile_evidence_index(preparation_id=preparation.id)
        preparation.strategy_version = "new-strategy.v1"
        session.flush()
        return output

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW, provider=provider)
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"
    assert session.scalar(
        select(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.project_id == initialized.project.id
        )
    ) is None
    assert session.get(Job, initialized.job.id).status == "cancelled"


def test_stale_running_claim_is_recovered_without_cloning_the_job(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    job = session.get(Job, initialized.job.id)
    assert job is not None
    job.started_at = NOW - timedelta(minutes=31)
    session.flush()

    assert worker.recover_stale_claims(before=NOW - timedelta(minutes=30)) == 1
    assert job.status == "queued"
    assert job.claim_token is None
    assert session.get(CompanyResearchPreparation, initialized.preparation.id).status == "queued"


def test_queued_cancel_never_reaches_the_provider(session) -> None:
    initialized = _initialized(session)
    job = session.get(Job, initialized.job.id)
    assert job is not None
    job.cancel_requested = True
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    assert worker.cancel_queued_claims() == 1
    assert worker.claim_next() is None
    assert job.status == "cancelled"
