from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
from app.models.research_preparation import ResearchPreparationEvent
from app.models.source_governance import SourceContract
from app.services.research_preparation import ResearchPreparationService
from app.services.research_preparation import ClaimDecision, ProtocolConfirmation


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


class _ProtocolGenerator:
    def draft_protocol(self, _input):
        return {
            "outcomes": [{"metric": "revenue"}],
            "baseline": {"metric": "revenue"},
            "horizon": {"start": "2026-01-01", "end": "2026-12-31"},
            "mechanisms": [{"driver": "demand"}],
            "verification_rules": [{"rule": "filing"}],
        }


class _PlanGenerator:
    def draft_evidence_plan(self, _input):
        return {"items": []}


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


def test_protocol_then_plan_wait_for_their_respective_human_reviews(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        case_id, preparation_id = case.id, preparation.id
        setup.commit()
    assert worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator)
    with sessions() as review:
        preparation = review.get(ResearchPreparation, preparation_id)
        artifact = review.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        ))
        assert preparation is not None and artifact is not None
        candidate_id = artifact.payload["candidates"][0]["candidate_id"]
        ResearchPreparationService(review).confirm_claims(
            case_id,
            actor="reviewer",
            revision=preparation.version,
            decisions=[ClaimDecision(candidate_id=uuid.UUID(candidate_id), outcome="confirmed", reason="reviewed")],
        )
        review.commit()
    assert worker.run_once(session_factory=sessions, generator_factory=_ProtocolGenerator)
    with sessions() as review:
        preparation = review.get(ResearchPreparation, preparation_id)
        protocol = review.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "research_protocol_draft",
            ResearchPreparationArtifact.state == "current",
        ))
        assert preparation is not None and protocol is not None
        assert preparation.protocol_review_state == "awaiting_review"
        ResearchPreparationService(review).confirm_protocol(
            case_id,
            actor="reviewer",
            revision=preparation.version,
            payload=ProtocolConfirmation(draft_sequence=protocol.sequence, edits={}),
        )
        review.commit()
    assert worker.run_once(session_factory=sessions, generator_factory=_PlanGenerator)
    with Session(engine) as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        assert preparation is not None
        assert preparation.draft_evidence_plan_state == "succeeded"
        assert preparation.plan_review_state == "awaiting_review"
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


def test_third_provider_failure_is_recoverable_without_another_queued_job(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        preparation_id = preparation.id
        setup.commit()
    for attempt in range(3):
        assert worker.run_once(session_factory=sessions, generator_factory=_ProviderFailureGenerator)
        if attempt < 2:
            with sessions() as retry:
                retry.get(ResearchPreparation, preparation_id).next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
                retry.commit()
    with Session(engine) as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        job = check.scalar(select(Job).where(Job.target_id == preparation_id))
        assert preparation is not None and preparation.parse_claims_state == "failed"
        assert preparation.status == "recoverable_failure"
        assert job is not None and job.status == "failed" and job.attempt == 3
        assert check.scalars(select(Job).where(
            Job.target_id == preparation_id,
            Job.status.in_(("queued", "running")),
        )).all() == []
        assert check.scalars(select(ResearchRun)).all() == []


def test_queued_cancelled_preparation_job_never_calls_provider(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        job = setup.scalar(select(Job).where(Job.target_id == preparation.id))
        assert job is not None
        job.cancel_requested = True
        preparation_id, job_id = preparation.id, job.id
        setup.commit()

    calls = 0
    class Generator:
        def validate_claim_drafts(self, _input):
            nonlocal calls
            calls += 1
            return []

    assert worker.run_once(session_factory=sessions, generator_factory=Generator)
    with Session(engine) as check:
        job = check.get(Job, job_id)
        assert calls == 0
        assert job is not None and job.status == "cancelled"
        assert check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id
        )).all() == []
        event = check.scalar(select(ResearchPreparationEvent).where(
            ResearchPreparationEvent.research_preparation_id == preparation_id,
            ResearchPreparationEvent.type == "preparation_output_discarded",
        ))
        assert event is not None and event.detail["reason"] == "cancelled"
        assert check.scalars(select(ResearchRun)).all() == []


def test_cancel_after_claim_before_provider_call_never_invokes_generator(tmp_path, monkeypatch) -> None:
    from app.scripts import run_research_preparation_worker as worker

    _, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        job = setup.scalar(select(Job).where(Job.target_id == preparation.id))
        assert job is not None
        job_id = job.id
        setup.commit()

    original_load = worker.load_preparation_input
    def cancel_during_load(session, case_id):
        value = original_load(session, case_id)
        with sessions() as cancellation:
            cancellation.get(Job, job_id).cancel_requested = True
            cancellation.commit()
        return value
    monkeypatch.setattr(worker, "load_preparation_input", cancel_during_load)
    calls = 0
    class Generator:
        def validate_claim_drafts(self, _input):
            nonlocal calls
            calls += 1
            return []

    assert worker.run_once(session_factory=sessions, generator_factory=Generator)
    assert calls == 0
    with sessions() as check:
        assert check.get(Job, job_id).status == "cancelled"


def test_late_input_change_discards_parse_output_with_a_safe_event(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        case_id, preparation_id = case.id, preparation.id
        job = setup.scalar(select(Job).where(Job.target_id == preparation_id))
        assert job is not None
        job_id = job.id
        setup.commit()

    class LateChangeGenerator(_ParseGenerator):
        def validate_claim_drafts(self, input):
            drafts = super().validate_claim_drafts(input)
            with sessions() as change:
                prep = change.get(ResearchPreparation, preparation_id)
                assert prep is not None
                prep.version += 1
                prep.input_fingerprint = "b" * 64
                change.commit()
            return drafts

    assert worker.run_once(session_factory=sessions, generator_factory=LateChangeGenerator)
    with Session(engine) as check:
        job = check.get(Job, job_id)
        assert job is not None and job.status == "cancelled"
        assert check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id
        )).all() == []
        event = check.scalar(select(ResearchPreparationEvent).where(
            ResearchPreparationEvent.research_preparation_id == preparation_id,
            ResearchPreparationEvent.type == "preparation_output_discarded",
        ))
        assert event is not None
        assert event.detail == {"job_id": str(job_id), "step": "parse_claims", "reason": "version_changed"}
        assert check.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all() == []


def test_recovery_gives_reclaimed_job_a_fresh_lease_and_does_not_reclaim_twice(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    stale_at = datetime.now(UTC) - timedelta(hours=2)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        job = setup.scalar(select(Job).where(Job.target_id == preparation.id))
        assert job is not None
        job.status, job.started_at, job.step = "running", stale_at, "parse_claims"
        preparation.parse_claims_state = "running"
        preparation_id, job_id = preparation.id, job.id
        setup.commit()

    assert worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator, recover_after_minutes=30)
    with Session(engine) as check:
        job = check.get(Job, job_id)
        assert job is not None and job.status == "succeeded"
        assert job.started_at is not None
        assert job.started_at.replace(tzinfo=UTC) > stale_at
        assert check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.state == "current",
        )).all()
    assert not worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator, recover_after_minutes=30)


def test_preparation_heartbeat_uses_available_loop_contract(tmp_path, monkeypatch) -> None:
    from app.scripts import run_research_preparation_worker as worker
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    _, sessions = _session_factory(tmp_path)
    monkeypatch.setenv("RESEARCH_PREPARATION_WORKER_ID", "prep-heartbeat")
    worker._touch(mode="loop", state="polling", session_factory=sessions)
    with sessions() as session:
        assert WorkerHeartbeatService(session).status()["status"] == "available"
