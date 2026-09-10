"""PostgreSQL locking proof for orchestration confirmation."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.models.event_research import (
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.acquisition import AcquisitionJob
from app.models.events import DomainEvent
from app.models.ledger import CaseTenantAdmission, ResearchCase, Thesis
from app.models.operational import EventResearchLifecycle, Job, JobEvent, ResearchRun
from app.models.research_orchestration import (
    AcquisitionQueryPlan,
    AcquisitionSeries,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.models.research_protocol import (
    CaseMechanismSelectionVersion,
    MechanismTemplateVersion,
)
from app.errors import ValidationFailedError
from app.repositories.documents import DocumentRepository
from app.repositories.auto_research import AutoResearchRepository
from app.repositories.research_orchestration import (
    ResearchOrchestrationRepository,
)
from app.services.ingest import DocumentService
from app.services.auto_research import AutoResearchService
from app.services.event_research import EventResearchService
from app.services.event_research_scope import EventResearchScopeService
from app.services.research_orchestration import (
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
from app.services.research_protocol import ResearchProtocolService
from app.schemas.v1.event_research import CreateEventResearchRequest
from tests.protocol_provenance import seed_protocol_footprint
from tests.research_identity import persist_research_principal


def _seed_appendable_orchestration(session, *, tenant_id: str, actor: str):
    now = datetime.now(UTC)
    document = DocumentService(DocumentRepository(session)).freeze(
        raw=f"appendable-{uuid.uuid4()}".encode(),
        source_url=f"https://example.test/{uuid.uuid4()}",
    )
    case = ResearchCase(
        title="Appendable orchestration",
        industry_topic="semiconductors",
        created_at=now,
        created_by=actor,
    )
    session.add(case)
    session.flush()
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by=actor,
        change_summary="initial",
        created_at=now,
    )
    session.add(scope)
    session.flush()
    session.add(
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id=tenant_id,
            initial_document_version_id=document.id,
            admitted_by=actor,
            admitted_at=now,
        )
    )
    orchestration = ResearchOrchestration(
        tenant_id=tenant_id,
        research_case_id=case.id,
        current_scope_version_id=scope.id,
        current_research_run_id=None,
        state="planning_acquisition",
        user_stage="acquisition",
        current_system_action="Planning source acquisition",
        system_action_reason="The scope is confirmed",
        checkpoint_json={},
        next_action_kind=None,
        next_action_label=None,
        next_action_payload=None,
        last_heartbeat_at=now,
        recovery_status="healthy",
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(orchestration)
    session.commit()
    return case.id, orchestration.id


def _seed_stale_acquiring(session, *, tenant_id: str):
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Two-worker acquisition recovery",
            event_title=f"Two-worker recovery {uuid.uuid4()}",
            research_question="Can recovery preserve one acquisition identity?",
            candidate_factors=["orders", "revenue", "inventory"],
            research_protocol_required=False,
        ),
        principal=persist_research_principal(
            session, tenant_id=tenant_id, label="stale-acquisition"
        ),
    )
    case_id = uuid.UUID(created.case_id)
    principal = OrchestrationPrincipal(tenant_id, "worker:research-orchestration")
    ResearchOrchestrationService(session).confirm_scope(
        case_id,
        principal,
        f"confirm:{case_id}",
    )
    orchestration = ResearchOrchestrationService(session).reconcile(
        case_id,
        principal,
    )
    assert orchestration.state == "acquiring"
    orchestration.last_heartbeat_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    return case_id, orchestration.id


def _run_two_acquisition_recovery_workers(engine, *, now: datetime):
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    start_gate = Barrier(2)
    complete_gate = Barrier(2)
    reconcile_gate = Barrier(2)

    def run_worker():
        with sessions() as command:
            service = ResearchOrchestrationService(command)
            start_gate.wait(timeout=10)
            started = service.start_stale_acquisition_recoveries(
                now=now,
                stale_before=now - timedelta(hours=1),
                limit=10,
            )
            command.commit()
            complete_gate.wait(timeout=10)
            completed = service.complete_acquisition_recoveries(
                now=now + timedelta(seconds=1),
                limit=10,
            )
            command.commit()
            reconcile_gate.wait(timeout=10)
            reconciled = service.reconcile_batch(
                now=now + timedelta(seconds=2),
                stale_before=now - timedelta(hours=1),
                limit=10,
            )
            command.commit()
            usable = command.scalar(select(1))
            command.commit()
            return started, completed, reconciled, usable

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_worker) for _ in range(2)]
        return [future.result(timeout=30) for future in futures]


@pytest.mark.pg_only
def test_two_concurrent_confirms_create_one_logical_confirmation(engine, session):
    now = datetime.now(UTC)
    tenant_id = "tenant-concurrent"
    actor = "human:concurrent"
    sessions = sessionmaker(bind=engine, future=True)
    setup = session
    document = DocumentService(DocumentRepository(setup)).freeze(
        raw=f"concurrent-{uuid.uuid4()}".encode(),
        source_url=f"https://example.test/{uuid.uuid4()}",
    )
    case = ResearchCase(
        title="Concurrent confirmation",
        industry_topic="semiconductors",
        created_at=now,
        created_by=actor,
    )
    setup.add(case)
    setup.flush()
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by=actor,
        change_summary="initial",
        created_at=now,
    )
    setup.add(scope)
    setup.flush()
    statement = "Concurrent active thesis"
    setup.add_all(
        [
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id=tenant_id,
                initial_document_version_id=document.id,
                admitted_by=actor,
                admitted_at=now,
            ),
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement=statement,
                description=None,
                position=1,
            ),
            Thesis(
                research_case_id=case.id,
                statement=statement,
                research_protocol_required=False,
                created_by=actor,
                created_at=now,
            ),
            EventResearchLifecycle(
                research_case_id=case.id,
                status="awaiting_key_review",
                active_run_id=None,
                current_round=0,
                status_summary="Awaiting scope confirmation",
                current_gap="Scope not confirmed",
                next_human_action="Confirm scope",
                updated_at=now,
            ),
        ]
    )
    setup.commit()
    case_id = case.id

    barrier = Barrier(2)

    def confirm() -> tuple[uuid.UUID, int]:
        with sessions() as command:
            barrier.wait(timeout=10)
            orchestration = ResearchOrchestrationService(command).confirm_scope(
                case_id,
                OrchestrationPrincipal(tenant_id, actor),
                "same-confirmation",
            )
            result = (orchestration.id, orchestration.version)
            command.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))

    assert len({result[0] for result in results}) == 1
    assert [result[1] for result in results] == [1, 1]
    orchestration_id = results[0][0]
    with sessions() as verification:
        assert verification.scalar(
            select(func.count())
            .select_from(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id == orchestration_id
            )
        ) == 1
        runs = list(
            verification.scalars(
                select(ResearchRun).where(
                    ResearchRun.research_case_id == case_id
                )
            )
        )
        assert len(runs) == 1
        assert (runs[0].status, runs[0].stage) == (
            "prepared",
            "awaiting_acquisition",
        )
        assert verification.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run", Job.target_id == runs[0].id)
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.aggregate_type == "research_orchestration",
                DomainEvent.aggregate_id == str(orchestration_id),
            )
        ) == 1


@pytest.mark.pg_only
def test_two_concurrent_prepared_run_enqueues_create_one_job(engine, session):
    now = datetime.now(UTC)
    sessions = sessionmaker(bind=engine, future=True)
    case = ResearchCase(
        title="Concurrent prepared enqueue",
        industry_topic="semiconductors",
        created_at=now,
        created_by="system:test",
    )
    session.add(case)
    session.flush()
    run = AutoResearchService(session).repo.create_run(
        research_case_id=case.id,
        status="prepared",
        stage="awaiting_acquisition",
    )
    session.commit()
    run_id = run.id
    barrier = Barrier(2)

    def enqueue() -> uuid.UUID:
        with sessions() as command:
            barrier.wait(timeout=10)
            job = AutoResearchService(command).enqueue_prepared_run(
                run_id,
                commit=False,
            )
            job_id = job.id
            command.commit()
            return job_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(lambda _: enqueue(), range(2)))

    assert len(set(job_ids)) == 1
    with sessions() as verification:
        persisted_run = verification.get(ResearchRun, run_id)
        assert persisted_run is not None
        assert (persisted_run.status, persisted_run.stage) == (
            "queued",
            "planning",
        )
        assert verification.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run", Job.target_id == run_id)
        ) == 1
        assert verification.scalar(
            select(func.count()).select_from(JobEvent)
        ) == 1


@pytest.mark.pg_only
def test_scope_revision_racing_stale_enqueue_leaves_no_live_old_job(
    engine,
    session,
):
    sessions = sessionmaker(bind=engine, future=True)
    factors = ["旧范围因素一", "旧范围因素二", "旧范围因素三"]
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Scope revision concurrency input",
            event_title="Scope revision concurrency",
            research_question="Can the revised scope be researched safely?",
            candidate_factors=factors,
            research_protocol_required=False,
        ),
        principal=persist_research_principal(
            session,
            tenant_id="tenant-concurrent-revision",
            label="scope-revision",
        ),
    )
    case_id = uuid.UUID(created.case_id)
    orchestration = ResearchOrchestrationService(session).confirm_scope(
        case_id,
        OrchestrationPrincipal(
            "tenant-concurrent-revision",
            "human:concurrent",
        ),
        "confirm-before-concurrent-revision",
    )
    old_run_id = orchestration.current_research_run_id
    assert old_run_id is not None
    orchestration_id = orchestration.id
    session.commit()
    barrier = Barrier(2)

    def enqueue_old() -> str:
        with sessions() as command:
            barrier.wait(timeout=10)
            try:
                AutoResearchService(command).enqueue_prepared_run(
                    old_run_id,
                    commit=False,
                )
            except ValueError:
                command.rollback()
                return "rejected"
            command.commit()
            return "enqueued"

    def revise_scope() -> str:
        with sessions() as command:
            barrier.wait(timeout=10)
            EventResearchScopeService(command).update(
                case_id,
                [factors[2], factors[0], factors[1]],
                "human:scope-editor",
                "Concurrent scope revision",
            )
            command.commit()
            return "revised"

    with ThreadPoolExecutor(max_workers=2) as executor:
        enqueue_result = executor.submit(enqueue_old)
        revision_result = executor.submit(revise_scope)
        assert revision_result.result(timeout=15) == "revised"
        assert enqueue_result.result(timeout=15) in {"enqueued", "rejected"}

    with sessions() as verification:
        old_run = verification.get(ResearchRun, old_run_id)
        current = verification.get(ResearchOrchestration, orchestration_id)
        assert old_run is not None
        assert current is not None
        assert (old_run.status, old_run.stage) == ("cancelled", "stopped")
        assert current.current_research_run_id not in {None, old_run_id}
        successor = verification.get(
            ResearchRun,
            current.current_research_run_id,
        )
        assert successor is not None
        assert (successor.status, successor.stage) == (
            "prepared",
            "awaiting_acquisition",
        )
        old_jobs = list(
            verification.scalars(
                select(Job).where(
                    Job.kind == "research_run",
                    Job.target_id == old_run_id,
                )
            )
        )
        assert all(job.status == "cancelled" for job in old_jobs)
        assert verification.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind == "research_run",
                Job.target_id == successor.id,
            )
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id == current.id,
                ResearchOrchestrationEvent.transition == "scope_revised",
            )
        ) == 1


@pytest.mark.pg_only
def test_two_concurrent_protocol_resumes_prepare_one_successor(
    engine,
    session,
):
    sessions = sessionmaker(bind=engine, future=True)
    tenant_id = "tenant-concurrent-protocol-resume"
    actor = "human:protocol-owner"
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Concurrent protocol resume input",
            event_title="Concurrent protocol resume",
            research_question="Can protocol completion resume once?",
            candidate_factors=["因素一", "因素二", "因素三"],
            research_protocol_required=False,
        ),
        principal=persist_research_principal(
            session, tenant_id=tenant_id, label="protocol-resume"
        ),
    )
    case_id = uuid.UUID(created.case_id)
    orchestration = ResearchOrchestrationService(session).confirm_scope(
        case_id,
        OrchestrationPrincipal(tenant_id, actor),
        "confirm-before-protocol-resume",
    )
    EventResearchScopeService(session).update(
        case_id,
        ["因素一", "因素二", "新增协议因素"],
        actor,
        "Add protocol-gated factor",
    )
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    blocked_thesis = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == "新增协议因素",
        )
    )
    assert scope is not None
    assert blocked_thesis is not None
    assert orchestration.state == "needs_scope_decision"
    seed_protocol_footprint(session, blocked_thesis, status="ready")
    session.commit()
    orchestration_id = orchestration.id
    scope_id = scope.id
    barrier = Barrier(2)

    def resume() -> tuple[uuid.UUID, int]:
        with sessions() as command:
            barrier.wait(timeout=10)
            current = ResearchOrchestrationService(
                command
            ).resume_after_protocol_completion(
                case_id,
                OrchestrationPrincipal(tenant_id, actor),
                scope_id,
                "same-protocol-resume",
            )
            result = (current.current_research_run_id, current.version)
            command.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resume(), range(2)))

    assert len({run_id for run_id, _ in results}) == 1
    assert [version for _, version in results] == [3, 3]
    with sessions() as verification:
        current = verification.get(
            ResearchOrchestration,
            orchestration_id,
        )
        assert current is not None
        successor_id = current.current_research_run_id
        assert successor_id is not None
        successor = verification.get(ResearchRun, successor_id)
        assert successor is not None
        assert (successor.status, successor.stage) == (
            "prepared",
            "awaiting_acquisition",
        )
        assert verification.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(ResearchRun.research_case_id == case_id)
        ) == 2
        assert verification.scalar(
            select(func.count())
            .select_from(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == orchestration_id,
                ResearchOrchestrationEvent.transition == "protocol_completed",
            )
        ) == 1
        assert verification.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.aggregate_type == "research_orchestration",
                DomainEvent.aggregate_id == str(orchestration_id),
                DomainEvent.type
                == "research.orchestration.protocol_completed",
            )
        ) == 1
        assert verification.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "research_run")
        ) == 0


@pytest.mark.pg_only
def test_protocol_mutation_and_resume_are_serialized_by_the_same_case_lock(
    engine,
    session,
):
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    tenant_id = "tenant-protocol-resume-race"
    actor = "human:protocol-owner"
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Protocol mutation versus resume",
            event_title="Protocol mutation versus resume",
            research_question="Does resume use the current locked protocol?",
            candidate_factors=["因素一", "因素二", "因素三"],
            research_protocol_required=False,
        ),
        principal=persist_research_principal(
            session, tenant_id=tenant_id, label="protocol-race"
        ),
    )
    case_id = uuid.UUID(created.case_id)
    orchestration = ResearchOrchestrationService(session).confirm_scope(
        case_id,
        OrchestrationPrincipal(tenant_id, actor),
        "confirm-before-protocol-race",
    )
    EventResearchScopeService(session).update(
        case_id,
        ["因素一", "因素二", "新增协议因素"],
        actor,
        "Add protocol-gated factor",
    )
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    blocked_thesis = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == "新增协议因素",
        )
    )
    assert scope is not None
    assert blocked_thesis is not None
    footprint = seed_protocol_footprint(
        session,
        blocked_thesis,
        status="ready",
    )
    replacement = MechanismTemplateVersion(
        template_key=f"replacement-{uuid.uuid4().hex}",
        version=1,
        display_name="Concurrent replacement without rules",
        industry_scope="test",
        approved_by=actor,
        reason="Force protocol readiness to be re-evaluated",
        created_at=datetime.now(UTC),
    )
    session.add(replacement)
    session.commit()
    scope_id = scope.id
    orchestration_id = orchestration.id
    original_template_id = footprint.template.id
    replacement_id = replacement.id
    barrier = Barrier(2)

    def mutate_protocol() -> str:
        with sessions() as command:
            barrier.wait(timeout=10)
            ResearchProtocolService(command).select_template(
                case_id,
                replacement_id,
                reviewer=actor,
                reason="Concurrent protocol revision",
            )
            command.commit()
            return "mutated"

    def resume_protocol() -> tuple[str, uuid.UUID | None]:
        with sessions() as command:
            barrier.wait(timeout=10)
            try:
                current = ResearchOrchestrationService(
                    command
                ).resume_after_protocol_completion(
                    case_id,
                    OrchestrationPrincipal(tenant_id, actor),
                    scope_id,
                    "protocol-race-resume",
                )
            except ValidationFailedError:
                command.rollback()
                return "blocked", None
            run_id = current.current_research_run_id
            command.commit()
            return "started", run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        mutation_future = executor.submit(mutate_protocol)
        resume_future = executor.submit(resume_protocol)
        assert mutation_future.result(timeout=20) == "mutated"
        outcome, run_id = resume_future.result(timeout=20)

    with sessions() as verification:
        latest_selection = verification.scalar(
            select(CaseMechanismSelectionVersion)
            .where(
                CaseMechanismSelectionVersion.research_case_id == case_id
            )
            .order_by(
                CaseMechanismSelectionVersion.created_at.desc(),
                CaseMechanismSelectionVersion.id.desc(),
            )
            .limit(1)
        )
        assert latest_selection is not None
        assert latest_selection.template_version_id == replacement_id
        current = verification.get(ResearchOrchestration, orchestration_id)
        assert current is not None
        if outcome == "blocked":
            assert run_id is None
            assert current.state == "needs_scope_decision"
            assert current.current_research_run_id is None
        else:
            assert run_id is not None
            assert current.current_research_run_id == run_id
            run = verification.get(ResearchRun, run_id)
            assert run is not None
            # Resume won the Case lock and rechecked the original effective
            # protocol before the replacement could commit. The append-only
            # replacement is therefore later than the run start; it did not
            # become a stale protocol before that start boundary.
            original_selection = verification.scalar(
                select(CaseMechanismSelectionVersion)
                .where(
                    CaseMechanismSelectionVersion.research_case_id == case_id,
                    CaseMechanismSelectionVersion.template_version_id
                    == original_template_id,
                )
            )
            assert original_selection is not None
            assert run.created_at <= latest_selection.created_at


@pytest.mark.pg_only
def test_find_reconcilable_skips_case_locked_by_normal_command(engine, session):
    now = datetime.now(UTC)
    stale_before = now - timedelta(hours=1)
    tenant_id = "tenant-reconcile-lock-order"
    actor = "system:reconciler"
    setup = session
    document = DocumentService(DocumentRepository(setup)).freeze(
        raw=f"lock-order-{uuid.uuid4()}".encode(),
        source_url=f"https://example.test/{uuid.uuid4()}",
    )
    rows: list[tuple[ResearchCase, ResearchOrchestration]] = []
    for index in range(3):
        case = ResearchCase(
            title=f"Reconciliation candidate {index}",
            industry_topic="semiconductors",
            created_at=now,
            created_by=actor,
        )
        setup.add(case)
        setup.flush()
        scope = EventResearchScopeVersion(
            research_case_id=case.id,
            version=1,
            changed_by=actor,
            change_summary="initial",
            created_at=now,
        )
        setup.add(scope)
        setup.flush()
        setup.add(
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id=tenant_id,
                initial_document_version_id=document.id,
                admitted_by=actor,
                admitted_at=now,
            )
        )
        orchestration = ResearchOrchestration(
            tenant_id=tenant_id,
            research_case_id=case.id,
            current_scope_version_id=scope.id,
            current_research_run_id=None,
            state="retry_wait",
            user_stage="acquisition",
            current_system_action="Waiting to retry",
            system_action_reason="A retry is pending",
            checkpoint_json={},
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            last_heartbeat_at=now - timedelta(hours=3 - index),
            recovery_status="stale",
            version=0,
            created_at=now,
            updated_at=now - timedelta(hours=3 - index),
        )
        setup.add(orchestration)
        setup.flush()
        rows.append((case, orchestration))
    setup.commit()
    case_ids = [case.id for case, _ in rows]
    orchestration_ids = [orchestration.id for _, orchestration in rows]

    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as session_a, sessions() as session_b:
        cached_second = session_b.get(
            ResearchOrchestration, orchestration_ids[1]
        )
        assert cached_second.version == 0
        session_b.commit()

        advanced_second = session_a.get(
            ResearchOrchestration, orchestration_ids[1]
        )
        advanced_second.version = 5
        session_a.commit()

        session_a.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == case_ids[0])
            .with_for_update()
        )
        session_b.execute(text("SET LOCAL lock_timeout = '1s'"))

        repository_b = ResearchOrchestrationRepository(session_b)
        found = repository_b.find_reconcilable(
            now=now,
            stale_before=stale_before,
            limit=1,
        )

        assert [row.id for row in found] == [orchestration_ids[1]]
        assert found[0] is cached_second
        assert found[0].version == 5
        heartbeat = repository_b.record_heartbeat(found[0], 5, now)
        assert heartbeat.version == 6
        session_b.commit()
        session_a.rollback()

    with sessions() as verification:
        assert verification.get(
            ResearchOrchestration, orchestration_ids[1]
        ).version == 6


@pytest.mark.pg_only
def test_postgres_future_retry_wait_rows_do_not_starve_batch(session):
    now = datetime.now(UTC)
    tenant_id = "tenant-retry-scheduler"
    future_ids = []
    for index in range(4):
        case_id, orchestration_id = _seed_appendable_orchestration(
            session,
            tenant_id=tenant_id,
            actor="worker:retry-scheduler",
        )
        run = AutoResearchRepository(session).create_run(
            research_case_id=case_id
        )
        job = AutoResearchRepository(session).enqueue_run_job(run)
        run.status = "failed"
        run.stage = "failed"
        job.status = "failed"
        job.failure_count = 1
        job.next_retry_at = now + timedelta(hours=1)
        orchestration = session.get(ResearchOrchestration, orchestration_id)
        orchestration.state = "retry_wait"
        orchestration.user_stage = "evidence_synthesis"
        orchestration.current_research_run_id = run.id
        orchestration.last_heartbeat_at = now - timedelta(
            hours=4, minutes=index
        )
        orchestration.updated_at = now - timedelta(hours=4, minutes=index)
        future_ids.append(orchestration.id)
        session.commit()

    _, executable_id = _seed_appendable_orchestration(
        session,
        tenant_id=tenant_id,
        actor="worker:retry-scheduler",
    )
    repository = ResearchOrchestrationRepository(session)

    before_due = repository.find_reconcilable(
        now=now + timedelta(minutes=1),
        stale_before=now - timedelta(hours=1),
        limit=2,
    )

    assert [row.id for row in before_due] == [executable_id]
    session.rollback()

    after_due = repository.find_reconcilable(
        now=now + timedelta(hours=2),
        stale_before=now - timedelta(hours=1),
        limit=10,
    )

    assert set(future_ids) <= {row.id for row in after_due}


@pytest.mark.pg_only
def test_stale_acquisition_recovery_candidates_skip_locked_case(engine, session):
    now = datetime.now(UTC)
    stale_before = now - timedelta(hours=1)
    tenant_id = "tenant-stale-acquisition-lock"
    first_case_id, first_orchestration_id = _seed_appendable_orchestration(
        session,
        tenant_id=tenant_id,
        actor="worker:recovery",
    )
    second_case_id, second_orchestration_id = _seed_appendable_orchestration(
        session,
        tenant_id=tenant_id,
        actor="worker:recovery",
    )
    first = session.get(ResearchOrchestration, first_orchestration_id)
    second = session.get(ResearchOrchestration, second_orchestration_id)
    assert first is not None and second is not None
    first.last_heartbeat_at = now - timedelta(hours=3)
    first.updated_at = now - timedelta(hours=3)
    second.last_heartbeat_at = now - timedelta(hours=2)
    second.updated_at = now - timedelta(hours=2)
    session.commit()

    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as session_a, sessions() as session_b:
        session_a.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == first_case_id)
            .with_for_update()
        )
        session_b.execute(text("SET LOCAL lock_timeout = '1s'"))

        found = ResearchOrchestrationRepository(
            session_b
        ).find_stale_acquisition_recovery_candidates(
            now=now,
            stale_before=stale_before,
            limit=1,
        )

        assert [row.id for row in found] == [second_orchestration_id]
        session_b.commit()
        session_a.rollback()

    with sessions() as verification:
        found = ResearchOrchestrationRepository(
            verification
        ).find_stale_acquisition_recovery_candidates(
            now=now,
            stale_before=stale_before,
            limit=1,
        )
        assert [row.id for row in found] == [first_orchestration_id]


@pytest.mark.pg_only
def test_incomplete_recovery_selects_invalid_markers_and_skips_locked_case(
    engine,
    session,
):
    now = datetime.now(UTC)
    tenant_id = "tenant-invalid-recovery-marker-lock"
    first_case_id, first_orchestration_id = _seed_appendable_orchestration(
        session,
        tenant_id=tenant_id,
        actor="worker:recovery",
    )
    _, second_orchestration_id = _seed_appendable_orchestration(
        session,
        tenant_id=tenant_id,
        actor="worker:recovery",
    )
    first = session.get(ResearchOrchestration, first_orchestration_id)
    second = session.get(ResearchOrchestration, second_orchestration_id)
    assert first is not None and second is not None
    first.state = "recovering"
    first.checkpoint_json = {"_acquisition_recovery": "invalid-secret"}
    first.updated_at = now - timedelta(hours=2)
    second.state = "recovering"
    second.checkpoint_json = {
        "_acquisition_recovery": {"resume_state": "invalid-state"}
    }
    second.updated_at = now - timedelta(hours=1)
    for orchestration, episode_id in (
        (first, "a" * 24),
        (second, "b" * 24),
    ):
        session.add(
            ResearchOrchestrationEvent(
                orchestration_id=orchestration.id,
                sequence=1,
                transition="acquisition_recovery_started",
                actor="worker:recovery",
                message="immutable recovery identity",
                payload_json={
                    "recovery_kind": "acquisition",
                    "episode_id": episode_id,
                    "resume_state": "planning_acquisition",
                    "checkpoint_digest": "c" * 16,
                },
                idempotency_key=(
                    f"acquisition-recovery:{orchestration.id}:"
                    f"{episode_id}:started"
                ),
                created_at=now,
            )
        )
    session.commit()

    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as session_a, sessions() as session_b:
        session_a.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == first_case_id)
            .with_for_update()
        )
        session_b.execute(text("SET LOCAL lock_timeout = '1s'"))

        found = ResearchOrchestrationRepository(
            session_b
        ).find_incomplete_acquisition_recoveries(now=now, limit=1)

        assert [row.id for row in found] == [second_orchestration_id]
        session_b.commit()
        session_a.rollback()

    with sessions() as verification:
        found = ResearchOrchestrationRepository(
            verification
        ).find_incomplete_acquisition_recoveries(now=now, limit=1)
        assert [row.id for row in found] == [first_orchestration_id]


@pytest.mark.pg_only
def test_two_workers_recover_one_acquiring_episode_without_duplicate_identity(
    engine,
    session,
):
    case_id, orchestration_id = _seed_stale_acquiring(
        session,
        tenant_id="tenant-two-worker-acquisition-recovery",
    )
    original_job_ids = set(
        session.scalars(
            select(AcquisitionJob.id).where(
                AcquisitionJob.research_case_id == case_id
            )
        )
    )
    original_series_ids = set(
        session.scalars(
            select(AcquisitionSeries.id).where(
                AcquisitionSeries.research_case_id == case_id
            )
        )
    )
    original_plan_ids = set(
        session.scalars(
            select(AcquisitionQueryPlan.id).where(
                AcquisitionQueryPlan.series_id.in_(original_series_ids)
            )
        )
    )
    assert len(original_job_ids) == len(original_series_ids) == len(original_plan_ids) == 7
    now = datetime.now(UTC)

    results = _run_two_acquisition_recovery_workers(engine, now=now)

    assert sum(result[0] for result in results) == 1
    assert sum(result[1] for result in results) == 1
    assert sum(result[2] for result in results) == 1
    assert [result[3] for result in results] == [1, 1]
    with sessionmaker(bind=engine, future=True)() as verification:
        current = verification.get(ResearchOrchestration, orchestration_id)
        assert current is not None
        assert current.state == "acquiring"
        events = list(
            verification.scalars(
                select(ResearchOrchestrationEvent)
                .where(
                    ResearchOrchestrationEvent.orchestration_id
                    == orchestration_id,
                    ResearchOrchestrationEvent.transition.in_(
                        (
                            "acquisition_recovery_started",
                            "recovery_completed",
                        )
                    ),
                )
                .order_by(ResearchOrchestrationEvent.sequence)
            )
        )
        assert [event.transition for event in events] == [
            "acquisition_recovery_started",
            "recovery_completed",
        ]
        assert len({event.idempotency_key for event in events}) == 2
        assert set(
            verification.scalars(
                select(AcquisitionJob.id).where(
                    AcquisitionJob.research_case_id == case_id
                )
            )
        ) == original_job_ids
        assert set(
            verification.scalars(
                select(AcquisitionSeries.id).where(
                    AcquisitionSeries.research_case_id == case_id
                )
            )
        ) == original_series_ids
        assert set(
            verification.scalars(
                select(AcquisitionQueryPlan.id).where(
                    AcquisitionQueryPlan.series_id.in_(original_series_ids)
                )
            )
        ) == original_plan_ids


@pytest.mark.pg_only
def test_two_workers_fail_corrupt_acquiring_binding_without_completion(
    engine,
    session,
):
    _, orchestration_id = _seed_stale_acquiring(
        session,
        tenant_id="tenant-two-worker-corrupt-acquisition",
    )
    orchestration = session.get(ResearchOrchestration, orchestration_id)
    assert orchestration is not None
    checkpoint = dict(orchestration.checkpoint_json)
    acquisition = dict(checkpoint["acquisition"])
    goals = list(acquisition["goals"])
    goals[0], goals[1] = goals[1], goals[0]
    acquisition["goals"] = goals
    checkpoint["acquisition"] = acquisition
    orchestration.checkpoint_json = checkpoint
    session.commit()
    now = datetime.now(UTC)

    results = _run_two_acquisition_recovery_workers(engine, now=now)

    assert sum(result[0] for result in results) == 1
    assert sum(result[1] for result in results) == 1
    assert [result[3] for result in results] == [1, 1]
    with sessionmaker(bind=engine, future=True)() as verification:
        current = verification.get(ResearchOrchestration, orchestration_id)
        assert current is not None
        assert current.state == "failed"
        transitions = list(
            verification.scalars(
                select(ResearchOrchestrationEvent.transition)
                .where(
                    ResearchOrchestrationEvent.orchestration_id
                    == orchestration_id
                )
                .order_by(ResearchOrchestrationEvent.sequence)
            )
        )
        assert transitions.count("acquisition_recovery_started") == 1
        assert transitions.count("recovery_failed") == 1
        assert "recovery_completed" not in transitions


@pytest.mark.pg_only
def test_two_concurrent_public_appends_serialize_sequences_and_outbox(
    engine, session
):
    tenant_id = "tenant-concurrent-append"
    actor = "system:append-worker"
    case_id, orchestration_id = _seed_appendable_orchestration(
        session, tenant_id=tenant_id, actor=actor
    )
    sessions = sessionmaker(bind=engine, future=True)
    barrier = Barrier(2)

    def append(index: int) -> int:
        with sessions() as command_session:
            barrier.wait(timeout=10)
            event = ResearchOrchestrationRepository(
                command_session
            ).append_event(
                case_id,
                tenant_id,
                transition="worker_checkpointed",
                actor=actor,
                message=f"Worker checkpoint {index}",
                payload={"worker": index},
                idempotency_key=f"concurrent-append-{index}",
            )
            sequence = event.sequence
            command_session.commit()
            return sequence

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = list(executor.map(append, range(2)))

    assert sorted(sequences) == [1, 2]
    with sessions() as verification:
        events = list(
            verification.scalars(
                select(ResearchOrchestrationEvent)
                .where(
                    ResearchOrchestrationEvent.orchestration_id
                    == orchestration_id
                )
                .order_by(ResearchOrchestrationEvent.sequence)
            )
        )
        assert [event.sequence for event in events] == [1, 2]
        assert verification.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.aggregate_type == "research_orchestration",
                DomainEvent.aggregate_id == str(orchestration_id),
            )
        ) == 2


@pytest.mark.pg_only
def test_unauthorized_lookup_does_not_lock_case_from_authorized_append(
    engine, session
):
    tenant_id = "tenant-authorized-append"
    actor = "human:authorized"
    case_id, orchestration_id = _seed_appendable_orchestration(
        session, tenant_id=tenant_id, actor=actor
    )
    sessions = sessionmaker(bind=engine, future=True)

    with sessions() as unauthorized, sessions() as authorized:
        assert ResearchOrchestrationRepository(unauthorized).get_for_update(
            case_id, "tenant-unauthorized"
        ) is None
        authorized.execute(text("SET LOCAL lock_timeout = '1s'"))

        event = ResearchOrchestrationRepository(authorized).append_event(
            case_id,
            tenant_id,
            transition="authorized_checkpoint",
            actor=actor,
            message="Authorized append was not blocked",
            payload={"authorized": True},
            idempotency_key="authorized-after-intruder",
        )
        sequence = event.sequence
        authorized.commit()
        unauthorized.rollback()

    assert sequence == 1
    with sessions() as verification:
        assert verification.scalar(
            select(func.count())
            .select_from(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == orchestration_id
            )
        ) == 1
        assert verification.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(
                DomainEvent.aggregate_type == "research_orchestration",
                DomainEvent.aggregate_id == str(orchestration_id),
            )
        ) == 1
