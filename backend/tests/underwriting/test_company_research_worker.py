from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.operational import Job, JobEvent
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
)
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
    current_time = NOW
    provider_attempts = 0

    def provider(preparation):
        nonlocal provider_attempts
        provider_attempts += 1
        if provider_attempts == 1:
            raise OSError("secret")
        return CompanyResearchSourceService(
            session, now=lambda: current_time
        ).compile_evidence_index(preparation_id=preparation.id)

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        provider=provider,
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "recoverable_failure"
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "queued" and job.error == "provider_unavailable"
    assert preparation is not None and preparation.status == "recoverable_failure"
    assert preparation.next_attempt_at == NOW + timedelta(seconds=30)
    assert worker.claim_next() is None

    current_time = NOW + timedelta(seconds=30)
    retry_claim = worker.claim_next()

    assert retry_claim is not None
    assert retry_claim.job_id == claim.job_id
    assert retry_claim.preparation_id == claim.preparation_id
    assert worker.run_claim(retry_claim) == "awaiting_evidence_review"
    session.flush()
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "waiting_for_review" and job.attempt == 2
    assert preparation is not None and preparation.attempt == 2
    assert provider_attempts == 2


def test_unknown_provider_exception_is_blocked_without_leaking_its_message(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        provider=lambda _preparation: (_ for _ in ()).throw(
            RuntimeError("provider secret token must not escape")
        ),
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"
    session.flush()
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "failed" and job.error == "provider_failed"
    assert preparation is not None
    assert preparation.status == "blocked"
    assert preparation.last_error_code == "provider_failed"
    artifacts = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    )
    assert artifacts == ()
    events = tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == initialized.preparation.id
            )
        )
    )
    assert events[-1].event_type == "source_preparation_blocked"
    assert events[-1].payload == {"code": "provider_failed"}
    assert "provider secret token" not in repr(events)
    job_events = tuple(
        session.scalars(select(JobEvent).where(JobEvent.job_id == initialized.job.id))
    )
    assert job_events[-1].message == "provider_failed"
    assert "provider secret token" not in repr(job_events)


def test_provider_flush_failure_rolls_back_only_provider_work_then_blocks_safely(session) -> None:
    initialized = _initialized(session)

    def provider(_preparation):
        # The duplicate primary key simulates a provider-side persistence bug.
        # Its flush invalidates the provider transaction after the worker has
        # already committed the lease-fenced claim.
        existing = session.get(Job, initialized.job.id)
        assert existing is not None
        session.expunge(existing)
        session.add(
            Job(
                id=initialized.job.id,
                kind="prepare_company_research",
                created_at=NOW,
            )
        )
        session.flush()
        raise AssertionError("unreachable")

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        provider=provider,
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"

    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "failed" and job.error == "provider_failed"
    assert preparation is not None
    assert preparation.status == "blocked"
    assert preparation.last_error_code == "provider_failed"
    events = tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == initialized.preparation.id
            )
        )
    )
    job_events = tuple(
        session.scalars(select(JobEvent).where(JobEvent.job_id == initialized.job.id))
    )
    assert events[-1].payload == {"code": "provider_failed"}
    assert job_events[-1].message == "provider_failed"
    assert "UNIQUE constraint" not in repr((events, job_events))


def test_manual_retry_accepts_a_due_worker_scheduled_recoverable_job(session) -> None:
    initialized = _initialized(session)
    current_time = NOW
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        provider=lambda _preparation: (_ for _ in ()).throw(OSError("secret")),
    )
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "recoverable_failure"

    current_time = NOW + timedelta(seconds=30)
    retried = CompanyResearchPreparationService(
        session, now=lambda: current_time
    ).retry(project_id=initialized.project.id)

    assert retried.preparation.status == "queued"


def test_recoverable_provider_retries_stop_after_the_third_attempt(session) -> None:
    initialized = _initialized(session)
    current_time = NOW
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        provider=lambda _preparation: (_ for _ in ()).throw(OSError("secret")),
    )

    first_claim = worker.claim_next()
    assert first_claim is not None
    assert worker.run_claim(first_claim) == "recoverable_failure"

    current_time = NOW + timedelta(seconds=30)
    second_claim = worker.claim_next()
    assert second_claim is not None
    assert worker.run_claim(second_claim) == "recoverable_failure"

    current_time = NOW + timedelta(seconds=150)
    third_claim = worker.claim_next()
    assert third_claim is not None
    assert worker.run_claim(third_claim) == "recoverable_failure"
    assert worker.claim_next() is None
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "failed" and job.attempt == 3
    assert preparation is not None and preparation.status == "blocked"


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
