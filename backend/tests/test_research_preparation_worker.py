from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.domain.atomic_claims import AtomicClaimDraft
from app.domain.research_preparation import preparation_input_fingerprint
from app.models.event_research import EventResearchScopeFactor, EventResearchScopeVersion
from app.models.ledger import (
    Base,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact
from app.models.research_preparation import ResearchPreparationEvent
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
)
from app.models.source_governance import SourceContract
from app.services.research_preparation import ResearchPreparationService
from app.services.research_preparation import ClaimDecision, ProtocolConfirmation
from app.services.atomic_claims import AtomicClaimService
from app.repositories.research_preparation import ResearchPreparationRepository


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'preparation-worker.db'}", future=True)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, future=True)


def _preparation(session, *, contract_effective_until=None):
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
        SourceContract(document_version_id=document.id, source_type="company_disclosure", provider_or_tenant="issuer", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=contract_effective_until, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="test", intake_metadata={}, declared_by="test", created_at=now),
        EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="test", change_summary="test", created_at=now),
    ))
    scope = session.scalar(select(EventResearchScopeVersion).where(EventResearchScopeVersion.research_case_id == case.id))
    assert scope is not None
    thesis = Thesis(research_case_id=case.id, statement="Revenue outcome", research_protocol_required=True, created_by="test", created_at=now)
    session.add(thesis)
    session.flush()
    session.add(EventResearchScopeFactor(scope_version_id=scope.id, statement=thesis.statement, description=None, position=1))
    template = MechanismTemplateVersion(template_key=f"worker-template-{case.id}", version=1, display_name="Worker template", industry_scope="test", supersedes_id=None, approved_by="test", reason="fixture", created_at=now)
    session.add(template)
    session.flush()
    source = MechanismNodeVersion(template_version_id=template.id, node_key="source", display_name="Source", role="driver", created_at=now)
    target = MechanismNodeVersion(template_version_id=template.id, node_key="target", display_name="Target", role="outcome", created_at=now)
    session.add_all((source, target))
    session.flush()
    session.add(MechanismEdgeVersion(template_version_id=template.id, edge_key="source_to_target", source_node_id=source.id, target_node_id=target.id, created_at=now))
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
    def __init__(self, payload=None):
        self.payload = payload

    def draft_protocol(self, _input):
        if self.payload is not None:
            return self.payload
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


def _materializable_protocol(session, case_id):
    document = session.scalar(select(DocumentVersion).join(CaseDocumentVersion).where(CaseDocumentVersion.research_case_id == case_id))
    thesis = session.scalar(select(Thesis).where(Thesis.research_case_id == case_id))
    template = session.scalar(select(MechanismTemplateVersion).where(MechanismTemplateVersion.template_key == f"worker-template-{case_id}"))
    edge = session.scalar(select(MechanismEdgeVersion).where(MechanismEdgeVersion.template_version_id == template.id))
    assert document is not None and thesis is not None and template is not None and edge is not None
    baseline = {"source_ref": f"document:{document.id}", "value": "1", "unit": "yuan", "observed_period": "2025-12-31", "available_at": document.available_at.replace(tzinfo=UTC).isoformat()}
    return {"outcomes": [{"thesis_id": str(thesis.id), "metric": {"metric_id": f"worker-metric-{thesis.id}", "display_name": "Revenue", "canonical_definition": "Quarterly revenue", "entity_scope": "company", "unit": "yuan", "frequency": "quarterly", "period_semantics": "period_end", "allowed_source_roles": ["primary_disclosure"], "role_eligibility": ["outcome"]}, "binding": {"entity_scope": {"company_id": "company", "company": "Company"}, "direction": "increase", "baseline": baseline, "horizon_start": "2026-01-01", "horizon_end": "2026-12-31"}, "template_version_id": str(template.id), "verification_rules": [{"mechanism_edge_id": str(edge.id), "expected_direction": "increase", "support_predicate": "supports", "contradiction_predicate": "contradicts", "allowed_source_roles": ["primary_disclosure"], "observed_period_start": "2026-01-01", "observed_period_end": "2026-12-31", "available_at_deadline": "2027-01-01", "next_verification_event": "earnings"}]}], "baseline": {"document_id": str(document.id)}, "horizon": {"start": "2026-01-01", "end": "2026-12-31"}, "mechanisms": [{"template_version_id": str(template.id)}], "verification_rules": [{"rule": "outcome rule"}]}


def test_postgresql_preparation_claim_locks_only_jobs() -> None:
    statement = ResearchPreparationRepository._eligible_preparation_jobs(
        datetime.now(UTC)
    ).with_for_update(of=Job, skip_locked=True).limit(1)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF jobs SKIP LOCKED" in sql
    assert "LEFT OUTER JOIN" not in sql and "JOIN research_preparations" not in sql


def test_sqlite_conditional_claim_allows_exactly_one_winner(tmp_path) -> None:
    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        job_id = setup.scalar(select(Job.id).where(Job.target_id == preparation.id))
        setup.commit()
    first, second = sessions(), sessions()
    try:
        # Both workers see the same candidate before either conditional write.
        assert first.scalar(ResearchPreparationRepository._eligible_preparation_jobs(datetime.now(UTC)).with_only_columns(Job.id)) == job_id
        assert second.scalar(ResearchPreparationRepository._eligible_preparation_jobs(datetime.now(UTC)).with_only_columns(Job.id)) == job_id
        assert first.execute(update(Job).where(Job.id == job_id, Job.status == "queued", Job.cancel_requested.is_(False)).values(status="running")).rowcount == 1
        first.commit()
        assert second.execute(update(Job).where(Job.id == job_id, Job.status == "queued", Job.cancel_requested.is_(False)).values(status="running")).rowcount == 0
        second.commit()
    finally:
        first.close()
        second.close()
    with Session(engine) as check:
        assert check.get(Job, job_id).status == "running"


def test_repository_claim_only_returns_one_job_across_two_sqlite_sessions(tmp_path) -> None:
    _, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        preparation_id = preparation.id
        setup.commit()
    first, second = sessions(), sessions()
    try:
        assert ResearchPreparationRepository(first).claim_next_preparation_job() is not None
        first.commit()
        assert ResearchPreparationRepository(second).claim_next_preparation_job() is None
        second.commit()
    finally:
        first.close()
        second.close()
    with sessions() as check:
        assert check.scalar(select(Job).where(Job.target_id == preparation_id)).status == "running"


def test_sqlite_preparation_claim_uses_a_bounded_candidate_batch() -> None:
    from app.repositories.research_preparation import SQLITE_PREPARATION_CLAIM_BATCH_SIZE

    statement = ResearchPreparationRepository._eligible_preparation_jobs(
        datetime.now(UTC)
    ).with_only_columns(Job.id).limit(SQLITE_PREPARATION_CLAIM_BATCH_SIZE)
    assert "LIMIT" in str(statement.compile())
    assert SQLITE_PREPARATION_CLAIM_BATCH_SIZE == 100


def test_claim_token_changes_after_stale_recovery(tmp_path) -> None:
    _, sessions = _session_factory(tmp_path)
    now = datetime.now(UTC)
    with sessions() as setup:
        _, preparation, _ = _preparation(setup)
        first = ResearchPreparationRepository(setup).claim_next_preparation_job(now=now)
        assert first is not None and first.claim_token is not None
        first_token = first.claim_token
        first.started_at = now - timedelta(hours=1)
        setup.commit()
    with sessions() as recovery:
        repo = ResearchPreparationRepository(recovery)
        assert repo.recover_stale_preparation_jobs(before=now - timedelta(minutes=30)) == 1
        reclaimed = repo.claim_next_preparation_job(now=now)
        assert reclaimed is not None
        assert reclaimed.claim_token is not None and reclaimed.claim_token != first_token
        assert reclaimed.started_at is not None
        assert reclaimed.started_at.replace(tzinfo=UTC) == now
        recovery.commit()


def test_old_claim_token_cannot_fail_or_cancel_a_reclaimed_job(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker
    from app.ai.research_preparation import load_preparation_input

    engine, sessions = _session_factory(tmp_path)
    now = datetime.now(UTC)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        repo = ResearchPreparationRepository(setup)
        first = repo.claim_next_preparation_job(now=now)
        assert first is not None
        first_input = worker._begin(setup, first)
        assert first_input is not None and first_input.claim_token is not None
        first.started_at = now - timedelta(hours=1)
        setup.commit()
        case_id, preparation_id, job_id = case.id, preparation.id, first.id
    with sessions() as recovery:
        repo = ResearchPreparationRepository(recovery)
        assert repo.recover_stale_preparation_jobs(before=now - timedelta(minutes=30)) == 1
        second = repo.claim_next_preparation_job(now=now)
        assert second is not None
        second_input = worker._begin(recovery, second)
        assert second_input is not None and second_input.claim_token != first_input.claim_token
        recovery.commit()
    worker._provider_failure(sessions, job_id=job_id, input=first_input)
    worker._internal_failure(sessions, job_id=job_id, input=first_input)
    # A's late completion is a no-op; B owns and completes the one artifact.
    with sessions() as load:
        loaded_input = load_preparation_input(load, case_id)
    drafts = _ParseGenerator().validate_claim_drafts(loaded_input)
    worker._complete(
        sessions,
        job_id=job_id,
        input=first_input,
        generator=_ParseGenerator(),
        loaded_input=loaded_input,
        output=drafts,
    )
    worker._complete(
        sessions,
        job_id=job_id,
        input=second_input,
        generator=_ParseGenerator(),
        loaded_input=loaded_input,
        output=drafts,
    )
    with Session(engine) as check:
        job = check.get(Job, job_id)
        prep = check.get(ResearchPreparation, preparation_id)
        assert job is not None and job.status == "succeeded" and job.claim_token == second_input.claim_token
        assert prep is not None and prep.parse_claims_state == "succeeded"
        assert len(check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        )).all()) == 1
        assert check.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all() == []


def test_internal_generator_error_fails_preparation_safely_and_can_retry(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    class BrokenGenerator:
        def validate_claim_drafts(self, _input):
            raise TypeError("Bearer sk-sentinel https://internal.invalid")

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        case_id, preparation_id = case.id, preparation.id
        setup.commit()
    with pytest.raises(TypeError):
        worker.run_once(session_factory=sessions, generator_factory=BrokenGenerator)
    with sessions() as repair:
        prep = repair.get(ResearchPreparation, preparation_id)
        job = repair.scalar(select(Job).where(Job.target_id == preparation_id))
        assert prep is not None and prep.parse_claims_state == "failed"
        assert prep.status == "recoverable_failure" and prep.last_error_code == "preparation_internal_error"
        assert job is not None and job.status == "failed"
        durable = f"{job.error} {prep.last_error_code}"
        assert "TypeError" not in durable and "sk-sentinel" not in durable and "internal.invalid" not in durable
        ResearchPreparationService(repair).retry_failed_step(case_id, actor="reviewer", revision=prep.version)
        repair.commit()
    with Session(engine) as check:
        assert check.get(ResearchPreparation, preparation_id).parse_claims_state == "queued"
        assert check.scalar(select(Job).where(Job.target_id == preparation_id, Job.status == "queued")) is not None


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
        strict_protocol = _materializable_protocol(setup, case_id)
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
    assert worker.run_once(session_factory=sessions, generator_factory=lambda: _ProtocolGenerator(strict_protocol))
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


@pytest.mark.parametrize("step", ["draft_protocol", "draft_evidence_plan"])
def test_changed_candidate_context_discards_protocol_or_plan_output(tmp_path, step: str) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        case_id, preparation_id = case.id, preparation.id
        strict_protocol = _materializable_protocol(setup, case_id)
        setup.commit()
    assert worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator)
    with sessions() as review:
        preparation = review.get(ResearchPreparation, preparation_id)
        claims = review.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        ))
        assert preparation is not None and claims is not None
        candidate_id = uuid.UUID(claims.payload["candidates"][0]["candidate_id"])
        ResearchPreparationService(review).confirm_claims(
            case_id, actor="reviewer", revision=preparation.version,
            decisions=[ClaimDecision(candidate_id=candidate_id, outcome="confirmed", reason="reviewed")],
        )
        review.commit()
    if step == "draft_evidence_plan":
        assert worker.run_once(session_factory=sessions, generator_factory=lambda: _ProtocolGenerator(strict_protocol))
        with sessions() as review:
            preparation = review.get(ResearchPreparation, preparation_id)
            protocol = review.scalar(select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation_id,
                ResearchPreparationArtifact.kind == "research_protocol_draft",
                ResearchPreparationArtifact.state == "current",
            ))
            assert preparation is not None and protocol is not None
            ResearchPreparationService(review).confirm_protocol(
                case_id, actor="reviewer", revision=preparation.version,
                payload=ProtocolConfirmation(draft_sequence=protocol.sequence, edits={}),
            )
            review.commit()

    class StaleGenerator:
        def _change_context(self):
            with sessions() as change:
                AtomicClaimService(change).review(
                    candidate_id, outcome="confirmed", reviewer="reviewer",
                    reason="new review", idempotency_key=f"new-context-{step}",
                )
                change.commit()

        def draft_protocol(self, _input):
            self._change_context()
            return _ProtocolGenerator(strict_protocol).draft_protocol(_input)

        def draft_evidence_plan(self, _input):
            self._change_context()
            return _PlanGenerator().draft_evidence_plan(_input)

    assert worker.run_once(session_factory=sessions, generator_factory=StaleGenerator)
    kind = "research_protocol_draft" if step == "draft_protocol" else "evidence_acquisition_plan"
    with Session(engine) as check:
        job = check.scalar(select(Job).where(
            Job.target_id == preparation_id,
            Job.correlation_id.like(f"%:{step}"),
        ))
        assert job is not None and job.status == "cancelled"
        assert check.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == kind,
            ResearchPreparationArtifact.state == "current",
        )) is None
        event = check.scalar(select(ResearchPreparationEvent).where(
            ResearchPreparationEvent.research_preparation_id == preparation_id,
            ResearchPreparationEvent.type == "preparation_output_discarded",
            ResearchPreparationEvent.step == step,
        ))
        assert event is not None and event.detail["reason"] == "candidate_context_changed"
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


def test_unavailable_source_input_fails_the_step_without_leaving_a_running_job(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        _, preparation, _ = _preparation(
            setup, contract_effective_until=datetime.now(UTC) - timedelta(seconds=1)
        )
        preparation_id = preparation.id
        setup.commit()

    assert worker.run_once(session_factory=sessions, generator_factory=_ParseGenerator)
    with Session(engine) as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        job = check.scalar(select(Job).where(Job.target_id == preparation_id))
        assert preparation is not None and preparation.status == "recoverable_failure"
        assert preparation.parse_claims_state == "failed"
        assert job is not None and job.status == "failed"
        assert "https://" not in (job.error or "")
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


def test_old_correlation_is_discarded_before_any_provider_call(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker

    engine, sessions = _session_factory(tmp_path)
    with sessions() as setup:
        case, preparation, _ = _preparation(setup)
        preparation_id = preparation.id
        old_job = setup.scalar(select(Job).where(Job.target_id == preparation_id))
        assert old_job is not None
        old_job_id = old_job.id
        ResearchPreparationService(setup).create_for_case(
            case.id, input_fingerprint="c" * 64, actor="reset"
        )
        setup.commit()

    calls = 0
    class Generator:
        def validate_claim_drafts(self, _input):
            nonlocal calls
            calls += 1
            return []

    assert worker.run_once(session_factory=sessions, generator_factory=Generator)
    with Session(engine) as check:
        job = check.get(Job, old_job_id)
        event = check.scalar(select(ResearchPreparationEvent).where(
            ResearchPreparationEvent.research_preparation_id == preparation_id,
            ResearchPreparationEvent.type == "preparation_output_discarded",
            ResearchPreparationEvent.detail["reason"].as_string() == "version_changed",
        ))
        assert calls == 0
        assert job is not None and job.status == "cancelled"
        assert event is not None and event.detail["job_id"] == str(old_job_id)
        assert check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.state == "current",
        )).all() == []


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
        assert WorkerHeartbeatService(session).status(worker_kind="research_preparation")["status"] == "available"


def test_preparation_heartbeat_does_not_make_research_run_worker_available(tmp_path) -> None:
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    _, sessions = _session_factory(tmp_path)
    with sessions() as session:
        heartbeats = WorkerHeartbeatService(session)
        heartbeats.touch(
            worker_id="preparation-only",
            mode="loop",
            state="polling",
            worker_kind="research_preparation",
        )
        assert heartbeats.status()["status"] == "unavailable"
        assert heartbeats.status(worker_kind="research_preparation")["status"] == "available"
        heartbeats.touch(
            worker_id="run-worker",
            mode="loop",
            state="polling",
            worker_kind="research_run",
        )
        assert heartbeats.status()["status"] == "available"


def test_worker_heartbeat_namespaces_a_shared_operator_id(tmp_path) -> None:
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    _, sessions = _session_factory(tmp_path)
    with sessions() as session:
        heartbeats = WorkerHeartbeatService(session)
        heartbeats.touch(worker_id="operator", mode="loop", state="polling", worker_kind="research_run")
        heartbeats.touch(worker_id="operator", mode="loop", state="polling", worker_kind="research_preparation")
        assert heartbeats.status(worker_kind="research_run")["status"] == "available"
        assert heartbeats.status(worker_kind="research_preparation")["status"] == "available"


def test_once_preparation_heartbeat_never_advertises_an_available_worker(tmp_path) -> None:
    from app.scripts import run_research_preparation_worker as worker
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    _, sessions = _session_factory(tmp_path)
    worker._touch(mode="once", state="idle", session_factory=sessions)
    with sessions() as session:
        status = WorkerHeartbeatService(session).status(worker_kind="research_preparation")
        assert status["mode"] == "once"
        assert status["status"] == "stale"


def test_once_cli_records_once_heartbeat_mode(tmp_path, monkeypatch) -> None:
    from app.scripts import run_research_preparation_worker as worker
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    _, sessions = _session_factory(tmp_path)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "run_once", lambda: False)
    monkeypatch.setattr("sys.argv", ["run_research_preparation_worker", "--once"])
    worker.main()
    with sessions() as session:
        status = WorkerHeartbeatService(session).status(worker_kind="research_preparation")
        assert status["mode"] == "once" and status["state"] == "idle"
