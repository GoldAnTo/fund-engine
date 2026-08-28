from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import ValidationError
from app.models.operational import Job, JobEvent
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.company_research_sources import CompanyResearchSourceService
from app.underwriting.services.product_foundation_fixture import ProductFoundationFixtureService
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
from app.scripts import run_company_research_worker


NOW = datetime(2026, 8, 26, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _initialized(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    initializer = CompanyResearchInitializer(session, now=lambda: NOW)
    preview = initializer.preview(
        company_id=loaded.objects["US:ALPHABET:COMPANY"].id, cutoff_at=CUTOFF
    )
    return initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=preview.company.object_id,
        cutoff_at=CUTOFF,
        idempotency_key="company-worker-alphabet",
    )


def _review_all_evidence(session, initialized):
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
    return current


def _ready_for_model(session):
    initialized = _initialized(session)
    source_worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    source_claim = source_worker.claim_next()
    assert source_claim is not None
    assert source_worker.run_claim(source_claim) == "awaiting_evidence_review"
    _review_all_evidence(session, initialized)
    return initialized


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


def test_worker_builds_all_model_artifacts_after_last_evidence_review(session) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "awaiting_judgment_review"
    repository = CompanyResearchRepository(session)
    heads = {
        kind: repository.current_artifact(initialized.project.id, kind)
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "judgment_context",
            "research_gaps",
            "memo",
        )
    }
    assert all(row is not None for row in heads.values())
    # The governed market boundary is complete, but the reviewed golden-case
    # evidence still has explicit critical operating-baseline gaps.  The
    # pipeline therefore persists the full non-valuation bundle and remains
    # honestly not answerable rather than manufacturing a valuation.
    assert repository.current_artifact(initialized.project.id, "valuation_set") is None
    judgment = heads["judgment_context"]
    memo = heads["memo"]
    assert judgment is not None and judgment.payload["market_security_bridge_available"]
    assert memo is not None and memo.payload["assessment_status"] == "not_answerable"
    assert judgment.payload["_lineage"]["market_snapshot_ids"]
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None
    assert (preparation.status, preparation.current_step, preparation.progress) == (
        "awaiting_judgment_review",
        "judgment_context",
        85,
    )
    assert job is not None
    assert (job.status, job.step, job.progress, job.claim_token) == (
        "waiting_for_review",
        "judgment_context",
        85,
        None,
    )


def test_missing_market_inputs_complete_with_not_answerable_and_gaps(
    session, monkeypatch
) -> None:
    initialized = _ready_for_model(session)
    session.commit()
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    governed = worker._governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=worker._repository.evidence_cutoff(
            worker._repository.current_artifact(
                initialized.project.id, "evidence_index"
            )
        ),
    )
    session.rollback()
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    draft = drafts.read(initialized.project.id)
    assert draft is not None
    drafts.save(
        initialized.project.id,
        expected_lock_version=draft.lock_version,
        patch=WorkspaceDraftPatch(
            price_snapshot_ids=(),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=None,
            security_rights_ids=(),
        ),
    )
    session.commit()
    monkeypatch.setattr(
        worker,
        "_governed_inputs",
        lambda **_kwargs: replace(governed, market_context=None),
    )

    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "awaiting_judgment_review"

    repository = CompanyResearchRepository(session)
    assert repository.current_artifact(initialized.project.id, "valuation_set") is None
    memo = repository.current_artifact(initialized.project.id, "memo")
    gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert memo is not None and memo.payload["assessment_status"] == "not_answerable"
    assert gaps is not None
    assert {item["code"] for item in gaps.payload["gaps"]} >= {
        "market_price_missing",
        "usd_cny_fx_missing",
    }


def test_model_provider_failure_is_requeued_with_bounded_backoff(session) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(
            TimeoutError("provider secret")
        ),
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"

    assert worker.run_claim(claim) == "recoverable_failure"

    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None
    assert (job.status, job.step, job.attempt, job.error) == (
        "queued",
        "model_bundle",
        2,
        "provider_unavailable",
    )
    assert preparation is not None
    assert preparation.status == "recoverable_failure"
    assert preparation.current_step == "model_bundle"
    assert preparation.next_attempt_at == NOW + timedelta(seconds=30)
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_manual_retry_keeps_a_model_failure_on_the_model_bundle_step(session) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(
            ConnectionError("secret")
        ),
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "recoverable_failure"

    retried = CompanyResearchPreparationService(
        session, now=lambda: NOW + timedelta(seconds=30)
    ).retry(project_id=initialized.project.id)

    assert retried.preparation.status == "building_model"
    retry = CompanyResearchPreparationWorker(
        session, now=lambda: NOW + timedelta(seconds=30)
    ).claim_next()
    assert retry is not None and retry.step == "model_bundle"


def test_manual_retry_does_not_consume_the_three_execution_attempt_budget(session) -> None:
    initialized = _ready_for_model(session)
    current_time = NOW
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        model_provider=lambda _input: (_ for _ in ()).throw(
            ConnectionError("secret")
        ),
    )
    first = worker.claim_next()
    assert first is not None
    assert worker.run_claim(first) == "recoverable_failure"
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and preparation is not None
    assert (job.attempt, preparation.attempt) == (2, 2)
    assert preparation.next_attempt_at == NOW + timedelta(seconds=30)

    current_time = NOW + timedelta(seconds=30)
    CompanyResearchPreparationService(
        session, now=lambda: current_time
    ).retry(project_id=initialized.project.id)
    assert (job.attempt, preparation.attempt) == (2, 2)
    second = worker.claim_next()
    assert second is not None
    assert worker.run_claim(second) == "recoverable_failure"
    assert (job.attempt, preparation.attempt) == (3, 3)
    assert preparation.next_attempt_at == current_time + timedelta(seconds=120)

    current_time += timedelta(seconds=120)
    third = worker.claim_next()
    assert third is not None
    assert worker.run_claim(third) == "recoverable_failure"
    assert job.status == "failed"
    assert preparation.status == "blocked"
    assert (job.attempt, preparation.attempt) == (3, 3)


@pytest.mark.parametrize(
    "error",
    (
        ValidationError("schema invalid"),
        ValidationError("hash invalid"),
        ValidationError("lineage invalid"),
        ValidationError("financial bridge does not close"),
    ),
)
def test_invalid_model_output_is_blocked_without_partial_artifacts(session, error) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(error),
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and preparation.status == "blocked"
    assert job is not None and job.status == "failed"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_stale_model_claim_is_recovered_and_reuses_the_same_job(session) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    job = session.get(Job, initialized.job.id)
    assert job is not None
    job.started_at = NOW - timedelta(minutes=31)
    session.flush()

    assert worker.recover_stale_claims(before=NOW - timedelta(minutes=30)) == 1
    assert job.status == "queued"
    assert job.claim_token is None
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert preparation is not None
    assert (preparation.status, preparation.current_step, preparation.progress) == (
        "building_model",
        "model_bundle",
        25,
    )
    retry = worker.claim_next()
    assert retry is not None and retry.job_id == claim.job_id


def test_model_crash_before_bundle_commit_recovers_without_partial_heads(
    session, monkeypatch
) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None

    monkeypatch.setattr(
        worker._repository,
        "complete_model_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("simulated model worker crash")
        ),
    )
    with pytest.raises(SystemExit, match="simulated model worker crash"):
        worker.run_claim(claim)

    repository = CompanyResearchRepository(session)
    assert repository.current_artifact(initialized.project.id, "business_map") is None
    job = session.get(Job, initialized.job.id)
    assert job is not None and job.status == "running"
    job.started_at = NOW - timedelta(minutes=31)
    session.flush()
    retry_worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW + timedelta(minutes=31)
    )
    assert retry_worker.recover_stale_claims(before=NOW) == 1
    retry = retry_worker.claim_next()
    assert retry is not None and retry.job_id == claim.job_id
    assert retry_worker.run_claim(retry) == "awaiting_judgment_review"


def test_committed_model_bundle_survives_crash_and_duplicate_worker_converges(
    session,
) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_judgment_review"
    session.commit()

    bind = session.get_bind()
    restarted = Session(bind=bind, future=True)
    try:
        duplicate = CompanyResearchPreparationWorker(
            restarted, now=lambda: NOW + timedelta(hours=1)
        )
        assert duplicate.claim_next() is None
        assert duplicate.recover_stale_claims(
            before=NOW + timedelta(hours=1)
        ) == 0
        rows = tuple(
            restarted.scalars(
                select(CompanyResearchArtifactVersion).where(
                    CompanyResearchArtifactVersion.project_id
                    == initialized.project.id
                )
            )
        )
        kinds = [row.kind for row in rows]
        assert kinds.count("business_map") == 1
        assert kinds.count("memo") == 1
    finally:
        restarted.rollback()
        restarted.close()


def test_model_provider_runs_without_claim_lock_and_stale_output_is_discarded(
    session,
) -> None:
    initialized = _ready_for_model(session)
    session.commit()

    def provider(build_input):
        external = Session(bind=session.get_bind(), future=True)
        try:
            changed = external.get(
                CompanyResearchPreparation, initialized.preparation.id
            )
            assert changed is not None
            changed.strategy_version = "changed-while-provider-ran.v1"
            external.commit()
        finally:
            external.close()
        from app.underwriting.services.company_research_model_builder import (
            CompanyResearchModelBuilder,
        )

        return CompanyResearchModelBuilder().build(build_input)

    worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW, model_provider=provider
    )
    claim = worker.claim_next()
    session.commit()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_draft_change_while_model_provider_runs_discards_stale_output(session) -> None:
    initialized = _ready_for_model(session)
    session.commit()
    artifact_ids_before = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion.id).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    )

    def provider(build_input):
        external = Session(bind=session.get_bind(), future=True)
        try:
            drafts = WorkspaceDraftService(external, now=lambda: NOW)
            draft = drafts.read(initialized.project.id)
            assert draft is not None
            drafts.save(
                initialized.project.id,
                expected_lock_version=draft.lock_version,
                patch=WorkspaceDraftPatch(user_focus="changed while building"),
            )
            external.commit()
        finally:
            external.close()
        from app.underwriting.services.company_research_model_builder import (
            CompanyResearchModelBuilder,
        )

        return CompanyResearchModelBuilder().build(build_input)

    worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW, model_provider=provider
    )
    claim = worker.claim_next()
    session.commit()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"

    repository = CompanyResearchRepository(session)
    assert repository.current_artifact(initialized.project.id, "business_map") is None
    artifact_ids_after = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion.id).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    )
    assert artifact_ids_after == artifact_ids_before
    events = tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == initialized.preparation.id
            ).order_by(CompanyResearchEvent.sequence)
        )
    )
    assert events[-1].event_type == "stale_output_discarded"
    assert all(event.event_type != "model_preparation_blocked" for event in events)


@pytest.mark.parametrize(
    "error",
    (
        FileNotFoundError("missing governed file"),
        PermissionError("governed file forbidden"),
        TypeError("provider contract bug"),
        AttributeError("provider implementation bug"),
        RuntimeError("unknown deterministic bug"),
    ),
)
def test_deterministic_provider_errors_rollback_and_escape(session, error) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(error),
    )
    claim = worker.claim_next()
    assert claim is not None

    with pytest.raises(type(error), match=str(error)):
        worker.run_claim(claim)

    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None
    job = session.get(Job, initialized.job.id)
    assert job is not None and job.status == "running"
    assert job.claim_token == claim.claim_token


def test_worker_cli_exits_nonzero_and_logs_a_deterministic_provider_error() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    script = """
import sys
from unittest.mock import patch
from app.scripts import run_company_research_worker as worker

sys.argv = ["run_company_research_worker", "--once"]
with patch.object(worker, "_touch", lambda **kwargs: None), patch.object(
    worker, "run_once", side_effect=TypeError("visible provider bug")
):
    worker.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "visible provider bug" in result.stderr


def test_entrypoint_crash_after_commit_converges_without_duplicate_artifacts(
    session, monkeypatch
) -> None:
    initialized = _ready_for_model(session)
    session.commit()
    sessions = sessionmaker(bind=session.get_bind(), future=True)
    original = CompanyResearchPreparationWorker.run_claim

    def crash_after_commit(self, claim):
        result = original(self, claim)
        raise SystemExit(f"crash after durable {result}")

    monkeypatch.setattr(
        CompanyResearchPreparationWorker, "run_claim", crash_after_commit
    )
    with pytest.raises(SystemExit, match="crash after durable"):
        run_company_research_worker.run_once(session_factory=sessions)
    monkeypatch.setattr(CompanyResearchPreparationWorker, "run_claim", original)

    assert not run_company_research_worker.run_once(session_factory=sessions)
    with sessions() as restarted:
        rows = tuple(
            restarted.scalars(
                select(CompanyResearchArtifactVersion).where(
                    CompanyResearchArtifactVersion.project_id
                    == initialized.project.id
                )
            )
        )
        assert sum(row.kind == "business_map" for row in rows) == 1
        assert sum(row.kind == "memo" for row in rows) == 1


def test_company_research_preparation_imports_in_a_clean_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.underwriting.services.company_research_preparation",
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_model_commit_receives_exact_claim_request_and_strategy_fence(
    session, monkeypatch
) -> None:
    _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    original = worker._repository.complete_model_bundle
    observed = {}

    def fenced(*args, **kwargs):
        observed.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker._repository, "complete_model_bundle", fenced)

    assert worker.run_claim(claim) == "awaiting_judgment_review"
    assert observed["expected_claim_token"] == claim.claim_token
    assert observed["expected_request_hash"] == claim.request_hash
    assert observed["expected_strategy_version"] == claim.strategy_version


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

    def provider(provider_input):
        nonlocal provider_attempts
        provider_attempts += 1
        if provider_attempts == 1:
            raise ConnectionError("secret")
        return CompanyResearchSourceService(
            session, now=lambda: current_time
        ).compile_evidence_index(preparation_id=provider_input.preparation_id)

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


def test_unknown_source_provider_exception_rolls_back_and_escapes(session) -> None:
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

    with pytest.raises(RuntimeError, match="provider secret token must not escape"):
        worker.run_claim(claim)

    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and job.status == "running" and job.error is None
    assert preparation is not None and preparation.status == "preparing_sources"
    artifacts = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    )
    assert artifacts == ()


def test_manual_retry_preserves_attempt_after_worker_scheduled_recoverable_failure(
    session,
) -> None:
    initialized = _initialized(session)
    current_time = NOW
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        provider=lambda _preparation: (_ for _ in ()).throw(
            ConnectionError("secret")
        ),
    )
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "recoverable_failure"
    assert initialized.job.status == "queued"
    assert (initialized.job.attempt, initialized.preparation.attempt) == (2, 2)

    current_time = NOW + timedelta(seconds=30)
    retried = CompanyResearchPreparationService(
        session, now=lambda: current_time
    ).retry(project_id=initialized.project.id)

    assert retried.preparation.status == "queued"
    assert (initialized.job.attempt, retried.preparation.attempt) == (2, 2)


def test_manual_retry_advances_attempt_after_synchronous_source_failure(
    session, monkeypatch
) -> None:
    initialized = _initialized(session)
    source_service = CompanyResearchSourceService(session, now=lambda: NOW)

    def unavailable_fixture():
        raise ValidationError("source parser failed")

    monkeypatch.setattr(source_service, "_load_fixture", unavailable_fixture)

    failed = source_service.prepare_evidence_index(
        preparation_id=initialized.preparation.id
    )

    assert failed.status == "recoverable_failure"
    assert failed.job.status == "failed"
    assert (failed.job.attempt, initialized.preparation.attempt) == (1, 1)

    retried = CompanyResearchPreparationService(
        session, now=lambda: NOW
    ).retry(project_id=initialized.project.id)

    assert retried.preparation.status == "queued"
    assert (failed.job.attempt, retried.preparation.attempt) == (2, 2)


def test_recoverable_provider_retries_stop_after_the_third_attempt(session) -> None:
    initialized = _initialized(session)
    current_time = NOW
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: current_time,
        provider=lambda _preparation: (_ for _ in ()).throw(
            ConnectionError("secret")
        ),
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

    def provider(provider_input):
        output = CompanyResearchSourceService(
            session, now=lambda: NOW
        ).compile_evidence_index(preparation_id=provider_input.preparation_id)
        # A separate actor can change the immutable input after provider work;
        # this is distinct from provider work: a separate actor owns its own
        # database session and can change the immutable input concurrently.
        external = Session(bind=session.get_bind(), future=True)
        try:
            changed = external.get(
                CompanyResearchPreparation, provider_input.preparation_id
            )
            assert changed is not None
            changed.strategy_version = "new-strategy.v1"
            external.commit()
        finally:
            external.close()
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


def test_artifact_head_change_after_provider_work_discards_worker_output(session) -> None:
    initialized = _initialized(session)

    def provider(provider_input):
        output = CompanyResearchSourceService(
            session, now=lambda: NOW
        ).compile_evidence_index(preparation_id=provider_input.preparation_id)
        # The worker has already committed its claim before provider work.  A
        # separate SQLite session can therefore publish a new artifact head
        # before this worker reaches its fenced completion transaction.
        external = Session(bind=session.get_bind(), future=True)
        try:
            CompanyResearchRepository(external).append_artifact(
                project_id=initialized.project.id,
                kind="evidence_index",
                input_hash=output.input_hash,
                payload=output.evidence_index_payload,
                source_refs=output.source_refs,
                expected_parent_id=None,
                created_at=NOW,
            )
            external.commit()
        finally:
            external.close()
        return output

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW, provider=provider)
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "discarded"
    session.expire_all()
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None
    assert job.status == "cancelled"
    assert job.error == "stale_output_discarded"
    assert job.claim_token is None
    assert preparation is not None
    assert preparation.status == "blocked"
    assert preparation.last_error_code == "stale_output_discarded"
    artifacts = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion)
            .where(CompanyResearchArtifactVersion.project_id == initialized.project.id)
            .order_by(CompanyResearchArtifactVersion.created_at)
        )
    )
    assert [artifact.kind for artifact in artifacts] == ["evidence_index"]
    events = tuple(
        session.scalars(
            select(CompanyResearchEvent)
            .where(CompanyResearchEvent.preparation_id == initialized.preparation.id)
            .order_by(CompanyResearchEvent.sequence)
        )
    )
    assert events[-1].event_type == "stale_output_discarded"
    assert all(event.event_type != "evidence_index_prepared" for event in events)


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


def test_stale_recovery_ignores_corrupt_or_non_source_company_jobs(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and preparation is not None
    job.target_type = "legacy_company_research"
    job.started_at = NOW - timedelta(minutes=31)
    before_job_events = tuple(
        session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
    )
    before_events = tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == preparation.id
            )
        )
    )
    session.flush()

    assert worker.recover_stale_claims(before=NOW - timedelta(minutes=30)) == 0
    assert job.status == "running"
    assert preparation.status == "preparing_sources"
    assert tuple(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))) == before_job_events
    assert tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == preparation.id
            )
        )
    ) == before_events


def test_queued_cancel_never_reaches_the_provider(session) -> None:
    initialized = _initialized(session)
    job = session.get(Job, initialized.job.id)
    assert job is not None
    job.cancel_requested = True
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    assert worker.cancel_queued_claims() == 1
    assert worker.claim_next() is None
    assert job.status == "cancelled"


def test_queued_cancel_ignores_corrupt_or_non_source_company_jobs(session) -> None:
    initialized = _initialized(session)
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and preparation is not None
    job.target_id = uuid4()
    job.cancel_requested = True
    before_job_events = tuple(
        session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
    )
    before_events = tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == preparation.id
            )
        )
    )
    session.flush()

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    assert worker.cancel_queued_claims() == 0
    assert job.status == "queued"
    assert preparation.status == "queued"
    assert tuple(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))) == before_job_events
    assert tuple(
        session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == preparation.id
            )
        )
    ) == before_events


def test_provider_cannot_commit_or_add_durable_database_writes(session) -> None:
    initialized = _initialized(session)
    leaked_job_id = uuid4()
    reference = CompanyResearchSourceService(
        session, now=lambda: NOW
    ).compile_evidence_index(preparation_id=initialized.preparation.id)

    def provider(provider_input):
        with pytest.raises(AttributeError):
            provider_input.add(
                Job(id=leaked_job_id, kind="provider_write", created_at=NOW)
            )
        with pytest.raises(AttributeError):
            provider_input.commit()
        return reference

    worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW, provider=provider
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "awaiting_evidence_review"
    assert session.get(Job, leaked_job_id) is None
    assert session.get(Job, initialized.job.id).status == "waiting_for_review"


def test_provider_receives_an_immutable_snapshot_without_session_write_apis(session) -> None:
    initialized = _initialized(session)
    reference = CompanyResearchSourceService(
        session, now=lambda: NOW
    ).compile_evidence_index(preparation_id=initialized.preparation.id)

    def provider(provider_input):
        assert provider_input.__class__.__name__ == "CompanyResearchProviderInput"
        assert not hasattr(provider_input, "add")
        assert not hasattr(provider_input, "flush")
        assert not hasattr(provider_input, "commit")
        with pytest.raises(FrozenInstanceError):
            provider_input.request_hash = "rewritten"
        return reference

    worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW, provider=provider
    )
    claim = worker.claim_next()
    assert claim is not None

    assert worker.run_claim(claim) == "awaiting_evidence_review"
    assert session.get(Job, initialized.job.id).status == "waiting_for_review"


def test_corrupt_claim_ownership_never_cancels_the_foreign_job(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    job = session.get(Job, initialized.job.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert job is not None and preparation is not None
    job.target_type = "foreign_job"
    session.flush()

    assert worker.run_claim(claim) == "discarded"
    assert job.status == "running"
    assert job.claim_token == claim.claim_token
    assert preparation.status == "preparing_sources"


def test_crash_after_provider_before_output_commit_retries_the_same_job(session, monkeypatch) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None

    def crash_before_output(*_args, **_kwargs):
        raise SystemExit("simulated worker crash")

    monkeypatch.setattr(
        worker._repository, "complete_evidence_preparation", crash_before_output
    )
    with pytest.raises(SystemExit, match="simulated worker crash"):
        worker.run_claim(claim)

    job = session.get(Job, initialized.job.id)
    assert job is not None and job.status == "running"
    job.started_at = NOW - timedelta(minutes=31)
    session.flush()
    retry_worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW + timedelta(minutes=31)
    )
    assert retry_worker.recover_stale_claims(before=NOW) == 1
    retry_claim = retry_worker.claim_next()
    assert retry_claim is not None and retry_claim.job_id == claim.job_id
    assert retry_worker.run_claim(retry_claim) == "awaiting_evidence_review"


def test_committed_output_survives_a_worker_crash_after_artifact_event_convergence(session) -> None:
    initialized = _initialized(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    preparation_id = initialized.preparation.id
    job_id = initialized.job.id
    project_id = initialized.project.id

    # Simulate process teardown after the worker's final commit, then inspect
    # the durable state through a fresh session.
    bind = session.get_bind()
    session.rollback()
    session.close()
    restarted = Session(bind=bind, future=True)
    try:
        preparation = restarted.get(
            CompanyResearchPreparation, preparation_id
        )
        job = restarted.get(Job, job_id)
        assert preparation is not None and preparation.status == "awaiting_evidence_review"
        assert job is not None and job.status == "waiting_for_review"
        assert len(
            tuple(
                restarted.scalars(
                    select(CompanyResearchArtifactVersion).where(
                        CompanyResearchArtifactVersion.project_id == project_id
                    )
                )
            )
        ) == 2
        assert CompanyResearchPreparationWorker(
            restarted, now=lambda: NOW + timedelta(hours=1)
        ).recover_stale_claims(before=NOW + timedelta(hours=1)) == 0
    finally:
        restarted.rollback()
        restarted.close()


@pytest.mark.pg_only
def test_postgresql_two_workers_claim_the_same_company_job_exclusively(engine) -> None:
    sessions = sessionmaker(bind=engine, future=True)
    initializer_session = sessions()
    first_session = sessions()
    second_session = sessions()
    try:
        _initialized(initializer_session)
        initializer_session.commit()
        first = CompanyResearchPreparationWorker(first_session, now=lambda: NOW)
        second = CompanyResearchPreparationWorker(second_session, now=lambda: NOW)

        assert first.claim_next() is not None
        assert second.claim_next() is None
    finally:
        first_session.rollback()
        second_session.rollback()
        initializer_session.rollback()
        first_session.close()
        second_session.close()
        initializer_session.close()
