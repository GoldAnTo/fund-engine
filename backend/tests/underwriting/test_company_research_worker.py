from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, create_engine, event as sa_event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.models.operational import Job, JobEvent
from app.underwriting.domain.product_contracts import ProductHistoricalBasisInput
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
)
from app.underwriting.services.company_research_basis_recovery import (
    CompanyResearchHistoricalBasisRecovery,
)
from app.underwriting.services.company_research_foundation import (
    alphabet_company_research_foundation_contract,
    build_alphabet_company_research_preview_at_cutoff,
)
from app.underwriting.services.company_research_boundary import (
    resolve_alphabet_company_research_boundary,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.market_snapshots import (
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    price_snapshot_hash,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchSourceCompiler,
    CompanyResearchSourceService,
)
from app.underwriting.services.product_foundation_fixture import ProductFoundationFixtureService
from app.underwriting.services.product_project import (
    ResearchProjectService,
    research_agenda_content_hash,
    research_project_security_content_hash,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
from app.scripts import run_company_research_worker


NOW = datetime(2026, 8, 26, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)
LEGACY_REQUEST_CUTOFF = datetime(2026, 8, 28, 4, 8, 33, 490000, tzinfo=UTC)
LEGACY_PROJECT_CREATED = datetime(2026, 8, 28, 4, 8, 35, 304392, tzinfo=UTC)
LEGACY_MANDATE_CREATED = datetime(2026, 8, 28, 4, 8, 35, 512718, tzinfo=UTC)
LEGACY_PREPARATION_CREATED = datetime(2026, 8, 28, 4, 8, 35, 743556, tzinfo=UTC)
LEGACY_EVIDENCE_MANIFEST_HASH = (
    "632f9e40fb2707a16b3cc104910b45ab9d71b916bcc666e141e56b2d2046200b"
)


def _pre_market_evidence_compilation(provider_input):
    compiled = CompanyResearchSourceCompiler().compile_evidence_index(provider_input)
    evidence_payload = deepcopy(compiled.evidence_index_payload)
    evidence_payload["fixture_content_hash"] = LEGACY_EVIDENCE_MANIFEST_HASH
    gaps_payload = deepcopy(compiled.research_gaps_payload)
    gaps_payload["fixture_content_hash"] = LEGACY_EVIDENCE_MANIFEST_HASH
    legacy_reasons = {
        "market_price_missing": "No authenticated market price is bundled.",
        "usd_cny_fx_missing": "No authenticated USD/CNY FX rate is bundled.",
    }
    for gap in gaps_payload["gaps"]:
        if gap["gap_key"] in legacy_reasons:
            gap["reason"] = legacy_reasons[gap["gap_key"]]
    return replace(
        compiled,
        input_hash=LEGACY_EVIDENCE_MANIFEST_HASH,
        evidence_index_payload=evidence_payload,
        research_gaps_payload=gaps_payload,
    )


def _original_pre_market_evidence_compilation(provider_input):
    compiled = _pre_market_evidence_compilation(provider_input)
    fixture = load_alphabet_golden_case_fixture()
    source_refs = tuple(
        {
            "source_role": fact.source_role,
            "source_url": fact.source_url,
            "source_locator": fact.source_locator,
            "raw_hash": fact.raw_hash,
        }
        for fact in sorted(
            fixture.facts,
            key=lambda item: (
                item.source_role,
                item.source_url,
                item.source_locator,
                item.fact_key,
            ),
        )
    )
    assert len(source_refs) == 7
    return replace(compiled, source_refs=source_refs)


def _initialized(session, *, idempotency_key="company-worker-alphabet"):
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
        idempotency_key=idempotency_key,
    )


def _genuine_legacy_initialized(session):
    loaded = ProductFoundationFixtureService(
        session, now=lambda: LEGACY_PROJECT_CREATED
    ).load(load_product_foundation_fixture())
    company = loaded.objects["US:ALPHABET:COMPANY"]
    preview = build_alphabet_company_research_preview_at_cutoff(
        session,
        company_id=company.id,
        cutoff_at=LEGACY_REQUEST_CUTOFF,
    )
    project = ResearchProjectService(
        session, now=lambda: LEGACY_PROJECT_CREATED
    ).create_project(
        primary_company_id=company.id,
        target_security_ids=tuple(row.object_id for row in preview.securities),
    )
    foundation = alphabet_company_research_foundation_contract(
        company_external_key=preview.company.external_key,
        company_id=preview.company.object_id,
        security_ids=tuple(row.object_id for row in preview.securities),
        request_hash=preview.input_hash,
        strategy_version=preview.strategy_version,
    )
    mandate = ResearchProjectService(
        session, now=lambda: LEGACY_MANDATE_CREATED
    ).append_product_mandate(
        project_id=project.id,
        value=foundation.mandate,
        benchmark_key=None,
        required_excess_return=None,
        effective_at=LEGACY_REQUEST_CUTOFF,
        expires_at=None,
        expected_parent_id=None,
    )
    scope = ResearchProjectService(
        session, now=lambda: LEGACY_MANDATE_CREATED + timedelta(milliseconds=50)
    ).append_scope(project.id, foundation.scope, expected_parent_id=None)
    agenda = ResearchProjectService(
        session, now=lambda: LEGACY_MANDATE_CREATED + timedelta(milliseconds=100)
    ).append_agenda(
        project.id,
        foundation.agenda(scope.id),
        expected_parent_id=None,
    )
    draft = WorkspaceDraftService(
        session, now=lambda: LEGACY_MANDATE_CREATED + timedelta(milliseconds=150)
    ).create(
        project.id,
        initial_content=WorkspaceDraftContent(
            mandate_id=mandate.id,
            scope_id=scope.id,
            agenda_id=agenda.id,
            historical_basis_id=None,
        ),
    )
    preparation_id = uuid4()
    job = Job(
        id=uuid4(),
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        step="evidence_index",
        target_type="company_research_preparation",
        target_id=preparation_id,
        research_case_id=None,
        created_at=LEGACY_PREPARATION_CREATED,
    )
    session.add(job)
    session.flush([job])
    repository = CompanyResearchRepository(session)
    preparation = repository.add_preparation(
        project_id=project.id,
        idempotency_key="genuine-pre-7707036",
        request_hash=preview.input_hash,
        strategy_version=preview.strategy_version,
        status="queued",
        current_step="evidence_index",
        progress=0,
        attempt=1,
        next_attempt_at=None,
        last_error_code=None,
        job_id=job.id,
        created_at=LEGACY_PREPARATION_CREATED,
        updated_at=LEGACY_PREPARATION_CREATED,
    )
    repository.append_event(
        preparation_id=preparation.id,
        event_type="initialized",
        payload={"request_hash": preview.input_hash},
        created_at=LEGACY_PREPARATION_CREATED,
    )
    return SimpleNamespace(
        project=project,
        basis=None,
        mandate=mandate,
        scope=scope,
        agenda=agenda,
        draft=draft,
        preparation=preparation,
        job=job,
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


def _review_evidence_with_one_rejection(session, initialized, *, now=NOW):
    workbench = CompanyResearchWorkbench(session, now=lambda: now)
    workspace = workbench.workspace(project_id=initialized.project.id)
    evidence = next(
        item.artifact for item in workspace.modules if item.key == "evidence_and_gaps"
    )
    assert evidence is not None
    current = evidence
    for index, fact in enumerate(evidence.payload["facts"]):
        current = workbench.review_evidence(
            project_id=initialized.project.id,
            evidence_artifact_id=current.id,
            fact_key=fact["fact_key"],
            decision="rejected" if index == len(evidence.payload["facts"]) - 1 else "confirmed",
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


def _legacy_blocked_missing_basis(
    session, *, initialized=None, now=NOW, provider=None
):
    initialized = initialized or _initialized(session)
    worker = CompanyResearchPreparationWorker(
        session, now=lambda: now, provider=provider
    )
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "awaiting_evidence_review"
    reviewed = _review_evidence_with_one_rejection(session, initialized, now=now)
    repository = CompanyResearchRepository(session)
    gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert gaps is not None
    drafts = WorkspaceDraftService(session, now=lambda: now)
    draft = drafts.read(initialized.project.id)
    assert draft is not None
    if draft.content.historical_basis_id is not None:
        drafts.save(
            initialized.project.id,
            expected_lock_version=draft.lock_version,
            patch=WorkspaceDraftPatch(historical_basis_id=None),
        )
    model_claim = worker.claim_next()
    assert model_claim is not None and model_claim.step == "model_bundle"
    assert worker.run_claim(model_claim) == "discarded"
    blocked_draft = drafts.read(initialized.project.id)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert blocked_draft is not None and preparation is not None and job is not None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.last_error_code,
        job.status,
        job.step,
        job.error,
    ) == (
        "blocked",
        "model_bundle",
        30,
        "validation_failed",
        "failed",
        "model_bundle",
        "validation_failed: company research historical basis is missing",
    )
    return initialized, reviewed, gaps, blocked_draft


def test_historical_basis_recovery_accepts_a_genuine_pre_7707036_request_cutoff(
    session,
) -> None:
    initialized = _genuine_legacy_initialized(session)
    assert initialized.mandate.effective_at == LEGACY_REQUEST_CUTOFF
    assert initialized.project.id is not None
    boundary = resolve_alphabet_company_research_boundary(CUTOFF)
    assert ProductRepository(session).product_basis_by_content_hash(
        boundary.basis_content_hash
    ) is None
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session,
        initialized=initialized,
        now=LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
    )
    _durably_rewrite_events_as_legacy_v1(
        session, initialized.preparation.id
    )
    repository = CompanyResearchRepository(session)
    assert {
        event.hash_version
        for event in repository.events(initialized.preparation.id)
    } == {1}

    result = CompanyResearchPreparationService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=1)
    ).retry(project_id=initialized.project.id)
    recovered = WorkspaceDraftService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=1)
    ).read(initialized.project.id)

    assert recovered is not None
    assert result.preparation.status == "building_model"
    assert recovered.content.historical_basis_id is not None
    assert recovered.lock_version == blocked_draft.lock_version + 1
    events = repository.events(initialized.preparation.id)
    assert [event.event_type for event in events[-2:]] == [
        "historical_basis_recovered",
        "retry_queued",
    ]
    assert [event.hash_version for event in events[-2:]] == [2, 2]
    assert all(event.hash_version == 1 for event in events[:-2])


@pytest.mark.parametrize(
    "provider",
    (_pre_market_evidence_compilation, _original_pre_market_evidence_compilation),
)
def test_historical_basis_recovery_accepts_the_authenticated_pre_market_evidence_manifest(
    session, provider
) -> None:
    initialized = _genuine_legacy_initialized(session)
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session,
        initialized=initialized,
        now=LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
        provider=provider,
    )

    result = CompanyResearchPreparationService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=2)
    ).retry(project_id=initialized.project.id)
    recovered = WorkspaceDraftService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=2)
    ).read(initialized.project.id)

    assert recovered is not None
    assert recovered.lock_version == blocked_draft.lock_version + 1
    assert recovered.content.historical_basis_id is not None
    assert result.preparation.status == "building_model"
    basis = ProductRepository(session).product_basis(
        recovered.content.historical_basis_id
    )
    assert basis is not None
    assert basis.source_manifest_hash == LEGACY_EVIDENCE_MANIFEST_HASH

    model_worker = CompanyResearchPreparationWorker(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=3)
    )
    claim = model_worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert model_worker.run_claim(claim) == "awaiting_judgment_review"
    repository = CompanyResearchRepository(session)
    evidence_after = repository.current_artifact(
        initialized.project.id, "evidence_index"
    )
    gaps_after = repository.current_artifact(initialized.project.id, "research_gaps")
    assert evidence_after is not None and gaps_after is not None
    assert (evidence_after.id, evidence_after.content_hash) == (
        reviewed.id,
        reviewed.content_hash,
    )
    assert (gaps_after.id, gaps_after.content_hash) == (gaps.id, gaps.content_hash)


@pytest.mark.parametrize("provider", (None, _pre_market_evidence_compilation))
def test_workbench_rejects_rehashed_substitute_source_gaps_for_every_governed_manifest(
    session, provider
) -> None:
    initialized = _genuine_legacy_initialized(session)
    initialized, _reviewed, gaps, _blocked_draft = _legacy_blocked_missing_basis(
        session,
        initialized=initialized,
        now=LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
        provider=provider,
    )
    payload = deepcopy(gaps.payload)
    payload["gaps"][0]["reason"] = "Substituted after publication."
    _durably_rewrite_artifact(session, gaps, payload=payload)

    with pytest.raises(
        ValidationError,
        match="company research evidence lineage is invalid",
    ):
        CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
            project_id=initialized.project.id
        )


@pytest.mark.parametrize("provider", (None, _pre_market_evidence_compilation))
def test_model_worker_rejects_rehashed_source_gaps_after_basis_recovery(
    session, provider
) -> None:
    initialized = _genuine_legacy_initialized(session)
    initialized, _reviewed, gaps, _blocked_draft = _legacy_blocked_missing_basis(
        session,
        initialized=initialized,
        now=LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
        provider=provider,
    )
    CompanyResearchPreparationService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=2)
    ).retry(project_id=initialized.project.id)
    payload = deepcopy(gaps.payload)
    payload["gaps"][0]["reason"] = "Substituted after basis recovery."
    _durably_rewrite_artifact(session, gaps, payload=payload)

    def forbidden_model_provider(_build_input):
        pytest.fail("model provider must not receive substituted source gaps")

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=3),
        model_provider=forbidden_model_provider,
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"


@pytest.mark.parametrize("provider", (None, _pre_market_evidence_compilation))
def test_model_worker_rejects_a_rehashed_review_successor_after_basis_recovery(
    session, provider
) -> None:
    initialized = _genuine_legacy_initialized(session)
    initialized, reviewed, _gaps, _blocked_draft = _legacy_blocked_missing_basis(
        session,
        initialized=initialized,
        now=LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
        provider=provider,
    )
    CompanyResearchPreparationService(
        session, now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=2)
    ).retry(project_id=initialized.project.id)
    payload = deepcopy(reviewed.payload)
    payload["facts"][0]["value"] = "999999"
    _durably_rewrite_artifact(session, reviewed, payload=payload)

    def forbidden_model_provider(_build_input):
        pytest.fail("model provider must not receive substituted reviewed evidence")

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=3),
        model_provider=forbidden_model_provider,
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"


def test_review_rejects_the_original_legacy_seven_to_eight_ref_mutation_before_writing(
    session,
) -> None:
    initialized = _genuine_legacy_initialized(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: LEGACY_PREPARATION_CREATED + timedelta(seconds=1),
        provider=_original_pre_market_evidence_compilation,
    )
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    repository = CompanyResearchRepository(session)
    evidence = repository.current_artifact(initialized.project.id, "evidence_index")
    gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert evidence is not None and gaps is not None
    invalid_refs = [*evidence.source_refs, dict(evidence.source_refs[0])]
    _durably_rewrite_artifact(session, evidence, source_refs=invalid_refs)
    _durably_rewrite_artifact(session, gaps, source_refs=invalid_refs)
    artifact_count = session.scalar(
        select(func.count())
        .select_from(CompanyResearchArtifactVersion)
        .where(CompanyResearchArtifactVersion.project_id == initialized.project.id)
    )
    event_count = len(repository.events(initialized.preparation.id))

    with pytest.raises(ValidationError):
        CompanyResearchWorkbench(session, now=lambda: NOW).review_evidence(
            project_id=initialized.project.id,
            evidence_artifact_id=evidence.id,
            fact_key=evidence.payload["facts"][0]["fact_key"],
            decision="confirmed",
            expected_head_id=evidence.id,
        )
    session.commit()

    assert session.scalar(
        select(func.count())
        .select_from(CompanyResearchArtifactVersion)
        .where(CompanyResearchArtifactVersion.project_id == initialized.project.id)
    ) == artifact_count
    assert len(repository.events(initialized.preparation.id)) == event_count


def test_missing_historical_basis_validation_diagnostic_is_safe_and_actionable(
    session,
) -> None:
    initialized, _reviewed, _gaps, _blocked_draft = (
        _legacy_blocked_missing_basis(session)
    )

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert preparation.last_error_code == "validation_failed"
    assert (
        job.error
        == "validation_failed: company research historical basis is missing"
    )
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == "company research historical basis is missing"
    company_events = CompanyResearchRepository(session).events(preparation.id)
    assert company_events[-1].event_type == "model_preparation_blocked"
    assert company_events[-1].payload == {
        "code": "validation_failed",
        "message": "company research historical basis is missing",
    }


def test_internal_validation_diagnostic_is_single_line_and_bounded(
    session, monkeypatch
) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    raw_message = "  internal\n validation\tmessage   " + "sensitive-detail " * 80
    normalized = " ".join(raw_message.split())
    monkeypatch.setattr(
        worker,
        "_model_input",
        lambda _claim: (_ for _ in ()).throw(ValidationError(raw_message)),
    )

    assert worker.run_claim(claim) == "discarded"

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert preparation.last_error_code == "validation_failed"
    prefix = "validation_failed: "
    assert job.error == prefix + normalized[: 512 - len(prefix)]
    assert len(job.error) == 512
    assert "\n" not in job.error and "\t" not in job.error
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == normalized[:512]
    assert len(job_events[-1].message) == 512
    company_events = CompanyResearchRepository(session).events(preparation.id)
    assert company_events[-1].payload == {
        "code": "validation_failed",
        "message": normalized[:512],
    }


@pytest.mark.parametrize("raw_message", ("", " \n\t  "))
def test_empty_internal_validation_diagnostic_falls_back_to_code_without_colon(
    session, monkeypatch, raw_message
) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    monkeypatch.setattr(
        worker,
        "_model_input",
        lambda _claim: (_ for _ in ()).throw(ValidationError(raw_message)),
    )

    assert worker.run_claim(claim) == "discarded"

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert preparation.last_error_code == "validation_failed"
    assert job.error == "validation_failed"
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == "validation_failed"
    company_events = CompanyResearchRepository(session).events(preparation.id)
    assert company_events[-1].payload == {"code": "validation_failed"}


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


def test_model_completion_preserves_the_reviewed_research_gaps_head(session) -> None:
    initialized = _ready_for_model(session)
    repository = CompanyResearchRepository(session)
    gaps_before = repository.current_artifact(
        initialized.project.id, "research_gaps"
    )
    assert gaps_before is not None
    gaps_snapshot = (
        gaps_before.id,
        gaps_before.version,
        gaps_before.content_hash,
        deepcopy(gaps_before.payload),
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "awaiting_judgment_review"
    gaps_after = repository.current_artifact(
        initialized.project.id, "research_gaps"
    )
    assert gaps_after is not None
    assert (
        gaps_after.id,
        gaps_after.version,
        gaps_after.content_hash,
        gaps_after.payload,
    ) == gaps_snapshot
    assert len(repository.artifact_chain(gaps_after.id)) == 1
    judgment = repository.current_artifact(
        initialized.project.id, "judgment_context"
    )
    assert judgment is not None
    assert any(
        reference["artifact_kind"] == "research_gaps"
        and reference["artifact_id"] == str(gaps_before.id)
        and reference["content_hash"] == gaps_before.content_hash
        for reference in judgment.payload["_lineage"]["artifact_refs"]
    )


def test_model_completion_rejects_derived_gaps_that_conflict_with_reviewed_source_gaps(
    session,
) -> None:
    from app.underwriting.domain.company_research import (
        ResearchGap,
        ResearchGapSeverity,
    )
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)
    repository = CompanyResearchRepository(session)
    gaps_before = repository.current_artifact(
        initialized.project.id, "research_gaps"
    )
    assert gaps_before is not None
    raw_gap = gaps_before.payload["gaps"][0]

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        conflicting = ResearchGap(
            code=raw_gap["gap_key"],
            module_key=raw_gap["business_module"],
            severity=ResearchGapSeverity.HIGH,
            message="provider attempted to reopen a governed-closed source gap",
        )
        return replace(result, gaps=(conflicting, *result.gaps))

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )

    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    gaps_after = repository.current_artifact(
        initialized.project.id, "research_gaps"
    )
    assert gaps_after is not None and gaps_after.id == gaps_before.id
    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    assert preparation is not None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.last_error_code,
    ) == ("blocked", "model_bundle", 30, "validation_failed")


def test_model_completion_rejects_a_derived_gap_without_business_map_projection(
    session,
) -> None:
    from app.underwriting.domain.company_research import (
        ResearchGap,
        ResearchGapSeverity,
    )
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        extra = ResearchGap(
            code="zz_provider_extra_gap",
            module_key="overview",
            severity=ResearchGapSeverity.HIGH,
            message="Provider gap has no business map projection.",
        )
        gaps = tuple(sorted((*result.gaps, extra), key=lambda gap: gap.code))
        return replace(
            result,
            gaps=gaps,
            memo=replace(
                result.memo,
                gap_keys=tuple(gap.code for gap in gaps),
            ),
            judgment_context=replace(
                result.judgment_context,
                next_verification_events=tuple(gap.message for gap in gaps),
            ),
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )

    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_model_completion_rejects_a_derived_gap_projected_to_the_wrong_module(
    session,
) -> None:
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        source_codes = {
            gap["gap_key"] for gap in build_input.gap_payload["gaps"]
        }
        target = next(gap for gap in result.gaps if gap.code not in source_codes)
        wrong_module = (
            "business_map" if target.module_key == "overview" else "overview"
        )
        changed = replace(target, module_key=wrong_module)
        return replace(
            result,
            gaps=tuple(
                changed if gap.code == target.code else gap
                for gap in result.gaps
            ),
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )

    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_model_completion_rejects_an_assessment_that_disagrees_with_the_memo(
    session,
) -> None:
    from app.underwriting.domain.company_research import CompanyResearchAssessment
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        return replace(
            result,
            assessment=CompanyResearchAssessment.partially_answerable(),
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


@pytest.mark.parametrize("field", ("assessment", "memo"))
def test_model_completion_bounds_malformed_nested_provider_results(
    session, field
) -> None:
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def malformed_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        return replace(result, **{field: None})

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=malformed_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and preparation.status == "blocked"
    assert job is not None and job.status == "failed"


def test_model_completion_rejects_memo_judgment_divergence(session) -> None:
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        return replace(
            result,
            memo=replace(
                result.memo,
                next_verification_events=(
                    *result.memo.next_verification_events,
                    "Memo-only verification event.",
                ),
            ),
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_model_completion_rejects_memo_counterevidence_divergence(session) -> None:
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        available_ref = next(
            reference
            for module in result.business_map.modules
            for reference in module.fact_refs
            if reference not in result.memo.strongest_counterevidence
        )
        return replace(
            result,
            memo=replace(
                result.memo,
                strongest_counterevidence=(
                    *result.memo.strongest_counterevidence,
                    available_ref,
                ),
            ),
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_model_completion_rejects_a_projected_critical_gap_with_answerable_assessment(
    session,
) -> None:
    from app.underwriting.domain.company_research import (
        CompanyResearchAssessment,
        ResearchGap,
        ResearchGapSeverity,
    )
    from app.underwriting.services.company_research_artifact_codec import (
        CompanyResearchArtifactCodec,
    )
    from app.underwriting.services.company_research_model_builder import (
        CompanyResearchModelBuilder,
    )

    initialized = _ready_for_model(session)

    def conflicting_provider(build_input):
        result = CompanyResearchModelBuilder().build(build_input)
        module = result.business_map.modules[0]
        extra = ResearchGap(
            code="zz_provider_critical_gap",
            module_key=module.module_key,
            severity=ResearchGapSeverity.CRITICAL,
            message="Provider critical gap must close valuation.",
        )
        gaps = tuple(sorted((*result.gaps, extra), key=lambda gap: gap.code))
        business_map = replace(
            result.business_map,
            modules=(
                replace(
                    module,
                    gap_refs=tuple(sorted((*module.gap_refs, extra.code))),
                ),
                *result.business_map.modules[1:],
            ),
        )
        events = tuple(gap.message for gap in gaps)
        memo = replace(
            result.memo,
            assessment_status="answerable",
            business_map_ref=replace(
                result.memo.business_map_ref,
                content_hash=canonical_hash(
                    CompanyResearchArtifactCodec.encode(
                        "business_map", business_map
                    )
                ),
            ),
            gap_keys=tuple(gap.code for gap in gaps),
            next_verification_events=events,
            research_gaps=gaps,
        )
        return replace(
            result,
            assessment=CompanyResearchAssessment.answerable(),
            business_map=business_map,
            gaps=gaps,
            judgment_context=replace(
                result.judgment_context,
                next_verification_events=events,
            ),
            memo=memo,
        )

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=conflicting_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "business_map"
    ) is None


def test_worker_blocks_a_contract_incompatible_historical_basis_before_model_provider(
    session,
) -> None:
    initialized = _ready_for_model(session)
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    draft = drafts.read(initialized.project.id)
    assert draft is not None and draft.content.historical_basis_id is not None
    original = ProductRepository(session).product_basis(draft.content.historical_basis_id)
    assert original is not None
    substitute = ResearchProjectService(
        session, now=lambda: NOW
    ).create_historical_basis(
        ProductHistoricalBasisInput(
            cutoff_at=CompanyResearchRepository._persisted_utc(original.cutoff),
            source_manifest_hash=original.source_manifest_hash,
            definition_bundle_hash="d" * 64,
            parser_bundle_hash="e" * 64,
        )
    )
    drafts.save(
        initialized.project.id,
        expected_lock_version=draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=substitute.id),
    )
    model_calls = []

    def model_provider(build_input):
        from app.underwriting.services.company_research_model_builder import (
            CompanyResearchModelBuilder,
        )

        model_calls.append(build_input)
        return CompanyResearchModelBuilder().build(build_input)

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=model_provider,
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "discarded"
    assert model_calls == []
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None
    assert (preparation.status, preparation.current_step, preparation.last_error_code) == (
        "blocked",
        "model_bundle",
        "validation_failed",
    )
    assert job is not None
    assert (job.status, job.step, job.error, job.claim_token) == (
        "failed",
        "model_bundle",
        "validation_failed: company research historical basis does not match "
        "governed model contract",
        None,
    )
    repository = CompanyResearchRepository(session)
    assert all(
        repository.current_artifact(initialized.project.id, kind) is None
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        )
    )


def test_worker_blocks_a_durably_corrupted_historical_basis_before_model_provider(
    session,
) -> None:
    initialized = _ready_for_model(session)
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.content.historical_basis_id is not None
    basis = ProductRepository(session).product_basis(draft.content.historical_basis_id)
    assert basis is not None
    tamper = text(
        "UPDATE uw_historical_bases "
        "SET content_hash = :content_hash WHERE id = :basis_id"
    ).bindparams(
        bindparam(
            "content_hash",
            type_=UnderwritingHistoricalBasis.__table__.c.content_hash.type,
        ),
        bindparam(
            "basis_id",
            type_=UnderwritingHistoricalBasis.__table__.c.id.type,
        ),
    )
    tampered = session.connection().execute(
        tamper,
        {"content_hash": "0" * 64, "basis_id": basis.id},
    )
    assert tampered.rowcount == 1
    model_calls = []
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda build_input: model_calls.append(build_input),
    )
    claim = worker.claim_next()

    assert claim is not None and claim.step == "model_bundle"
    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        worker._model_input(claim)
    assert worker.run_claim(claim) == "discarded"
    assert model_calls == []
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None
    assert (preparation.status, preparation.last_error_code) == (
        "blocked",
        "validation_failed",
    )
    assert job is not None
    assert (job.status, job.error, job.claim_token) == (
        "failed",
        "validation_failed: company research historical basis is invalid",
        None,
    )
    repository = CompanyResearchRepository(session)
    assert all(
        repository.current_artifact(initialized.project.id, kind) is None
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        )
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
    assert {item["gap_key"] for item in gaps.payload["gaps"]} >= {
        "market_price_missing",
        "usd_cny_fx_missing",
    }
    assert set(memo.payload["gap_keys"]) >= {
        "market_price_missing",
        "usd_cny_fx_missing",
    }


def test_model_provider_failure_is_requeued_with_bounded_backoff(session) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(
            TimeoutError("provider secret\nline two")
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
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == "provider_unavailable"
    company_event = CompanyResearchRepository(session).events(preparation.id)[-1]
    assert company_event.payload == {
        "code": "provider_unavailable",
        "recoverable": True,
    }
    assert "provider secret\nline two" not in str(job_events[-1].message)
    assert "provider secret\nline two" not in str(company_event.payload)


def test_model_provider_validation_error_is_sanitized_at_provider_boundary(
    session,
) -> None:
    initialized = _ready_for_model(session)
    raw_message = "provider secret\nline two"
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW,
        model_provider=lambda _input: (_ for _ in ()).throw(
            ValidationError(raw_message)
        ),
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"

    assert worker.run_claim(claim) == "discarded"

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert (preparation.status, preparation.last_error_code) == (
        "blocked",
        "validation_failed",
    )
    assert (job.status, job.error) == ("failed", "validation_failed")
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == "validation_failed"
    company_events = CompanyResearchRepository(session).events(preparation.id)
    company_event = company_events[-1]
    assert company_event.event_type == "model_preparation_blocked"
    assert company_event.payload == {"code": "validation_failed"}
    persisted_diagnostics = (
        job.error,
        *(event.message for event in job_events),
        *(str(event.payload) for event in company_events),
    )
    for forbidden in ("provider", "secret", "line two", raw_message):
        assert all(forbidden not in str(value) for value in persisted_diagnostics)


def test_post_provider_bundle_validation_diagnostic_remains_actionable(
    session, monkeypatch
) -> None:
    initialized = _ready_for_model(session)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    monkeypatch.setattr(
        worker,
        "_persisted_bundle",
        lambda *_args: (_ for _ in ()).throw(
            ValidationError("trusted bundle validation failed")
        ),
    )

    assert worker.run_claim(claim) == "discarded"

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert preparation.last_error_code == "validation_failed"
    assert job.error == "validation_failed: trusted bundle validation failed"
    job_events = tuple(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.seq)
        )
    )
    assert job_events[-1].message == "trusted bundle validation failed"
    company_event = CompanyResearchRepository(session).events(preparation.id)[-1]
    assert company_event.payload == {
        "code": "validation_failed",
        "message": "trusted bundle validation failed",
    }


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


def test_retry_recovers_only_a_missing_historical_basis_without_rewriting_reviewed_state(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = CompanyResearchRepository(session)
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.last_error_code,
    ) == ("blocked", "model_bundle", 30, "validation_failed")
    assert (job.status, job.step, job.error) == (
        "failed",
        "model_bundle",
        "validation_failed: company research historical basis is missing",
    )
    reviewed_snapshot = (
        reviewed.id,
        reviewed.version,
        reviewed.content_hash,
        deepcopy(reviewed.payload["facts"]),
    )
    gaps_snapshot = (gaps.id, gaps.version, gaps.content_hash, deepcopy(gaps.payload))
    missing_content = blocked_draft.content.model_dump(mode="json")
    events_before = repository.events(initialized.preparation.id)

    retried = CompanyResearchPreparationService(
        session, now=lambda: NOW + timedelta(seconds=1)
    ).retry(project_id=initialized.project.id)

    assert (
        retried.preparation.status,
        retried.preparation.current_step,
        retried.preparation.progress,
        retried.preparation.last_error_code,
    ) == ("building_model", "model_bundle", 25, None)
    recovered = drafts.read(initialized.project.id)
    assert recovered is not None and recovered.content.historical_basis_id is not None
    assert recovered.lock_version == blocked_draft.lock_version + 1
    recovered_content = recovered.content.model_dump(mode="json")
    recovered_content["historical_basis_id"] = None
    assert recovered_content == missing_content
    reviewed_after = repository.current_artifact(initialized.project.id, "evidence_index")
    gaps_after = repository.current_artifact(initialized.project.id, "research_gaps")
    assert reviewed_after is not None and gaps_after is not None
    assert (
        reviewed_after.id,
        reviewed_after.version,
        reviewed_after.content_hash,
        reviewed_after.payload["facts"],
    ) == reviewed_snapshot
    assert (
        gaps_after.id,
        gaps_after.version,
        gaps_after.content_hash,
        gaps_after.payload,
    ) == gaps_snapshot
    events_after = repository.events(initialized.preparation.id)
    assert len(events_after) == len(events_before) + 2
    assert [event.event_type for event in events_after[-2:]] == [
        "historical_basis_recovered",
        "retry_queued",
    ]
    assert events_after[-2].payload["prior_lock_version"] == blocked_draft.lock_version
    assert events_after[-2].payload["new_lock_version"] == blocked_draft.lock_version + 1


def _append_recovery_evidence(
    session,
    initialized,
    reviewed,
    mutate,
):
    repository = CompanyResearchRepository(session)
    payload = deepcopy(reviewed.payload)
    mutate(payload)
    return repository.append_artifact(
        project_id=initialized.project.id,
        kind="evidence_index",
        input_hash="f" * 64,
        payload=payload,
        source_refs=reviewed.source_refs,
        expected_parent_id=reviewed.id,
        created_at=NOW + timedelta(seconds=1),
    )


def _durably_rewrite_artifact(
    session,
    row,
    *,
    payload=None,
    source_refs=None,
    input_hash=None,
):
    repository = CompanyResearchRepository(session)
    persisted = session.get(CompanyResearchArtifactVersion, row.id)
    assert persisted is not None
    row = persisted
    rewritten_payload = deepcopy(row.payload) if payload is None else payload
    rewritten_refs = deepcopy(row.source_refs) if source_refs is None else source_refs
    rewritten_input_hash = row.input_hash if input_hash is None else input_hash
    content_hash = repository.artifact_content_hash(
        project_id=row.project_id,
        kind=row.kind,
        version=row.version,
        supersedes_id=row.supersedes_id,
        parent_content_hash=row.parent_content_hash,
        input_hash=rewritten_input_hash,
        payload=rewritten_payload,
        source_refs=rewritten_refs,
    )
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET "
        "payload = :payload, source_refs = :source_refs, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "source_refs",
            type_=CompanyResearchArtifactVersion.__table__.c.source_refs.type,
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    assert session.connection().execute(
        statement,
        {
            "payload": rewritten_payload,
            "source_refs": rewritten_refs,
            "input_hash": rewritten_input_hash,
            "content_hash": content_hash,
            "id": row.id,
        },
    ).rowcount == 1
    session.expire(row)
    return row


def _durably_rewrite_review_successors(session, head, mutate_payload) -> None:
    repository = CompanyResearchRepository(session)
    chain = repository.artifact_chain(head.id)
    rewritten_hashes = {chain[0].id: chain[0].content_hash}
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET "
        "parent_content_hash = :parent_content_hash, payload = :payload, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "parent_content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.parent_content_hash.type,
        ),
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    for parent, row in zip(chain, chain[1:], strict=False):
        parent_facts = parent.payload["facts"]
        row_facts = row.payload["facts"]
        reviewed = [
            after
            for before, after in zip(parent_facts, row_facts, strict=True)
            if before != after
        ]
        assert len(reviewed) == 1
        fact_key = reviewed[0]["fact_key"]
        decision = reviewed[0]["review_decision"]
        payload = deepcopy(row.payload)
        mutate_payload(payload)
        parent_content_hash = rewritten_hashes[parent.id]
        input_hash = canonical_hash(
            {
                "parent": parent_content_hash,
                "fact_key": fact_key,
                "decision": decision,
            }
        )
        content_hash = repository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=parent_content_hash,
            input_hash=input_hash,
            payload=payload,
            source_refs=row.source_refs,
        )
        assert session.connection().execute(
            statement,
            {
                "parent_content_hash": parent_content_hash,
                "payload": payload,
                "input_hash": input_hash,
                "content_hash": content_hash,
                "id": row.id,
            },
        ).rowcount == 1
        rewritten_hashes[row.id] = content_hash
    session.expire_all()


def _durably_rewrite_first_review_decision(session, head, decision) -> None:
    """Keep the complete chain cryptographically valid with one malformed decision."""
    repository = CompanyResearchRepository(session)
    chain = repository.artifact_chain(head.id)
    first_parent, first_successor = chain[:2]
    changed = [
        after
        for before, after in zip(
            first_parent.payload["facts"],
            first_successor.payload["facts"],
            strict=True,
        )
        if before != after
    ]
    assert len(changed) == 1
    target_fact_key = changed[0]["fact_key"]
    rewritten_hashes = {chain[0].id: chain[0].content_hash}
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET "
        "parent_content_hash = :parent_content_hash, payload = :payload, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "parent_content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.parent_content_hash.type,
        ),
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    for parent, row in zip(chain, chain[1:], strict=False):
        original_changes = [
            after
            for before, after in zip(
                parent.payload["facts"], row.payload["facts"], strict=True
            )
            if before != after
        ]
        assert len(original_changes) == 1
        reviewed_fact = original_changes[0]
        fact_key = reviewed_fact["fact_key"]
        transition_decision = reviewed_fact["review_decision"]
        payload = deepcopy(row.payload)
        for fact in payload["facts"]:
            if fact["fact_key"] == target_fact_key:
                fact["review_decision"] = decision
                if fact_key == target_fact_key:
                    transition_decision = decision
                break
        parent_content_hash = rewritten_hashes[parent.id]
        input_hash = canonical_hash(
            {
                "parent": parent_content_hash,
                "fact_key": fact_key,
                "decision": transition_decision,
            }
        )
        content_hash = repository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=parent_content_hash,
            input_hash=input_hash,
            payload=payload,
            source_refs=row.source_refs,
        )
        assert session.connection().execute(
            statement,
            {
                "parent_content_hash": parent_content_hash,
                "payload": payload,
                "input_hash": input_hash,
                "content_hash": content_hash,
                "id": row.id,
            },
        ).rowcount == 1
        rewritten_hashes[row.id] = content_hash
    session.expire_all()


def _durably_rewrite_review_events(session, preparation_id, mode: str) -> None:
    repository = CompanyResearchRepository(session)
    events = list(repository.events(preparation_id))
    review_indexes = [
        index
        for index, event in enumerate(events)
        if event.event_type == "evidence_reviewed"
    ]
    assert len(review_indexes) >= 2
    if mode == "duplicate":
        last = events[review_indexes[0]]
        repository.append_event(
            preparation_id=preparation_id,
            event_type="evidence_reviewed",
            payload=last.payload,
            created_at=events[-1].created_at.replace(tzinfo=UTC)
            + timedelta(seconds=1),
        )
        return
    payloads = [deepcopy(event.payload) for event in events]
    created_ats = [event.created_at for event in events]
    first = review_indexes[0]
    if mode == "wrong_artifact":
        payloads[first]["evidence_artifact_id"] = str(uuid4())
    elif mode == "wrong_fact":
        payloads[first]["fact_key"] = "substituted_fact"
    elif mode == "wrong_decision":
        payloads[first]["decision"] = (
            "rejected"
            if payloads[first]["decision"] == "confirmed"
            else "confirmed"
        )
    elif mode == "reordered":
        second = review_indexes[1]
        payloads[first], payloads[second] = payloads[second], payloads[first]
    elif mode == "timestamp_mismatch":
        for index in range(first, len(created_ats)):
            created_ats[index] = created_ats[index] + timedelta(seconds=1)
    else:
        raise AssertionError(mode)
    statement = text(
        "UPDATE uw_company_research_events SET previous_event_hash = :previous, "
        "payload = :payload, created_at = :created_at, "
        "content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "previous",
            type_=CompanyResearchEvent.__table__.c.previous_event_hash.type,
        ),
        bindparam("payload", type_=CompanyResearchEvent.__table__.c.payload.type),
        bindparam(
            "created_at", type_=CompanyResearchEvent.__table__.c.created_at.type
        ),
        bindparam("id", type_=CompanyResearchEvent.__table__.c.id.type),
    )
    previous_hash = None
    for event, payload, created_at in zip(
        events, payloads, created_ats, strict=True
    ):
        content_hash = repository.event_content_hash_v2(
            preparation_id=event.preparation_id,
            sequence=event.sequence,
            previous_event_hash=previous_hash,
            event_type=event.event_type,
            payload=payload,
            created_at=created_at,
        )
        session.execute(
            statement,
            {
                "previous": previous_hash,
                "payload": payload,
                "created_at": created_at,
                "content_hash": content_hash,
                "id": event.id,
            },
        )
        previous_hash = content_hash
    session.expire_all()


def _durably_rewrite_events_as_legacy_v1(session, preparation_id) -> None:
    repository = CompanyResearchRepository(session)
    events = repository.events(preparation_id)
    statement = text(
        "UPDATE uw_company_research_events SET hash_version = 1, "
        "previous_event_hash = :previous, content_hash = :content_hash "
        "WHERE id = :id"
    ).bindparams(
        bindparam(
            "previous",
            type_=CompanyResearchEvent.__table__.c.previous_event_hash.type,
        ),
        bindparam("id", type_=CompanyResearchEvent.__table__.c.id.type),
    )
    previous_hash = None
    for event in events:
        content_hash = repository.event_content_hash(
            preparation_id=event.preparation_id,
            sequence=event.sequence,
            previous_event_hash=previous_hash,
            event_type=event.event_type,
            payload=event.payload,
        )
        assert session.execute(
            statement,
            {
                "previous": previous_hash,
                "content_hash": content_hash,
                "id": event.id,
            },
        ).rowcount == 1
        previous_hash = content_hash
    session.expire_all()


@pytest.mark.parametrize("decision", ([], {}, None, 1))
def test_historical_basis_recovery_rejects_malformed_review_decision(
    session, decision
) -> None:
    initialized, reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    _durably_rewrite_first_review_decision(session, reviewed, decision)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize("field", ("fact_key", "source_refs"))
def test_historical_basis_recovery_bounds_other_malformed_review_fields(
    session, field
) -> None:
    initialized, reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = CompanyResearchRepository(session)
    chain = repository.artifact_chain(reviewed.id)
    parent, head = chain[-2:]
    payload = deepcopy(head.payload)
    source_refs = deepcopy(head.source_refs)
    input_hash = head.input_hash
    if field == "fact_key":
        changed = [
            after
            for before, after in zip(
                parent.payload["facts"], payload["facts"], strict=True
            )
            if before != after
        ]
        assert len(changed) == 1
        changed[0]["fact_key"] = []
        input_hash = canonical_hash(
            {
                "parent": parent.content_hash,
                "fact_key": [],
                "decision": changed[0]["review_decision"],
            }
        )
    else:
        source_refs = [[]]
    _durably_rewrite_artifact(
        session,
        head,
        payload=payload,
        source_refs=source_refs,
        input_hash=input_hash,
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize(
    ("case", "mutate"),
    (
        (
            "unexpected_top_level_key",
            lambda payload: payload.__setitem__("unexpected_top_level_key", True),
        ),
        (
            "changed_company_identity",
            lambda payload: payload.__setitem__(
                "company_external_key", "US:OTHER:COMPANY"
            ),
        ),
    ),
)
def test_historical_basis_recovery_authenticates_every_review_successor_payload(
    session, case, mutate
) -> None:
    initialized, reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    _durably_rewrite_review_successors(session, reviewed, mutate)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize(
    ("mutation", "apply"),
    (
        ("delete_fact", lambda payload, refs: payload["facts"].pop()),
        (
            "change_fact_key",
            lambda payload, refs: payload["facts"][0].__setitem__(
                "fact_key", "changed_fact_key"
            ),
        ),
        (
            "change_fact_source",
            lambda payload, refs: payload["facts"][0].__setitem__(
                "source_locator", "changed locator"
            ),
        ),
        (
            "change_source_refs",
            lambda payload, refs: refs[0].__setitem__("raw_hash", "1" * 64),
        ),
    ),
)
def test_historical_basis_recovery_authenticates_the_complete_reviewed_evidence_contract(
    session, mutation, apply
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    payload = deepcopy(reviewed.payload)
    source_refs = deepcopy(reviewed.source_refs)
    apply(payload, source_refs)
    _durably_rewrite_artifact(
        session,
        reviewed,
        payload=payload,
        source_refs=source_refs,
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None
    assert CompanyResearchRepository(session).current_artifact(
        initialized.project.id, "research_gaps"
    ).id == gaps.id


def test_historical_basis_recovery_authenticates_reviewed_evidence_input_hash(
    session,
) -> None:
    initialized, reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    _durably_rewrite_artifact(session, reviewed, input_hash="1" * 64)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize(
    ("mutation", "apply"),
    (
        ("delete_gap", lambda payload, refs: payload["gaps"].pop()),
        (
            "change_gap",
            lambda payload, refs: payload["gaps"][0].__setitem__(
                "reason", "changed reason"
            ),
        ),
        (
            "change_source_refs",
            lambda payload, refs: refs[0].__setitem__("raw_hash", "2" * 64),
        ),
    ),
)
def test_historical_basis_recovery_authenticates_the_complete_gaps_contract(
    session, mutation, apply
) -> None:
    initialized, _reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    payload = deepcopy(gaps.payload)
    source_refs = deepcopy(gaps.source_refs)
    apply(payload, source_refs)
    _durably_rewrite_artifact(
        session,
        gaps,
        payload=payload,
        source_refs=source_refs,
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize(
    ("case", "mutate"),
    (
        (
            "unreviewed",
            lambda payload: payload["facts"][-1].pop("review_decision"),
        ),
        (
            "wrong_cutoff",
            lambda payload: payload.__setitem__(
                "cutoff", (CUTOFF + timedelta(seconds=1)).isoformat()
            ),
        ),
        (
            "wrong_fixture_hash",
            lambda payload: payload.__setitem__("fixture_content_hash", "0" * 64),
        ),
        (
            "wrong_company",
            lambda payload: payload.__setitem__(
                "company_external_key", "US:OTHER:COMPANY"
            ),
        ),
        (
            "wrong_securities",
            lambda payload: payload.__setitem__(
                "security_external_keys", ["NYSE:OTHER"]
            ),
        ),
    ),
)
def test_historical_basis_recovery_rejects_invalid_reviewed_evidence_without_requeue(
    session, case, mutate
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    invalid = _append_recovery_evidence(
        session, initialized, reviewed, mutate
    )
    repository = CompanyResearchRepository(session)
    events_before = repository.events(initialized.preparation.id)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    current_evidence = repository.current_artifact(
        initialized.project.id, "evidence_index"
    )
    current_gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    current_draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert preparation is not None and preparation.status == "blocked"
    assert job is not None and job.status == "failed"
    assert current_evidence is not None and current_evidence.id == invalid.id
    assert current_gaps is not None
    assert (current_gaps.id, current_gaps.content_hash) == (gaps.id, gaps.content_hash)
    assert current_draft is not None
    assert current_draft.lock_version == blocked_draft.lock_version
    assert current_draft.content.historical_basis_id is None
    assert repository.events(initialized.preparation.id) == events_before


@pytest.mark.parametrize(
    "patch",
    (
        WorkspaceDraftPatch(mandate_id=None),
        WorkspaceDraftPatch(scope_id=None),
        WorkspaceDraftPatch(agenda_id=None),
        WorkspaceDraftPatch(price_snapshot_ids=()),
        WorkspaceDraftPatch(fx_snapshot_ids=()),
        WorkspaceDraftPatch(capital_structure_snapshot_id=None),
        WorkspaceDraftPatch(security_rights_ids=()),
    ),
)
def test_historical_basis_recovery_rejects_each_incomplete_draft_reference(
    session, patch
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    drafts = WorkspaceDraftService(session, now=lambda: NOW + timedelta(seconds=1))
    incomplete = drafts.save(
        initialized.project.id,
        expected_lock_version=blocked_draft.lock_version,
        patch=patch,
    )
    repository = CompanyResearchRepository(session)
    events_before = repository.events(initialized.preparation.id)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    after = drafts.read(initialized.project.id)
    assert after is not None and after.lock_version == incomplete.lock_version
    assert after.content.historical_basis_id is None
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert repository.events(initialized.preparation.id) == events_before


def test_historical_basis_recovery_rejects_a_nonexistent_mandate_reference(
    session,
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    drafts = WorkspaceDraftService(session, now=lambda: NOW + timedelta(seconds=1))
    changed = drafts.save(
        initialized.project.id,
        expected_lock_version=blocked_draft.lock_version,
        patch=WorkspaceDraftPatch(mandate_id=uuid4()),
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    after = drafts.read(initialized.project.id)
    assert after is not None and after.lock_version == changed.lock_version
    assert after.content.historical_basis_id is None


def _stored_time_text(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _mandate_content_hash(
    row,
    *,
    horizon_years: int | None = None,
    effective_at=None,
    comparison_set=None,
) -> str:
    return canonical_hash(
        {
            "schema_version": "product.investment-mandate.v1",
            "project_id": str(row.project_id),
            "mandate_key": row.mandate_key,
            "horizon_years": (
                row.horizon_years if horizon_years is None else horizon_years
            ),
            "base_currency": row.base_currency,
            "required_return": format(row.required_return, ".8f"),
            "permanent_loss_limit": format(row.permanent_loss_limit, ".8f"),
            "comparison_set": (
                list(row.comparison_set)
                if comparison_set is None
                else comparison_set
            ),
            "benchmark_key": row.benchmark_key,
            "required_excess_return": (
                format(row.required_excess_return, ".8f")
                if row.required_excess_return is not None
                else None
            ),
            "effective_at": _stored_time_text(
                row.effective_at if effective_at is None else effective_at
            ),
            "expires_at": _stored_time_text(row.expires_at),
        }
    )


@pytest.mark.parametrize(
    ("direction", "recompute_hash"),
    (
        ("future", False),
        ("future", True),
        ("past", False),
        ("past", True),
    ),
)
def test_historical_basis_recovery_bounds_mandate_effective_at_by_lifecycle(
    session, direction, recompute_hash
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    mandate = ProductRepository(session).product_mandate(
        initialized.project.id, blocked_draft.content.mandate_id
    )
    project = session.get(UnderwritingResearchProject, initialized.project.id)
    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    assert mandate is not None and project is not None and preparation is not None
    anchor = preparation.created_at if direction == "future" else project.created_at
    wrong_effective_at = anchor + (
        timedelta(days=365) if direction == "future" else -timedelta(days=365)
    )
    content_hash = (
        _mandate_content_hash(mandate, effective_at=wrong_effective_at)
        if recompute_hash
        else mandate.content_hash
    )
    statement = text(
        "UPDATE uw_mandate_versions SET effective_at = :effective_at, "
        "content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "effective_at",
            type_=UnderwritingMandateVersion.__table__.c.effective_at.type,
        ),
        bindparam(
            "content_hash",
            type_=UnderwritingMandateVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=UnderwritingMandateVersion.__table__.c.id.type),
    )
    assert session.execute(
        statement,
        {
            "effective_at": wrong_effective_at,
            "content_hash": content_hash,
            "id": mandate.id,
        },
    ).rowcount == 1
    session.expire_all()

    with pytest.raises(ValidationError, match="recovery draft is incomplete"):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)


def test_historical_basis_recovery_anchors_legacy_request_to_initialized_event(
    session,
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    mandate = ProductRepository(session).product_mandate(
        initialized.project.id, blocked_draft.content.mandate_id
    )
    agenda = ProductRepository(session).agenda(
        initialized.project.id, blocked_draft.content.agenda_id
    )
    assert mandate is not None and agenda is not None
    wrong_cutoff = CUTOFF - timedelta(days=1)
    wrong_preview = build_alphabet_company_research_preview_at_cutoff(
        session,
        company_id=initialized.project.primary_company_id,
        cutoff_at=wrong_cutoff,
    )
    generator = deepcopy(agenda.generator_provenance)
    generator["input_summary_hash"] = wrong_preview.input_hash
    agenda_hash = research_agenda_content_hash(
        project_id=agenda.project_id,
        scope_id=agenda.scope_id,
        payload=agenda.payload,
        generator_provenance=generator,
    )
    session.execute(
        text(
            "UPDATE uw_company_research_preparations SET request_hash = :hash "
            "WHERE id = :id"
        ).bindparams(
            bindparam(
                "id", type_=CompanyResearchPreparation.__table__.c.id.type
            )
        ),
        {"hash": wrong_preview.input_hash, "id": initialized.preparation.id},
    )
    session.execute(
        text(
            "UPDATE uw_mandate_versions SET effective_at = :effective_at, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "effective_at",
                type_=UnderwritingMandateVersion.__table__.c.effective_at.type,
            ),
            bindparam("id", type_=UnderwritingMandateVersion.__table__.c.id.type),
        ),
        {
            "effective_at": wrong_cutoff,
            "content_hash": _mandate_content_hash(
                mandate, effective_at=wrong_cutoff
            ),
            "id": mandate.id,
        },
    )
    session.execute(
        text(
            "UPDATE uw_research_agenda_versions SET generator_provenance = :generator, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "generator",
                type_=UnderwritingResearchAgendaVersion.__table__.c.generator_provenance.type,
            ),
            bindparam(
                "id", type_=UnderwritingResearchAgendaVersion.__table__.c.id.type
            ),
        ),
        {"generator": generator, "content_hash": agenda_hash, "id": agenda.id},
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="recovery evidence is invalid"):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "malformed"])
def test_historical_basis_recovery_requires_one_authentic_initialized_event(
    session, mode: str
) -> None:
    initialized, _reviewed, _gaps, _blocked_draft = (
        _legacy_blocked_missing_basis(session)
    )
    repository = CompanyResearchRepository(session)
    if mode == "missing":
        session.connection().exec_driver_sql(
            "DELETE FROM uw_company_research_events WHERE preparation_id = ?",
            (initialized.preparation.id.hex,),
        )
    elif mode == "duplicate":
        repository.append_event(
            preparation_id=initialized.preparation.id,
            event_type="initialized",
            payload={"request_hash": initialized.preparation.request_hash},
            created_at=NOW + timedelta(seconds=2),
        )
    else:
        events = repository.events(initialized.preparation.id)
        previous_hash = None
        statement = text(
            "UPDATE uw_company_research_events SET previous_event_hash = :previous, "
            "payload = :payload, content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "previous",
                type_=CompanyResearchEvent.__table__.c.previous_event_hash.type,
            ),
            bindparam("payload", type_=CompanyResearchEvent.__table__.c.payload.type),
            bindparam("id", type_=CompanyResearchEvent.__table__.c.id.type),
        )
        for event in events:
            payload = (
                {"request_hash": []} if event.sequence == 1 else event.payload
            )
            content_hash = repository.event_content_hash_v2(
                preparation_id=event.preparation_id,
                sequence=event.sequence,
                previous_event_hash=previous_hash,
                event_type=event.event_type,
                payload=payload,
                created_at=event.created_at,
            )
            session.execute(
                statement,
                {
                    "previous": previous_hash,
                    "payload": payload,
                    "content_hash": content_hash,
                    "id": event.id,
                },
            )
            previous_hash = content_hash
    session.expire_all()

    with pytest.raises(ValidationError, match="recovery evidence is invalid"):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=3)
        ).recover(initialized.preparation.id)


@pytest.mark.parametrize(
    ("kind", "malformed"),
    (
        ("mandate_comparison_set", 7),
        ("scope_covered_segments", 7),
        ("scope_target_security_ids", {"unexpected": "mapping"}),
        ("agenda_items", 7),
        ("agenda_generator", ["unexpected"]),
    ),
)
def test_historical_basis_recovery_bounds_malformed_foundation_json(
    session, kind, malformed
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = ProductRepository(session)
    if kind == "mandate_comparison_set":
        row = repository.product_mandate(
            initialized.project.id, blocked_draft.content.mandate_id
        )
        assert row is not None
        content_hash = _mandate_content_hash(row, comparison_set=malformed)
        statement = text(
            "UPDATE uw_mandate_versions SET comparison_set = :malformed, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "malformed",
                type_=UnderwritingMandateVersion.__table__.c.comparison_set.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingMandateVersion.__table__.c.content_hash.type,
            ),
            bindparam("id", type_=UnderwritingMandateVersion.__table__.c.id.type),
        )
        values = {"malformed": malformed, "content_hash": content_hash, "id": row.id}
    elif kind.startswith("scope_"):
        row = repository.scope(initialized.project.id, blocked_draft.content.scope_id)
        assert row is not None
        payload = deepcopy(row.payload)
        payload[kind.removeprefix("scope_")] = malformed
        content_hash = canonical_hash(
            {
                "schema_version": "product.research-scope.v1",
                "project_id": str(row.project_id),
                "scope": payload,
            }
        )
        statement = text(
            "UPDATE uw_research_scope_versions SET payload = :malformed, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "malformed",
                type_=UnderwritingResearchScopeVersion.__table__.c.payload.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingResearchScopeVersion.__table__.c.content_hash.type,
            ),
            bindparam(
                "id", type_=UnderwritingResearchScopeVersion.__table__.c.id.type
            ),
        )
        values = {"malformed": payload, "content_hash": content_hash, "id": row.id}
    else:
        row = repository.agenda(
            initialized.project.id, blocked_draft.content.agenda_id
        )
        assert row is not None
        payload = deepcopy(row.payload)
        generator = deepcopy(row.generator_provenance)
        if kind == "agenda_items":
            payload["items"] = malformed
        else:
            generator = malformed
        content_hash = canonical_hash(
            {
                "schema_version": "product.research-agenda.v1",
                "project_id": str(row.project_id),
                "scope_id": str(row.scope_id),
                "items": payload["items"],
                "generator": generator,
            }
        )
        statement = text(
            "UPDATE uw_research_agenda_versions SET payload = :payload, "
            "generator_provenance = :generator, content_hash = :content_hash "
            "WHERE id = :id"
        ).bindparams(
            bindparam(
                "payload",
                type_=UnderwritingResearchAgendaVersion.__table__.c.payload.type,
            ),
            bindparam(
                "generator",
                type_=UnderwritingResearchAgendaVersion.__table__.c.generator_provenance.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingResearchAgendaVersion.__table__.c.content_hash.type,
            ),
            bindparam(
                "id", type_=UnderwritingResearchAgendaVersion.__table__.c.id.type
            ),
        )
        values = {
            "payload": payload,
            "generator": generator,
            "content_hash": content_hash,
            "id": row.id,
        }
    assert session.execute(statement, values).rowcount == 1
    session.expire_all()

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)


@pytest.mark.parametrize(
    ("kind", "recompute_hash"),
    (
        ("mandate", False),
        ("mandate", True),
        ("scope", False),
        ("scope", True),
        ("agenda", False),
        ("agenda", True),
    ),
)
def test_historical_basis_recovery_authenticates_the_foundation_contract(
    session, kind, recompute_hash
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = ProductRepository(session)
    if kind == "mandate":
        row = repository.product_mandate(
            initialized.project.id, blocked_draft.content.mandate_id
        )
        assert row is not None
        values = {"horizon_years": 4}
        values["content_hash"] = (
            _mandate_content_hash(row, horizon_years=4)
            if recompute_hash
            else row.content_hash
        )
        statement = text(
            "UPDATE uw_mandate_versions SET horizon_years = :horizon_years, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "horizon_years",
                type_=UnderwritingMandateVersion.__table__.c.horizon_years.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingMandateVersion.__table__.c.content_hash.type,
            ),
            bindparam("id", type_=UnderwritingMandateVersion.__table__.c.id.type),
        )
    elif kind == "scope":
        row = repository.scope(initialized.project.id, blocked_draft.content.scope_id)
        assert row is not None
        payload = deepcopy(row.payload)
        payload["covered_segments"] = ["tampered-segment"]
        values = {"payload": payload}
        values["content_hash"] = (
            canonical_hash(
                {
                    "schema_version": "product.research-scope.v1",
                    "project_id": str(row.project_id),
                    "scope": payload,
                }
            )
            if recompute_hash
            else row.content_hash
        )
        statement = text(
            "UPDATE uw_research_scope_versions SET payload = :payload, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "payload",
                type_=UnderwritingResearchScopeVersion.__table__.c.payload.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingResearchScopeVersion.__table__.c.content_hash.type,
            ),
            bindparam(
                "id", type_=UnderwritingResearchScopeVersion.__table__.c.id.type
            ),
        )
    else:
        row = repository.agenda(
            initialized.project.id, blocked_draft.content.agenda_id
        )
        assert row is not None
        payload = deepcopy(row.payload)
        payload["items"] = [*payload["items"], "tampered_module"]
        values = {"payload": payload}
        values["content_hash"] = (
            canonical_hash(
                {
                    "schema_version": "product.research-agenda.v1",
                    "project_id": str(row.project_id),
                    "scope_id": str(row.scope_id),
                    "items": payload["items"],
                    "generator": row.generator_provenance,
                }
            )
            if recompute_hash
            else row.content_hash
        )
        statement = text(
            "UPDATE uw_research_agenda_versions SET payload = :payload, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "payload",
                type_=UnderwritingResearchAgendaVersion.__table__.c.payload.type,
            ),
            bindparam(
                "content_hash",
                type_=UnderwritingResearchAgendaVersion.__table__.c.content_hash.type,
            ),
            bindparam(
                "id", type_=UnderwritingResearchAgendaVersion.__table__.c.id.type
            ),
        )
    values["id"] = row.id
    assert session.execute(statement, values).rowcount == 1
    session.expire_all()

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None


@pytest.mark.parametrize("field", ("scope_id", "agenda_id"))
def test_historical_basis_recovery_rejects_a_foreign_foundation_reference(
    session, field
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    project_service = ResearchProjectService(session, now=lambda: NOW)
    foreign_project = project_service.create_project(
        initialized.project.primary_company_id,
        initialized.project.target_security_ids,
    )
    repository = ProductRepository(session)
    original_scope = repository.scope(
        initialized.project.id, blocked_draft.content.scope_id
    )
    original_agenda = repository.agenda(
        initialized.project.id, blocked_draft.content.agenda_id
    )
    assert original_scope is not None and original_agenda is not None
    foreign_scope = repository.append_scope(
        project_id=foreign_project.id,
        payload=deepcopy(original_scope.payload),
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    foreign_agenda = repository.append_agenda(
        project_id=foreign_project.id,
        scope_id=foreign_scope.id,
        payload=deepcopy(original_agenda.payload),
        generator_provenance=deepcopy(original_agenda.generator_provenance),
        content_hash="b" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    foreign_id = foreign_scope.id if field == "scope_id" else foreign_agenda.id
    drafts = WorkspaceDraftService(session, now=lambda: NOW + timedelta(seconds=1))
    changed = drafts.save(
        initialized.project.id,
        expected_lock_version=blocked_draft.lock_version,
        patch=WorkspaceDraftPatch(**{field: foreign_id}),
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    after = drafts.read(initialized.project.id)
    assert after is not None and after.lock_version == changed.lock_version
    assert after.content.historical_basis_id is None


@pytest.mark.parametrize(
    "field",
    (
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "capital_structure_snapshot_id",
        "security_rights_ids",
    ),
)
def test_historical_basis_recovery_rejects_an_existing_wrong_market_reference(
    session, field
) -> None:
    initialized, _reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    content = blocked_draft.content
    foreign_company = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "CN:300750:COMPANY"
        )
    )
    foreign_security = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "SZSE:300750"
        )
    )
    assert foreign_company is not None and foreign_security is not None
    if field == "price_snapshot_ids":
        original = session.get(UnderwritingPriceSnapshot, content.price_snapshot_ids[0])
        assert original is not None
        wrong = UnderwritingPriceSnapshot(
            security_identity_id=foreign_security.id,
            price=original.price,
            currency=original.currency,
            price_type=original.price_type,
            adjustment_basis=original.adjustment_basis,
            market_at=original.market_at,
            available_at=original.available_at,
            source_id="test:foreign-price",
            raw_hash="8" * 64,
            content_hash="0" * 64,
            created_at=NOW,
        )
        wrong.content_hash = price_snapshot_hash(wrong)
    elif field == "fx_snapshot_ids":
        original = session.get(UnderwritingFXSnapshot, content.fx_snapshot_ids[0])
        assert original is not None
        wrong = UnderwritingFXSnapshot(
            base_currency=original.quote_currency,
            quote_currency=original.base_currency,
            rate=original.rate,
            quote_direction=original.quote_direction,
            market_at=original.market_at,
            available_at=original.available_at,
            source_id="test:wrong-fx-pair",
            raw_hash="8" * 64,
            content_hash="0" * 64,
            created_at=NOW,
        )
        wrong.content_hash = fx_snapshot_hash(wrong)
    elif field == "capital_structure_snapshot_id":
        original = session.get(
            UnderwritingCapitalStructureSnapshot,
            content.capital_structure_snapshot_id,
        )
        assert original is not None
        wrong = UnderwritingCapitalStructureSnapshot(
            company_id=foreign_company.id,
            currency=original.currency,
            cash=original.cash,
            debt=original.debt,
            minority_interest=original.minority_interest,
            investments=original.investments,
            pension_liabilities=original.pension_liabilities,
            other_adjustments=original.other_adjustments,
            basic_shares=original.basic_shares,
            diluted_shares=original.diluted_shares,
            potential_dilution_descriptors=deepcopy(
                original.potential_dilution_descriptors
            ),
            report_period_start=original.report_period_start,
            report_period_end=original.report_period_end,
            market_at=original.market_at,
            available_at=original.available_at,
            source_id="test:foreign-capital",
            raw_hash="8" * 64,
            content_hash="0" * 64,
            created_at=NOW,
        )
        wrong.content_hash = capital_structure_snapshot_hash(wrong)
    else:
        wrong = session.scalar(
            select(UnderwritingSecurityRightsVersion).where(
                UnderwritingSecurityRightsVersion.security_identity_id
                == foreign_security.id
            )
        )
        assert wrong is not None
    if field != "security_rights_ids":
        session.add(wrong)
        session.flush()
    if field == "capital_structure_snapshot_id":
        value = wrong.id
    else:
        current_ids = getattr(content, field)
        value = tuple(sorted((*current_ids, wrong.id), key=str))
    drafts = WorkspaceDraftService(session, now=lambda: NOW + timedelta(seconds=1))
    changed = drafts.save(
        initialized.project.id,
        expected_lock_version=blocked_draft.lock_version,
        patch=WorkspaceDraftPatch(**{field: value}),
    )

    with pytest.raises(
        ValidationError,
        match="historical basis recovery draft is incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    after = drafts.read(initialized.project.id)
    assert after is not None and after.lock_version == changed.lock_version
    assert after.content.historical_basis_id is None


def test_historical_basis_recovery_rejects_a_durably_changed_cached_security(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-stale-identity.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as bootstrap:
        initialized, _reviewed, _gaps, _draft = _legacy_blocked_missing_basis(
            bootstrap
        )
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        security_id = initialized.project.target_security_ids[0]
        bootstrap.commit()

    with sessions() as cached, sessions() as mutator:
        repository = ProductRepository(cached)
        project = repository.project(project_id)
        assert project is not None
        assert repository.object(project[0].primary_company_id) is not None
        security = repository.object(security_id)
        assert security is not None and security.external_key in {
            "NASDAQ:GOOG",
            "NASDAQ:GOOGL",
        }
        assert CompanyResearchRepository(cached).current_artifact(
            project_id, "evidence_index"
        ) is not None
        assert WorkspaceDraftService(cached, now=lambda: NOW).read(
            project_id
        ) is not None

        update_security = text(
            "UPDATE uw_research_objects SET external_key = :external_key "
            "WHERE id = :security_id"
        ).bindparams(
            bindparam(
                "security_id", type_=UnderwritingResearchObject.__table__.c.id.type
            )
        )
        assert mutator.execute(
            update_security,
            {"security_id": security_id, "external_key": "US:ALTERED:SECURITY"},
        ).rowcount == 1
        mutator.commit()

        with pytest.raises(
            ValidationError,
            match="historical basis recovery project identity is invalid",
        ):
            CompanyResearchHistoricalBasisRecovery(
                cached, now=lambda: NOW + timedelta(seconds=2)
            ).recover(preparation_id)
        cached.rollback()

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("target", "error"),
    (
        ("project", "project identity is invalid"),
        ("company", "project identity is invalid"),
        ("artifact", "evidence is invalid"),
        ("draft", "draft is incomplete"),
    ),
)
def test_historical_basis_recovery_refreshes_every_cached_authority(
    tmp_path, target, error
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'company-research-stale-{target}.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as bootstrap:
        initialized, reviewed, _gaps, blocked_draft = _legacy_blocked_missing_basis(
            bootstrap
        )
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        company_id = initialized.project.primary_company_id
        security_id = initialized.project.target_security_ids[0]
        evidence_id = reviewed.id
        draft_id = blocked_draft.id
        foreign_company_id = bootstrap.scalar(
            select(UnderwritingResearchObject.id).where(
                UnderwritingResearchObject.external_key == "CN:300750:COMPANY"
            )
        )
        assert foreign_company_id is not None
        bootstrap.commit()

    with sessions() as cached, sessions() as mutator:
        products = ProductRepository(cached)
        assert products.project(project_id) is not None
        assert products.object(company_id) is not None
        assert products.object(security_id) is not None
        assert CompanyResearchRepository(cached).current_artifact(
            project_id, "evidence_index"
        ) is not None
        assert WorkspaceDraftService(cached, now=lambda: NOW).read(
            project_id
        ) is not None

        if target == "project":
            statement = text(
                "UPDATE uw_research_projects SET primary_company_id = :company_id "
                "WHERE id = :project_id"
            ).bindparams(
                bindparam(
                    "company_id",
                    type_=UnderwritingResearchProject.__table__.c.primary_company_id.type,
                ),
                bindparam(
                    "project_id", type_=UnderwritingResearchProject.__table__.c.id.type
                ),
            )
            assert mutator.execute(
                statement,
                {"company_id": foreign_company_id, "project_id": project_id},
            ).rowcount == 1
        elif target == "company":
            statement = text(
                "UPDATE uw_research_objects SET external_key = :external_key "
                "WHERE id = :company_id"
            ).bindparams(
                bindparam(
                    "company_id", type_=UnderwritingResearchObject.__table__.c.id.type
                )
            )
            assert mutator.execute(
                statement,
                {"company_id": company_id, "external_key": "US:ALTERED:COMPANY"},
            ).rowcount == 1
        elif target == "artifact":
            row = mutator.get(CompanyResearchArtifactVersion, evidence_id)
            assert row is not None
            payload = deepcopy(row.payload)
            payload["company_external_key"] = "US:ALTERED:COMPANY"
            _durably_rewrite_artifact(mutator, row, payload=payload)
        else:
            row = mutator.get(UnderwritingWorkspaceDraft, draft_id)
            assert row is not None
            content = deepcopy(row.content)
            content["mandate_id"] = str(uuid4())
            statement = text(
                "UPDATE uw_workspace_drafts SET content = :content WHERE id = :id"
            ).bindparams(
                bindparam(
                    "content", type_=UnderwritingWorkspaceDraft.__table__.c.content.type
                ),
                bindparam("id", type_=UnderwritingWorkspaceDraft.__table__.c.id.type),
            )
            assert mutator.execute(
                statement, {"content": content, "id": draft_id}
            ).rowcount == 1
        mutator.commit()

        with pytest.raises(ValidationError, match=error):
            CompanyResearchHistoricalBasisRecovery(
                cached, now=lambda: NOW + timedelta(seconds=2)
            ).recover(preparation_id)
        cached.rollback()

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_historical_basis_recovery_rejects_an_already_bound_conflicting_basis(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    boundary = resolve_alphabet_company_research_boundary(CUTOFF)
    conflicting = ResearchProjectService(
        session, now=lambda: NOW + timedelta(seconds=1)
    ).create_historical_basis(
        ProductHistoricalBasisInput(
            cutoff_at=boundary.cutoff_at,
            source_manifest_hash=boundary.basis_input.source_manifest_hash,
            definition_bundle_hash="d" * 64,
            parser_bundle_hash=boundary.basis_input.parser_bundle_hash,
        )
    )
    drafts = WorkspaceDraftService(session, now=lambda: NOW + timedelta(seconds=1))
    bound = drafts.save(
        initialized.project.id,
        expected_lock_version=blocked_draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=conflicting.id),
    )
    repository = CompanyResearchRepository(session)
    events_before = repository.events(initialized.preparation.id)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery found a conflicting basis",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    assert drafts.read(initialized.project.id).lock_version == bound.lock_version
    assert repository.events(initialized.preparation.id) == events_before
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id


def test_historical_basis_recovery_rejects_a_wrong_project_security_identity(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    security = session.get(
        UnderwritingResearchObject, initialized.project.target_security_ids[0]
    )
    assert security is not None
    identity_tamper = text(
        "UPDATE uw_research_objects SET external_key = :external_key WHERE id = :id"
    ).bindparams(
        bindparam(
            "external_key",
            type_=UnderwritingResearchObject.__table__.c.external_key.type,
        ),
        bindparam("id", type_=UnderwritingResearchObject.__table__.c.id.type),
    )
    assert session.connection().execute(
        identity_tamper,
        {"external_key": "NYSE:OTHER", "id": security.id},
    ).rowcount == 1
    session.expire(security)
    repository = CompanyResearchRepository(session)
    events_before = repository.events(initialized.preparation.id)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery project identity is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert preparation is not None and preparation.status == "blocked"
    assert job is not None and job.status == "failed"
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert repository.events(initialized.preparation.id) == events_before


@pytest.mark.parametrize("recomputed_wrong_membership", [False, True])
def test_historical_basis_recovery_authenticates_project_membership_hashes(
    session, recomputed_wrong_membership: bool
) -> None:
    initialized, _reviewed, _gaps, _blocked_draft = (
        _legacy_blocked_missing_basis(session)
    )
    membership = session.scalar(
        select(UnderwritingResearchProjectSecurity)
        .where(
            UnderwritingResearchProjectSecurity.project_id == initialized.project.id
        )
        .limit(1)
    )
    assert membership is not None
    wrong_hash = (
        research_project_security_content_hash(
            project_id=initialized.project.id,
            security_id=uuid4(),
        )
        if recomputed_wrong_membership
        else "f" * 64
    )
    statement = text(
        "UPDATE uw_research_project_securities "
        "SET content_hash = :content_hash WHERE project_id = :project_id "
        "AND security_id = :security_id"
    ).bindparams(
        bindparam(
            "project_id",
            type_=UnderwritingResearchProjectSecurity.__table__.c.project_id.type,
        ),
        bindparam(
            "security_id",
            type_=UnderwritingResearchProjectSecurity.__table__.c.security_id.type,
        ),
    )
    assert session.execute(
        statement,
        {
            "content_hash": wrong_hash,
            "project_id": membership.project_id,
            "security_id": membership.security_id,
        },
    ).rowcount == 1
    session.expire_all()

    with pytest.raises(
        ValidationError,
        match="historical basis recovery project identity is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)


def test_historical_basis_recovery_locks_project_before_mutable_ownership_rows(
    session, monkeypatch
) -> None:
    initialized, _reviewed, _gaps, _blocked_draft = (
        _legacy_blocked_missing_basis(session)
    )
    repository = CompanyResearchRepository(session)
    calls: list[str] = []
    for name, label in (
        ("_project_for_update", "project"),
        ("_preparation_for_update", "preparation"),
        ("_job_for_update", "job"),
        ("_workspace_draft_for_update", "draft"),
    ):
        original = getattr(repository, name)

        def wrapped(*args, _original=original, _label=label, **kwargs):
            calls.append(_label)
            return _original(*args, **kwargs)

        monkeypatch.setattr(repository, name, wrapped)

    repository.lock_basis_recovery_state(initialized.preparation.id)

    assert calls[:4] == ["project", "preparation", "job", "draft"]


def test_worker_claim_boundary_locks_project_before_preparation_and_job(
    session, monkeypatch
) -> None:
    initialized = _initialized(session, idempotency_key="worker-lock-order")
    repository = CompanyResearchRepository(session)
    calls: list[str] = []
    for name, label in (
        ("_project_for_update", "project"),
        ("_preparation_for_update", "preparation"),
        ("_job_for_update", "job"),
    ):
        original = getattr(repository, name)

        def wrapped(*args, _original=original, _label=label, **kwargs):
            calls.append(_label)
            return _original(*args, **kwargs)

        monkeypatch.setattr(repository, name, wrapped)

    locked = repository.lock_worker_claim_state(
        preparation_id=initialized.preparation.id,
        job_id=initialized.job.id,
    )

    assert locked is not None
    assert calls == ["project", "preparation", "job"]


def test_claim_next_skips_a_locked_candidate_and_claims_the_next_job(
    session, monkeypatch
) -> None:
    first = _initialized(session, idempotency_key="claim-skip-first")
    first.preparation.request_hash = "f" * 64
    session.flush()
    second = _initialized(session, idempotency_key="claim-skip-second")
    ordered = tuple(
        session.execute(
            select(Job.id, CompanyResearchPreparation.id)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                CompanyResearchPreparation.id.in_(
                    (first.preparation.id, second.preparation.id)
                )
            )
            .order_by(Job.created_at, Job.id)
        )
    )
    assert len(ordered) == 2
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    real_lock = worker._repository.lock_worker_claim_state
    calls = []

    def skip_first(*, preparation_id, job_id, **kwargs):
        calls.append((job_id, preparation_id, kwargs))
        if preparation_id == ordered[0][1]:
            return None
        return real_lock(
            preparation_id=preparation_id, job_id=job_id, **kwargs
        )

    monkeypatch.setattr(
        worker._repository, "lock_worker_claim_state", skip_first
    )

    claim = worker.claim_next()

    assert claim is not None
    assert (claim.job_id, claim.preparation_id) == ordered[1]
    assert [call[:2] for call in calls] == list(ordered)
    assert all(call[2] == {"skip_locked": True} for call in calls)


def test_claim_next_keyset_pages_beyond_a_fully_locked_first_batch(
    session, monkeypatch
) -> None:
    from app.underwriting.services import company_research_preparation as module

    initialized = []
    for index in range(3):
        if initialized:
            initialized[-1].preparation.request_hash = f"{index:064x}"
            session.flush()
        initialized.append(
            _initialized(session, idempotency_key=f"claim-page-{index}")
        )
    ordered = tuple(
        session.execute(
            select(Job.id, CompanyResearchPreparation.id)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                CompanyResearchPreparation.id.in_(
                    tuple(item.preparation.id for item in initialized)
                )
            )
            .order_by(Job.created_at, Job.id)
        )
    )
    monkeypatch.setattr(module, "_WORKER_CANDIDATE_PAGE_SIZE", 2)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    real_lock = worker._repository.lock_worker_claim_state
    calls = []

    def skip_first_page(*, preparation_id, job_id, **kwargs):
        calls.append((job_id, preparation_id, kwargs))
        if (job_id, preparation_id) in ordered[:2]:
            return None
        return real_lock(
            preparation_id=preparation_id, job_id=job_id, **kwargs
        )

    monkeypatch.setattr(
        worker._repository, "lock_worker_claim_state", skip_first_page
    )
    candidate_sql: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            "SELECT jobs.id" in statement
            and "uw_company_research_preparations.id" in statement
            and "jobs.created_at" in statement
        ):
            candidate_sql.append(statement)

    sa_event.listen(session.bind, "before_cursor_execute", capture)
    try:
        claim = worker.claim_next()
    finally:
        sa_event.remove(session.bind, "before_cursor_execute", capture)

    assert claim is not None
    assert (claim.job_id, claim.preparation_id) == ordered[2]
    assert [call[:2] for call in calls] == list(ordered)
    assert len(candidate_sql) == 2
    assert all("LIMIT" in statement for statement in candidate_sql)
    assert "jobs.created_at >" in candidate_sql[1]


def test_retry_boundary_locks_project_before_preparation_and_job(
    session, monkeypatch
) -> None:
    initialized = _initialized(session, idempotency_key="retry-lock-order")
    initialized.preparation.status = "recoverable_failure"
    initialized.preparation.last_error_code = "source_unavailable"
    initialized.job.status = "failed"
    initialized.job.error = "source_unavailable"
    session.flush()
    repository = CompanyResearchRepository(session)
    calls: list[str] = []
    for name, label in (
        ("_project_for_update", "project"),
        ("_preparation_for_update", "preparation"),
        ("_job_for_update", "job"),
    ):
        original = getattr(repository, name)

        def wrapped(*args, _original=original, _label=label, **kwargs):
            calls.append(_label)
            return _original(*args, **kwargs)

        monkeypatch.setattr(repository, name, wrapped)

    queued = repository.requeue_recoverable_preparation(
        initialized.preparation.id, updated_at=NOW
    )

    assert queued.status == "queued"
    assert calls == ["project", "preparation", "job"]


@pytest.mark.parametrize("operation", ("recover_stale", "cancel_queued"))
def test_worker_maintenance_transitions_use_project_first_ownership_boundary(
    session, monkeypatch, operation: str
) -> None:
    initialized = _initialized(
        session, idempotency_key=f"maintenance-lock-{operation}"
    )
    preparation = initialized.preparation
    job = initialized.job
    if operation == "recover_stale":
        preparation.status = "preparing_sources"
        preparation.progress = 5
        job.status = "running"
        job.step = "evidence_index"
        job.started_at = NOW - timedelta(hours=1)
        job.claim_token = "stale-claim"
    else:
        job.cancel_requested = True
    session.flush()
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    real_lock = worker._repository.lock_worker_claim_state
    calls = []

    def record_lock(*, preparation_id, job_id, **kwargs):
        calls.append((preparation_id, job_id, kwargs))
        return real_lock(
            preparation_id=preparation_id, job_id=job_id, **kwargs
        )

    monkeypatch.setattr(
        worker._repository, "lock_worker_claim_state", record_lock
    )

    changed = (
        worker.recover_stale_claims(before=NOW)
        if operation == "recover_stale"
        else worker.cancel_queued_claims()
    )

    assert changed == 1
    assert calls == [
        (preparation.id, job.id, {"skip_locked": True})
    ]


@pytest.mark.parametrize("operation", ("recover_stale", "cancel_queued"))
def test_worker_maintenance_keyset_pages_beyond_a_fully_locked_first_batch(
    session, monkeypatch, operation: str
) -> None:
    from app.underwriting.services import company_research_preparation as module

    initialized = []
    for index in range(3):
        if initialized:
            initialized[-1].preparation.request_hash = f"{index + 10:064x}"
            session.flush()
        item = _initialized(
            session, idempotency_key=f"maintenance-page-{operation}-{index}"
        )
        if operation == "recover_stale":
            item.preparation.status = "preparing_sources"
            item.preparation.progress = 5
            item.job.status = "running"
            item.job.step = "evidence_index"
            item.job.started_at = NOW - timedelta(hours=1)
            item.job.claim_token = f"stale-{index}"
        else:
            item.job.cancel_requested = True
        initialized.append(item)
        session.flush()
    ordered = tuple(
        session.execute(
            select(Job.id, CompanyResearchPreparation.id)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                CompanyResearchPreparation.id.in_(
                    tuple(item.preparation.id for item in initialized)
                )
            )
            .order_by(Job.created_at, Job.id)
        )
    )
    monkeypatch.setattr(module, "_WORKER_CANDIDATE_PAGE_SIZE", 2)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    real_lock = worker._repository.lock_worker_claim_state
    calls = []

    def skip_first_page(*, preparation_id, job_id, **kwargs):
        calls.append((job_id, preparation_id, kwargs))
        if (job_id, preparation_id) in ordered[:2]:
            return None
        return real_lock(
            preparation_id=preparation_id, job_id=job_id, **kwargs
        )

    monkeypatch.setattr(
        worker._repository, "lock_worker_claim_state", skip_first_page
    )
    candidate_sql: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            "SELECT jobs.id" in statement
            and "uw_company_research_preparations.id" in statement
            and "jobs.created_at" in statement
        ):
            candidate_sql.append(statement)

    sa_event.listen(session.bind, "before_cursor_execute", capture)
    try:
        changed = (
            worker.recover_stale_claims(before=NOW)
            if operation == "recover_stale"
            else worker.cancel_queued_claims()
        )
    finally:
        sa_event.remove(session.bind, "before_cursor_execute", capture)

    assert changed == 1
    assert [call[:2] for call in calls] == list(ordered)
    assert len(candidate_sql) == 2
    assert all("LIMIT" in statement for statement in candidate_sql)
    assert "jobs.created_at >" in candidate_sql[1]


@pytest.mark.parametrize("operation", ("recover_stale", "cancel_queued"))
def test_worker_maintenance_caps_transitions_and_next_poll_progresses(
    session, monkeypatch, operation: str
) -> None:
    from app.underwriting.services import company_research_preparation as module

    initialized = []
    for index in range(3):
        if initialized:
            initialized[-1].preparation.request_hash = f"{index + 20:064x}"
            session.flush()
        item = _initialized(
            session, idempotency_key=f"maintenance-budget-{operation}-{index}"
        )
        if operation == "recover_stale":
            item.preparation.status = "preparing_sources"
            item.preparation.progress = 5
            item.job.status = "running"
            item.job.step = "evidence_index"
            item.job.started_at = NOW - timedelta(hours=1)
            item.job.claim_token = f"stale-budget-{index}"
        else:
            item.job.cancel_requested = True
        initialized.append(item)
        session.flush()
    monkeypatch.setattr(module, "_WORKER_CANDIDATE_PAGE_SIZE", 2)
    monkeypatch.setattr(module, "_WORKER_MAX_CANDIDATE_PAGES", 2)
    monkeypatch.setattr(module, "_WORKER_MAX_MAINTENANCE_TRANSITIONS", 1)
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)

    first = (
        worker.recover_stale_claims(before=NOW)
        if operation == "recover_stale"
        else worker.cancel_queued_claims()
    )
    second = (
        worker.recover_stale_claims(before=NOW)
        if operation == "recover_stale"
        else worker.cancel_queued_claims()
    )

    assert (first, second) == (1, 1)
    terminal = "queued" if operation == "recover_stale" else "cancelled"
    assert sum(item.job.status == terminal for item in initialized) == 2


def test_worker_run_once_claims_after_bounded_maintenance(
    session, monkeypatch
) -> None:
    from app.underwriting.services import company_research_preparation as module

    initialized = []
    for index in range(2):
        if initialized:
            initialized[-1].preparation.request_hash = f"{index + 30:064x}"
            session.flush()
        item = _initialized(
            session, idempotency_key=f"run-once-maintenance-budget-{index}"
        )
        item.preparation.status = "preparing_sources"
        item.preparation.progress = 5
        item.job.status = "running"
        item.job.step = "evidence_index"
        item.job.started_at = NOW - timedelta(hours=1)
        item.job.claim_token = f"run-once-stale-{index}"
        initialized.append(item)
        session.flush()
    session.commit()
    monkeypatch.setattr(module, "_WORKER_MAX_MAINTENANCE_TRANSITIONS", 1)
    monkeypatch.setattr(run_company_research_worker, "_utcnow", lambda: NOW)
    claims = []
    monkeypatch.setattr(
        CompanyResearchPreparationWorker,
        "run_claim",
        lambda _worker, claim: claims.append(claim),
    )
    sessions = sessionmaker(bind=session.get_bind(), future=True)

    assert run_company_research_worker.run_once(
        session_factory=sessions, recover_after_minutes=30
    )

    assert len(claims) == 1


def test_historical_basis_recovery_rejects_a_wrong_gaps_fixture_hash(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = CompanyResearchRepository(session)
    payload = deepcopy(gaps.payload)
    payload["fixture_content_hash"] = "0" * 64
    invalid_content_hash = repository.artifact_content_hash(
        project_id=initialized.project.id,
        kind="research_gaps",
        version=gaps.version,
        supersedes_id=gaps.supersedes_id,
        parent_content_hash=gaps.parent_content_hash,
        input_hash=gaps.input_hash,
        payload=payload,
        source_refs=gaps.source_refs,
    )
    gaps_tamper = text(
        "UPDATE uw_company_research_artifact_versions "
        "SET payload = :payload, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "payload",
            type_=CompanyResearchArtifactVersion.__table__.c.payload.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam(
            "id", type_=CompanyResearchArtifactVersion.__table__.c.id.type
        ),
    )
    assert session.connection().execute(
        gaps_tamper,
        {
            "payload": payload,
            "content_hash": invalid_content_hash,
            "id": gaps.id,
        },
    ).rowcount == 1
    session.expire(gaps)
    events_before = repository.events(initialized.preparation.id)

    with pytest.raises(
        ValidationError,
        match="historical basis recovery evidence is invalid",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=2)
        ).recover(initialized.preparation.id)

    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert preparation is not None and preparation.status == "blocked"
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert repository.events(initialized.preparation.id) == events_before


def test_rehashed_evidence_decision_must_match_review_audit_event(session) -> None:
    initialized, reviewed, _gaps, _blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    _durably_rewrite_first_review_decision(session, reviewed, "rejected")

    with pytest.raises(ValidationError, match="evidence audit history is invalid"):
        CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
            project_id=initialized.project.id
        )
    with pytest.raises(ValidationError, match="recovery evidence is invalid"):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)


@pytest.mark.parametrize(
    "mode",
    (
        "deleted",
        "duplicate",
        "reordered",
        "wrong_artifact",
        "wrong_fact",
        "wrong_decision",
        "timestamp_mismatch",
    ),
)
def test_evidence_review_audit_reconciliation_fails_closed(
    session, mode: str
) -> None:
    initialized, _reviewed, _gaps, _blocked_draft = (
        _legacy_blocked_missing_basis(session)
    )
    if mode == "deleted":
        session.connection().exec_driver_sql(
            "DELETE FROM uw_company_research_events "
            "WHERE preparation_id = ? AND sequence > 1",
            (initialized.preparation.id.hex,),
        )
        session.expire_all()
    else:
        _durably_rewrite_review_events(
            session, initialized.preparation.id, mode
        )

    with pytest.raises(ValidationError, match="evidence audit history is invalid"):
        CompanyResearchWorkbench(session, now=lambda: NOW).workspace(
            project_id=initialized.project.id
        )
    with pytest.raises(ValidationError, match="recovery evidence is invalid"):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("status", "recoverable_failure"),
        ("current_step", "research_gaps"),
        ("progress", 25),
        ("last_error_code", "provider_unavailable"),
    ),
)
def test_historical_basis_recovery_requires_the_exact_blocked_preparation_state(
    session, field, value
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    assert preparation is not None
    setattr(preparation, field, value)
    session.flush()

    with pytest.raises(
        ValidationError,
        match="preparation is not eligible for basis recovery",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)

    repository = CompanyResearchRepository(session)
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None and draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert all(
        event.event_type != "historical_basis_recovered"
        for event in repository.events(initialized.preparation.id)
    )


def test_historical_basis_recovery_requires_a_complete_locked_snapshot(session) -> None:
    initialized = _initialized(session)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    preparation.status = "blocked"
    preparation.current_step = "model_bundle"
    preparation.progress = 30
    preparation.last_error_code = "validation_failed"
    job.status = "failed"
    job.step = "model_bundle"
    job.error = "validation_failed"
    session.flush()

    with pytest.raises(
        ValidationError,
        match="historical basis recovery inputs are incomplete",
    ):
        CompanyResearchHistoricalBasisRecovery(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).recover(initialized.preparation.id)


def test_historical_basis_recovery_is_idempotent_for_the_exact_recovered_basis(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = CompanyResearchRepository(session)
    basis_count_before = session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    )
    recovery = CompanyResearchHistoricalBasisRecovery(
        session, now=lambda: NOW + timedelta(seconds=1)
    )

    first = recovery.recover(initialized.preparation.id)
    second = recovery.recover(initialized.preparation.id)

    assert first == second
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingHistoricalBasis))
        == basis_count_before
    )
    recovered = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert recovered is not None
    assert recovered.lock_version == blocked_draft.lock_version + 1
    assert recovered.content.historical_basis_id == first
    assert [
        event.event_type
        for event in repository.events(initialized.preparation.id)
    ].count("historical_basis_recovered") == 1
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id


def test_historical_basis_recovery_creates_the_exact_basis_when_none_exists(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    old_basis_id = initialized.basis.id
    delete_basis = text(
        "DELETE FROM uw_historical_bases WHERE id = :basis_id"
    ).bindparams(
        bindparam(
            "basis_id", type_=UnderwritingHistoricalBasis.__table__.c.id.type
        )
    )
    assert session.connection().execute(
        delete_basis, {"basis_id": old_basis_id}
    ).rowcount == 1

    recovered_basis_id = CompanyResearchHistoricalBasisRecovery(
        session, now=lambda: NOW + timedelta(seconds=1)
    ).recover(initialized.preparation.id)

    assert recovered_basis_id != old_basis_id
    boundary = resolve_alphabet_company_research_boundary(CUTOFF)
    basis = ProductRepository(session).product_basis(recovered_basis_id)
    assert basis is not None and basis.content_hash == boundary.basis_content_hash
    recovered_draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert recovered_draft is not None
    assert recovered_draft.lock_version == blocked_draft.lock_version + 1
    assert recovered_draft.content.historical_basis_id == recovered_basis_id
    repository = CompanyResearchRepository(session)
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id


def test_historical_basis_recovery_rejects_a_stale_draft_cas_without_requeue(
    session, monkeypatch
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    recovery = CompanyResearchHistoricalBasisRecovery(
        session, now=lambda: NOW + timedelta(seconds=1)
    )
    original_save = recovery._drafts.save

    def race(project_id, *, expected_lock_version, patch):
        WorkspaceDraftService(
            session, now=lambda: NOW + timedelta(seconds=1)
        ).save(
            project_id,
            expected_lock_version=expected_lock_version,
            patch=WorkspaceDraftPatch(user_focus="concurrent edit"),
        )
        return original_save(
            project_id,
            expected_lock_version=expected_lock_version,
            patch=patch,
        )

    monkeypatch.setattr(recovery._drafts, "save", race)

    with pytest.raises(ConflictError, match="workspace draft changed"):
        recovery.recover(initialized.preparation.id)

    repository = CompanyResearchRepository(session)
    preparation = session.get(CompanyResearchPreparation, initialized.preparation.id)
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and preparation.status == "blocked"
    assert job is not None and job.status == "failed"
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert all(
        event.event_type != "historical_basis_recovered"
        for event in repository.events(initialized.preparation.id)
    )


def test_sqlite_two_retries_recover_one_basis_and_queue_one_attempt(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-basis-recovery.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as bootstrap:
        initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
            bootstrap
        )
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        evidence_snapshot = (
            reviewed.id,
            reviewed.version,
            reviewed.content_hash,
            deepcopy(reviewed.payload),
        )
        gaps_snapshot = (gaps.id, gaps.version, gaps.content_hash, deepcopy(gaps.payload))
        bootstrap.commit()

    barrier = Barrier(2)

    def retry_once():
        with sessions() as competing:
            barrier.wait()
            try:
                result = CompanyResearchPreparationService(
                    competing, now=lambda: NOW + timedelta(seconds=1)
                ).retry(project_id=project_id)
                competing.commit()
                return result.preparation.status
            except ValidationError as exc:
                competing.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: retry_once(), range(2)))

    assert outcomes.count("building_model") == 1
    assert sum("recoverable" in outcome or "eligible" in outcome for outcome in outcomes) == 1
    with sessions() as observer:
        repository = CompanyResearchRepository(observer)
        preparation = observer.get(CompanyResearchPreparation, preparation_id)
        job = repository.prepare_job(preparation_id)
        draft = WorkspaceDraftService(observer, now=lambda: NOW).read(project_id)
        evidence = repository.current_artifact(project_id, "evidence_index")
        current_gaps = repository.current_artifact(project_id, "research_gaps")
        assert preparation is not None and job is not None and draft is not None
        assert (preparation.status, preparation.attempt, job.status, job.attempt) == (
            "building_model", 2, "queued", 2
        )
        assert draft.lock_version == blocked_draft.lock_version + 1
        assert draft.content.historical_basis_id is not None
        boundary = resolve_alphabet_company_research_boundary(CUTOFF)
        assert observer.scalar(
            select(func.count())
            .select_from(UnderwritingHistoricalBasis)
            .where(
                UnderwritingHistoricalBasis.content_hash
                == boundary.basis_content_hash
            )
        ) == 1
        basis = ProductRepository(observer).product_basis(
            draft.content.historical_basis_id
        )
        assert basis is not None
        repository.authenticate_governed_historical_basis(
            basis,
            expected_input=boundary.basis_input,
            expected_content_hash=boundary.basis_content_hash,
        )
        assert [event.event_type for event in repository.events(preparation_id)].count(
            "historical_basis_recovered"
        ) == 1
        assert (
            evidence.id,
            evidence.version,
            evidence.content_hash,
            evidence.payload,
        ) == evidence_snapshot
        assert (
            current_gaps.id,
            current_gaps.version,
            current_gaps.content_hash,
            current_gaps.payload,
        ) == gaps_snapshot
        decisions = [
            fact["review_decision"] for fact in evidence.payload["facts"]
        ]
        assert len(decisions) == 7
        assert decisions.count("confirmed") == 6
        assert decisions.count("rejected") == 1
        assert all(
            repository.current_artifact(project_id, kind) is None
            for kind in (
                "business_map",
                "driver_map",
                "financial_bridge",
                "scenario_set",
                "valuation_set",
                "judgment_context",
                "memo",
            )
        )
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_blocked_retry_deadline_rejects_before_recovery_writes_even_if_committed(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    preparation.next_attempt_at = NOW + timedelta(minutes=5)
    session.flush()
    repository = CompanyResearchRepository(session)
    event_snapshot = tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(preparation.id)
    )
    preparation_snapshot = (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.attempt,
        repository._persisted_utc(preparation.next_attempt_at),
        preparation.last_error_code,
    )
    job_snapshot = (job.status, job.step, job.attempt, job.error)

    with pytest.raises(ValidationError, match="not ready to retry"):
        CompanyResearchPreparationService(session, now=lambda: NOW).retry(
            project_id=initialized.project.id
        )
    session.commit()

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None
    assert draft.id == blocked_draft.id
    assert draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.attempt,
        repository._persisted_utc(preparation.next_attempt_at),
        preparation.last_error_code,
    ) == preparation_snapshot
    assert (job.status, job.step, job.attempt, job.error) == job_snapshot
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(preparation.id)
    ) == event_snapshot


def test_blocked_retry_future_event_tail_rejects_before_recovery_writes_even_if_committed(
    session,
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    repository = CompanyResearchRepository(session)
    events = repository.events(initialized.preparation.id)
    tail = events[-1]
    future = NOW + timedelta(minutes=5)
    future_hash = repository.event_content_hash_v2(
        preparation_id=tail.preparation_id,
        sequence=tail.sequence,
        previous_event_hash=tail.previous_event_hash,
        event_type=tail.event_type,
        payload=tail.payload,
        created_at=future,
    )
    assert session.execute(
        text(
            "UPDATE uw_company_research_events SET created_at = :created_at, "
            "content_hash = :content_hash WHERE id = :id"
        ).bindparams(
            bindparam(
                "created_at", type_=CompanyResearchEvent.__table__.c.created_at.type
            ),
            bindparam("id", type_=CompanyResearchEvent.__table__.c.id.type),
        ),
        {"created_at": future, "content_hash": future_hash, "id": tail.id},
    ).rowcount == 1
    session.expire_all()
    basis_count = session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    )
    event_snapshot = tuple(
        (event.id, event.sequence, event.created_at, event.content_hash)
        for event in repository.events(initialized.preparation.id)
    )

    with pytest.raises(ValidationError, match="not ready to retry"):
        CompanyResearchPreparationService(session, now=lambda: NOW).retry(
            project_id=initialized.project.id
        )
    session.commit()

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None
    assert draft.id == blocked_draft.id
    assert draft.lock_version == blocked_draft.lock_version
    assert draft.content.historical_basis_id is None
    assert session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    ) == basis_count
    assert repository.current_artifact(
        initialized.project.id, "evidence_index"
    ).id == reviewed.id
    assert repository.current_artifact(
        initialized.project.id, "research_gaps"
    ).id == gaps.id
    assert tuple(
        (event.id, event.sequence, event.created_at, event.content_hash)
        for event in repository.events(initialized.preparation.id)
    ) == event_snapshot


@pytest.mark.parametrize("failure", ("draft_cas", "recovery_event"))
def test_basis_recovery_savepoint_rolls_back_every_write_when_later_step_fails(
    session, monkeypatch, failure: str
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    assert session.execute(
        text("DELETE FROM uw_historical_bases WHERE id = :basis_id").bindparams(
            bindparam(
                "basis_id", type_=UnderwritingHistoricalBasis.__table__.c.id.type
            )
        ),
        {"basis_id": initialized.basis.id},
    ).rowcount == 1
    session.expire_all()
    recovery = CompanyResearchHistoricalBasisRecovery(
        session, now=lambda: NOW + timedelta(seconds=1)
    )
    repository = CompanyResearchRepository(session)
    basis_count = session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    )
    event_snapshot = tuple(
        (event.id, event.sequence, event.content_hash)
        for event in repository.events(initialized.preparation.id)
    )
    if failure == "draft_cas":
        monkeypatch.setattr(
            recovery._drafts,
            "save",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ConflictError("workspace draft changed")
            ),
        )
        expected_error = ConflictError
    else:
        monkeypatch.setattr(
            recovery._company,
            "append_event",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValidationError("forced recovery event failure")
            ),
        )
        expected_error = ValidationError

    with pytest.raises(expected_error):
        recovery.recover(initialized.preparation.id)
    session.commit()

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None
    assert draft.id == blocked_draft.id
    assert draft.lock_version == blocked_draft.lock_version
    assert draft.content == blocked_draft.content
    assert session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    ) == basis_count
    assert repository.current_artifact(
        initialized.project.id, "evidence_index"
    ).id == reviewed.id
    assert repository.current_artifact(
        initialized.project.id, "research_gaps"
    ).id == gaps.id
    assert tuple(
        (event.id, event.sequence, event.content_hash)
        for event in repository.events(initialized.preparation.id)
    ) == event_snapshot


def test_blocked_retry_rolls_back_recovery_and_queue_when_retry_event_fails(
    session, monkeypatch
) -> None:
    initialized, reviewed, gaps, blocked_draft = _legacy_blocked_missing_basis(
        session
    )
    assert session.execute(
        text("DELETE FROM uw_historical_bases WHERE id = :basis_id").bindparams(
            bindparam(
                "basis_id", type_=UnderwritingHistoricalBasis.__table__.c.id.type
            )
        ),
        {"basis_id": initialized.basis.id},
    ).rowcount == 1
    session.flush()
    repository = CompanyResearchRepository(session)
    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    basis_count = session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    )
    event_snapshot = tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(preparation.id)
    )
    preparation_snapshot = (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.attempt,
        preparation.last_error_code,
    )
    job_snapshot = (job.status, job.step, job.attempt, job.error)
    service = CompanyResearchPreparationService(session, now=lambda: NOW)

    def reject_retry_event(*_args, **kwargs):
        assert kwargs["event_type"] == "retry_queued"
        raise ValidationError("forced retry event failure")

    monkeypatch.setattr(
        service._company_repository, "append_event", reject_retry_event
    )

    with pytest.raises(ValidationError, match="forced retry event failure"):
        service.retry(project_id=initialized.project.id)
    session.commit()
    session.expire_all()

    draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        initialized.project.id
    )
    assert draft is not None
    assert draft.id == blocked_draft.id
    assert draft.lock_version == blocked_draft.lock_version
    assert draft.content == blocked_draft.content
    assert session.scalar(
        select(func.count()).select_from(UnderwritingHistoricalBasis)
    ) == basis_count
    preparation = session.get(CompanyResearchPreparation, preparation.id)
    job = session.get(Job, job.id)
    assert preparation is not None and job is not None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        preparation.attempt,
        preparation.last_error_code,
    ) == preparation_snapshot
    assert (job.status, job.step, job.attempt, job.error) == job_snapshot
    assert repository.current_artifact(initialized.project.id, "evidence_index").id == reviewed.id
    assert repository.current_artifact(initialized.project.id, "research_gaps").id == gaps.id
    assert tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(preparation.id)
    ) == event_snapshot


def test_ordinary_retry_rolls_back_queue_when_retry_event_fails(
    session, monkeypatch
) -> None:
    initialized = _initialized(session, idempotency_key="ordinary-retry-atomic")
    initialized.preparation.status = "recoverable_failure"
    initialized.preparation.last_error_code = "source_unavailable"
    initialized.preparation.next_attempt_at = NOW
    initialized.job.status = "failed"
    initialized.job.error = "source_unavailable"
    session.flush()
    repository = CompanyResearchRepository(session)
    event_snapshot = tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(initialized.preparation.id)
    )
    preparation_snapshot = (
        initialized.preparation.status,
        initialized.preparation.progress,
        initialized.preparation.attempt,
        initialized.preparation.last_error_code,
        repository._persisted_utc(initialized.preparation.next_attempt_at),
    )
    job_snapshot = (
        initialized.job.status,
        initialized.job.attempt,
        initialized.job.error,
    )
    service = CompanyResearchPreparationService(session, now=lambda: NOW)
    monkeypatch.setattr(
        service._company_repository,
        "append_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValidationError("forced retry event failure")
        ),
    )

    with pytest.raises(ValidationError, match="forced retry event failure"):
        service.retry(project_id=initialized.project.id)
    session.commit()
    session.expire_all()

    preparation = session.get(
        CompanyResearchPreparation, initialized.preparation.id
    )
    job = session.get(Job, initialized.job.id)
    assert preparation is not None and job is not None
    assert (
        preparation.status,
        preparation.progress,
        preparation.attempt,
        preparation.last_error_code,
        repository._persisted_utc(preparation.next_attempt_at),
    ) == preparation_snapshot
    assert (job.status, job.attempt, job.error) == job_snapshot
    assert tuple(
        (event.id, event.sequence, event.event_type, event.content_hash)
        for event in repository.events(preparation.id)
    ) == event_snapshot


def test_sqlite_two_ordinary_retries_queue_one_attempt_and_one_event(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-ordinary-retry.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as bootstrap:
        initialized = _initialized(bootstrap, idempotency_key="ordinary-concurrent")
        initialized.preparation.status = "recoverable_failure"
        initialized.preparation.last_error_code = "source_unavailable"
        initialized.preparation.next_attempt_at = NOW - timedelta(seconds=1)
        initialized.job.status = "failed"
        initialized.job.error = "source_unavailable"
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        bootstrap.commit()

    barrier = Barrier(2)

    def retry_once():
        with sessions() as competing:
            barrier.wait()
            try:
                result = CompanyResearchPreparationService(
                    competing, now=lambda: NOW
                ).retry(project_id=project_id)
                competing.commit()
                return result.preparation.status
            except ValidationError as exc:
                competing.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: retry_once(), range(2)))

    assert outcomes.count("queued") == 1
    assert sum("recoverable" in outcome for outcome in outcomes) == 1
    with sessions() as observer:
        repository = CompanyResearchRepository(observer)
        preparation = observer.get(CompanyResearchPreparation, preparation_id)
        job = repository.prepare_job(preparation_id)
        assert preparation is not None and job is not None
        assert (preparation.status, preparation.attempt) == ("queued", 2)
        assert (job.status, job.attempt) == ("queued", 2)
        assert [
            event.event_type
            for event in repository.events(preparation_id)
            if event.event_type == "retry_queued"
        ] == ["retry_queued"]

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("cached_status", "durable_status", "expects_recovery"),
    (
        ("blocked", "recoverable_failure", False),
        ("recoverable_failure", "blocked", True),
    ),
)
def test_retry_selects_branch_from_fresh_locked_state(
    tmp_path, cached_status: str, durable_status: str, expects_recovery: bool
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'fresh-retry-branch-{cached_status}.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as bootstrap:
        initialized, _reviewed, _gaps, blocked_draft = (
            _legacy_blocked_missing_basis(bootstrap)
        )
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        job_id = initialized.job.id
        if cached_status == "recoverable_failure":
            initialized.preparation.status = "recoverable_failure"
            initialized.preparation.last_error_code = "source_unavailable"
        bootstrap.commit()

    with sessions() as cached:
        stale_preparation = cached.get(
            CompanyResearchPreparation, preparation_id
        )
        assert stale_preparation is not None
        assert stale_preparation.status == cached_status
        with sessions() as mutator:
            preparation = mutator.get(
                CompanyResearchPreparation, preparation_id
            )
            job = mutator.get(Job, job_id)
            assert preparation is not None and job is not None
            preparation.status = durable_status
            preparation.last_error_code = (
                "validation_failed"
                if durable_status == "blocked"
                else "source_unavailable"
            )
            preparation.progress = 30
            preparation.current_step = "model_bundle"
            job.status = "failed"
            job.step = "model_bundle"
            job.error = preparation.last_error_code
            mutator.commit()

        result = CompanyResearchPreparationService(
            cached, now=lambda: NOW + timedelta(seconds=1)
        ).retry(project_id=project_id)
        cached.commit()

        assert result.preparation.status == "building_model"
        assert result.preparation.current_step == "model_bundle"
        assert result.preparation.progress == 25
        assert result.preparation.attempt == 2
        draft = WorkspaceDraftService(cached, now=lambda: NOW).read(project_id)
        assert draft is not None and draft.id == blocked_draft.id
        assert (draft.content.historical_basis_id is not None) is expects_recovery
        event_types = [
            event.event_type
            for event in CompanyResearchRepository(cached).events(preparation_id)
        ]
        assert event_types.count("historical_basis_recovered") == (
            1 if expects_recovery else 0
        )
        assert event_types.count("retry_queued") == 1
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize("cached_state", ["future_then_due", "due_then_future"])
def test_retry_deadline_is_decided_from_fresh_locked_state(
    tmp_path, cached_state: str
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'company-retry-deadline-{cached_state}.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as bootstrap:
        initialized = _initialized(
            bootstrap, idempotency_key=f"retry-deadline-{cached_state}"
        )
        initialized.preparation.status = "recoverable_failure"
        initialized.preparation.last_error_code = "source_unavailable"
        initialized.preparation.next_attempt_at = (
            NOW + timedelta(minutes=5)
            if cached_state == "future_then_due"
            else NOW - timedelta(seconds=1)
        )
        initialized.job.status = "failed"
        initialized.job.error = "source_unavailable"
        project_id = initialized.project.id
        preparation_id = initialized.preparation.id
        bootstrap.commit()

    with sessions() as cached, sessions() as mutator:
        service = CompanyResearchPreparationService(cached, now=lambda: NOW)
        stale = service.status(project_id=project_id).preparation
        cached.commit()
        changed = mutator.get(CompanyResearchPreparation, preparation_id)
        assert changed is not None
        changed.next_attempt_at = (
            NOW - timedelta(seconds=1)
            if cached_state == "future_then_due"
            else NOW + timedelta(minutes=5)
        )
        mutator.commit()
        assert stale.next_attempt_at != changed.next_attempt_at

        if cached_state == "future_then_due":
            assert service.retry(project_id=project_id).preparation.status == "queued"
            cached.commit()
        else:
            with pytest.raises(
                ValidationError, match="not ready to retry"
            ):
                service.retry(project_id=project_id)
            cached.rollback()

    Base.metadata.drop_all(engine)
    engine.dispose()


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
def test_postgresql_two_workers_skip_a_locked_project_and_claim_distinct_jobs(
    engine,
) -> None:
    sessions = sessionmaker(bind=engine, future=True)
    initializer_session = sessions()
    first_session = sessions()
    second_session = sessions()
    try:
        first_initialized = _initialized(
            initializer_session, idempotency_key="pg-skip-locked-first"
        )
        first_initialized.preparation.request_hash = "f" * 64
        initializer_session.flush()
        second_initialized = _initialized(
            initializer_session, idempotency_key="pg-skip-locked-second"
        )
        expected = {
            first_initialized.preparation.id,
            second_initialized.preparation.id,
        }
        initializer_session.commit()
        first = CompanyResearchPreparationWorker(first_session, now=lambda: NOW)
        second = CompanyResearchPreparationWorker(second_session, now=lambda: NOW)

        first_claim = first.claim_next()
        assert first_claim is not None
        with ThreadPoolExecutor(max_workers=1) as pool:
            second_claim = pool.submit(second.claim_next).result(timeout=3)
        assert second_claim is not None
        assert {first_claim.preparation_id, second_claim.preparation_id} == expected
    finally:
        first_session.rollback()
        second_session.rollback()
        initializer_session.rollback()
        first_session.close()
        second_session.close()
        initializer_session.close()
