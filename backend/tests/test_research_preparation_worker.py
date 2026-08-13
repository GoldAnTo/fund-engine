from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.atomic_claims import AtomicClaimDraft
from app.domain.research_preparation import preparation_input_fingerprint
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import (
    Base,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
)
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact
from app.models.source_governance import SourceContract
from app.services.research_preparation import ResearchPreparationService


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'preparation-worker.db'}", future=True)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, future=True)


def _preparation(session):
    now = datetime.now(UTC)
    case = ResearchCase(title="worker", industry_topic="test", created_by="test", created_at=now)
    document = DocumentVersion(
        content_sha256="a" * 64,
        source_url="https://example.test/document",
        available_at=now,
        acquired_at=now,
        parser_version="test",
        source_authority="primary_disclosure",
    )
    session.add_all((case, document))
    session.flush()
    session.add_all((
        CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now),
        CaseTenantAdmission(research_case_id=case.id, tenant_id="test", initial_document_version_id=document.id, admitted_by="test", admitted_at=now),
        SourceContract(document_version_id=document.id, source_type="company_disclosure", provider_or_tenant="issuer", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="test", intake_metadata={}, declared_by="test", created_at=now),
        EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="test", change_summary="test", created_at=now),
    ))
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="Revenue grew ten percent.")
    session.add(span)
    session.flush()
    preparation = ResearchPreparationService(session).create_for_case(
        case.id,
        input_fingerprint=preparation_input_fingerprint(document.id, session.scalar(select(EventResearchScopeVersion.id).where(EventResearchScopeVersion.research_case_id == case.id))),
        actor="test",
    )
    session.flush()
    return case, preparation, span


class _ParseGenerator:
    def validate_claim_drafts(self, input):
        span = input.source_spans[0]
        return [AtomicClaimDraft(
            source_span_id=span.source_span_id,
            quote="Revenue grew",
            quote_start=0,
            quote_end=len("Revenue grew"),
            normalized_text="Revenue grew ten percent",
            claim_type="disclosed_fact",
            assertion_actor=None,
            subject=None,
            predicate=None,
            object_text=None,
            numeric_value=None,
            unit=None,
            observed_period=None,
            scope={},
        )]


class _ProviderFailureGenerator:
    def validate_claim_drafts(self, _input):
        from app.ai.research_preparation import ResearchPreparationProviderError

        raise ResearchPreparationProviderError("Bearer sk-sentinel https://provider.invalid")


def test_preparation_worker_runs_parse_without_creating_a_research_run(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        case_id, preparation_id = case.id, preparation.id
        setup.commit()

    assert worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator)

    with Session(engine) as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        assert preparation is not None
        assert preparation.parse_claims_state == "succeeded"
        assert preparation.claim_review_state == "awaiting_review"
        assert preparation.draft_protocol_state == "queued"
        artifact = check.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        ))
        assert artifact is not None and len(artifact.payload["candidates"]) == 1
        job = check.scalar(select(Job).where(Job.target_id == preparation_id))
        assert job is not None and job.status == "succeeded"
        assert check.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all() == []


def test_preparation_and_run_workers_claim_only_their_own_job_kinds(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as preparation_worker
    from app.scripts import run_research_worker as run_worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        normal = Job(kind="assess", status="queued", target_type="research_run", target_id=None, created_at=datetime.now(UTC))
        setup.add(normal)
        setup.flush()
        preparation_id, normal_id = preparation.id, normal.id
        setup.commit()

    # The preparation worker must leave a normal job alone.
    assert preparation_worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator)
    with Session(engine) as check:
        assert check.get(Job, normal_id).status == "queued"
        assert check.scalar(select(Job).where(Job.target_id == preparation_id)).status == "succeeded"

    # A normal worker's run-job query must not claim a queued preparation job.
    with sessions() as setup:
        again = Job(kind="prepare_research", status="queued", target_type="research_preparation", target_id=preparation_id, research_case_id=None, correlation_id=f"{preparation_id}:1:parse_claims", created_at=datetime.now(UTC))
        setup.add(again)
        setup.flush()
        setup.commit()
        again_id = again.id
    run_worker.SessionLocal = sessions
    run_worker.MonitorScheduler.dispatch_due = lambda _self: []
    run_worker.FundDisclosureSyncScheduler.dispatch_due = lambda _self: []
    assert not run_worker.run_once()
    with Session(engine) as check:
        assert check.get(Job, again_id).status == "queued"


def test_provider_failure_requeues_with_a_fixed_safe_error(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        preparation_id = preparation.id
        setup.commit()

    assert worker.run_once(session_factory=sessions, generator_factory=_ProviderFailureGenerator)

    with Session(engine) as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        job = check.scalar(select(Job).where(Job.target_id == preparation_id))
        assert preparation is not None and preparation.parse_claims_state == "retrying"
        assert preparation.next_attempt_at is not None
        assert job is not None and job.status == "queued" and job.attempt == 2
        durable = f"{job.error} {preparation.last_error_code}"
        assert "sk-sentinel" not in durable and "Bearer" not in durable and "provider.invalid" not in durable
