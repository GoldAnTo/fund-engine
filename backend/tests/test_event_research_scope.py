from __future__ import annotations

import uuid
from datetime import datetime, timezone
from threading import Event, Thread, get_ident

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.errors import ValidationFailedError
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.events import DomainEvent
from app.models.ledger import (
    AIAssessment,
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, Job, ResearchRun, ResearchTask
from app.models.proposals import Proposal
from app.services.auto_research import AutoResearchService
from app.services.event_conclusion import EventConclusionService
from app.services.event_review_queue import EventReviewQueueService
from app.services.event_research_scope import EventResearchScopeService
from app.services.event_research_scope_evidence import (
    append_current_scope_evidence_assignment,
    lock_event_scope_case,
    lock_event_research_lifecycle,
)
from app.services.event_research import EventResearchService
from app.repositories.event_research import EventResearchLifecycleRepository
from app.repositories.operational import TaskRepository
from app.schemas.v1.event_research import CreateEventResearchRequest


INITIAL_FACTORS = [
    "资本开支上调可能加剧自由现金流担忧",
    "盈利前景与市场预期可能存在分歧",
    "估值重定价可能放大盘后波动",
]


def _create_event(client) -> dict:
    response = client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "Alphabet 公布财报后上调全年资本开支指引，盘后股价下跌。",
            "source_url": "https://example.com/alphabet",
            "event_title": "Alphabet 财报后股价下跌",
            "company_name": "Alphabet",
            "ticker": "GOOGL",
            "market_reaction": "盘后下跌",
            "research_question": "资本开支上调是否是盘后下跌的主要因素？",
            "candidate_factors": INITIAL_FACTORS,
            "created_by": "tester",
        },
    )
    assert response.status_code == 201
    return response.json()


def _start_case_run(client, session, case_id: uuid.UUID) -> uuid.UUID:
    """Scope-replacement tests need an explicitly authorized prior run."""
    response = client.post(
        f"/api/v1/research-cases/{case_id}/runs",
        json={"max_rounds": 3, "budget": 100},
    )
    assert response.status_code == 201
    run_id = uuid.UUID(response.json()["id"])
    lifecycle = session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.active_run_id = run_id
    session.commit()
    return run_id


def _scope_statements(session, version_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(EventResearchScopeFactor.statement)
            .where(EventResearchScopeFactor.scope_version_id == version_id)
            .order_by(EventResearchScopeFactor.position)
        )
    )


def _reviewed_evidence(session, case_id: uuid.UUID, factor: str) -> EvidenceLink:
    now = datetime.now(timezone.utc)
    thesis = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == factor,
        )
    )
    assert thesis is not None
    document = DocumentVersion(
        content_sha256=str(uuid.uuid4()).replace("-", ""),
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Verified release",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="Reviewed evidence",
        locator={"kind": "fixture"},
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="Reviewed evidence statement",
        created_at=now,
    )
    session.add(statement)
    session.flush()
    link = EvidenceLink(
        thesis_id=thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="reviewed fixture",
        scope={"period": "event"},
        available_at=now,
        creator_type="human",
        review_state="reviewed",
        created_at=now,
    )
    session.add(link)
    session.commit()
    return link


def _cover_current_scope(session, case_id: uuid.UUID) -> list[EvidenceLink]:
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert scope is not None
    links: list[EvidenceLink] = []
    for factor in _scope_statements(session, scope.id):
        link = _reviewed_evidence(session, case_id, factor)
        append_current_scope_evidence_assignment(
            session,
            case_id=case_id,
            evidence_link_id=link.id,
            factor_statement=factor,
            created_at=datetime.now(timezone.utc),
        )
        links.append(link)
    session.commit()
    return links


def test_creating_event_persists_ordered_scope_version_one(cmd_client, cmd_session) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])

    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion).where(
                EventResearchScopeVersion.research_case_id == case_id
            )
        )
    )

    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].changed_by == "tester"
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS


def test_scope_case_lock_requests_a_for_update_research_case_row() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.statement = None

        def scalar(self, statement):
            self.statement = statement
            return object()

    session = RecordingSession()

    lock_event_scope_case(session, uuid.uuid4())

    assert session.statement._for_update_arg is not None
    assert session.statement.get_final_froms()[0].name == "research_cases"


def test_lifecycle_lock_uses_case_then_lifecycle_rows() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.statements = []

        def scalar(self, statement):
            self.statements.append(statement)
            return object()

    session = RecordingSession()

    lock_event_research_lifecycle(session, uuid.uuid4())

    assert [statement.get_final_froms()[0].name for statement in session.statements] == [
        "research_cases",
        "event_research_lifecycles",
    ]
    assert all(statement._for_update_arg is not None for statement in session.statements)


@pytest.mark.pg_only
def test_postgres_scope_update_waits_for_publish_then_snapshots_confirmed_link(engine) -> None:
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        created = EventResearchService(bootstrap).create(
            CreateEventResearchRequest(
                raw_input="Event input",
                event_title="Concurrent event",
                research_question="What explains the event?",
                candidate_factors=INITIAL_FACTORS,
                created_by="tester",
            )
        )
        case_id = uuid.UUID(created.case_id)
        thesis = bootstrap.scalar(
            select(Thesis).where(
                Thesis.research_case_id == case_id,
                Thesis.statement == INITIAL_FACTORS[0],
            )
        )
        assert thesis is not None
        now = datetime.now(timezone.utc)
        document = DocumentVersion(
            content_sha256=uuid.uuid4().hex,
            source_url="https://investor.tsmc.com/english/quarterly-results",
            available_at=now,
            acquired_at=now,
            parser_version="fixture",
        )
        bootstrap.add(document)
        bootstrap.flush()
        span = SourceSpan(
            document_version_id=document.id,
            verbatim_text="Concurrent reviewed evidence",
            locator={"kind": "fixture"},
        )
        bootstrap.add(span)
        bootstrap.flush()
        statement = SourceStatement(
            source_span_id=span.id,
            kind="fact",
            normalized_text="Concurrent evidence statement",
            created_at=now,
        )
        bootstrap.add(statement)
        bootstrap.commit()
        statement_id = statement.id
        thesis_id = thesis.id
    finally:
        bootstrap.close()

    publish_locked, release_publish, scope_started, scope_lock_attempted, scope_finished = (
        Event(),
        Event(),
        Event(),
        Event(),
        Event(),
    )
    errors: list[BaseException] = []
    published_link_id: list[uuid.UUID] = []
    scope_thread_id: list[int] = []

    def observe_scope_lock(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            scope_thread_id
            and get_ident() == scope_thread_id[0]
            and "research_cases" in statement.lower()
            and "for update" in statement.lower()
        ):
            scope_lock_attempted.set()

    def publish() -> None:
        session = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            # Match the reviewed-evidence publisher: acquire the case root
            # before appending the link, then retain it through commit.
            lock_event_scope_case(session, case_id)
            link = EvidenceLink(
                thesis_id=thesis_id,
                source_statement_id=statement_id,
                role="supports",
                reason="concurrent publish",
                scope={"period": "event"},
                available_at=now,
                creator_type="human",
                review_state="reviewed",
                created_at=now,
            )
            session.add(link)
            session.flush()
            published_link_id.append(link.id)
            publish_locked.set()
            assert release_publish.wait(timeout=5)
            append_current_scope_evidence_assignment(
                session,
                case_id=case_id,
                evidence_link_id=link.id,
                factor_statement=INITIAL_FACTORS[0],
                created_at=now,
            )
            session.commit()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def update_scope() -> None:
        session = SessionLocal()
        try:
            scope_thread_id.append(get_ident())
            scope_started.set()
            EventResearchScopeService(session).update(
                case_id,
                [INITIAL_FACTORS[0], "New factor two", "New factor three"],
                "reviewer",
            )
            session.commit()
            scope_finished.set()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    publisher = Thread(target=publish)
    updater = Thread(target=update_scope)
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_scope_lock)
    try:
        publisher.start()
        assert publish_locked.wait(timeout=5)
        updater.start()
        assert scope_started.wait(timeout=5)
        # The event is emitted immediately before PostgreSQL sends SELECT FOR
        # UPDATE.  Publisher still holds that row, so a completed scope update
        # here would prove the lock was not honored.
        assert scope_lock_attempted.wait(timeout=5)
        assert not scope_finished.is_set()
        release_publish.set()
        publisher.join(timeout=5)
        updater.join(timeout=5)
        assert not publisher.is_alive()
        assert not updater.is_alive()
        assert not errors
    finally:
        release_publish.set()
        publisher.join(timeout=5)
        updater.join(timeout=5)
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_scope_lock)

    verify = SessionLocal()
    try:
        assignment = verify.scalar(
            select(EventResearchScopeEvidenceAssignment)
            .join(
                EventResearchScopeVersion,
                EventResearchScopeVersion.id
                == EventResearchScopeEvidenceAssignment.scope_version_id,
            )
            .where(
                EventResearchScopeEvidenceAssignment.evidence_link_id
                == published_link_id[0],
                EventResearchScopeVersion.research_case_id == case_id,
                EventResearchScopeVersion.version == 2,
            )
        )
        assert assignment is not None
        assert assignment.disposition == "mapped"
    finally:
        verify.close()


@pytest.mark.pg_only
@pytest.mark.parametrize("task_type", ["support", "result"])
def test_postgres_scope_replacement_discards_inflight_old_run_output(
    engine, monkeypatch, task_type
) -> None:
    """A successful provider return cannot outlive a committed replacement."""
    from app.ai.proposal import EvidenceProposer
    from app.ai.assessment_gen import AssessmentGenerator

    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        created = EventResearchService(bootstrap).create(
            CreateEventResearchRequest(
                raw_input="Event input",
                event_title="In-flight replacement",
                research_question="What explains the event?",
                candidate_factors=INITIAL_FACTORS,
                created_by="tester",
            )
        )
        case_id = uuid.UUID(created.case_id)
        old_run_id = uuid.UUID(created.lifecycle.active_run_id)
        old_run = bootstrap.get(ResearchRun, old_run_id)
        assert old_run is not None
        old_run.max_rounds = 1
        tasks = list(
            bootstrap.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == old_run_id)
                .order_by(ResearchTask.created_at)
            )
        )
        target_task = next(task for task in tasks if task.task_type == task_type)
        for task in tasks:
            if task.id != target_task.id:
                task.status = "cancelled"
                task.stage = "stopped"
        bootstrap.commit()
        target_task_id = target_task.id
    finally:
        bootstrap.close()

    provider_entered, allow_provider_return = Event(), Event()
    scope_completed = Event()
    output_slot_checked = Event()
    worker_errors: list[BaseException] = []
    scope_errors: list[BaseException] = []

    def blocked_propose(self, thesis_id, session, *, before_persist=None):
        provider_entered.set()
        if not allow_provider_return.wait(timeout=5):
            raise RuntimeError("test did not release blocked provider")
        if before_persist is None:
            return [uuid.uuid4()]
        output_slot_checked.set()
        if before_persist is not None and not before_persist():
            return []
        proposal = Proposal(
            kind="evidence_link",
            payload={"source_statement_id": str(uuid.uuid4()), "role": "supports", "reason": "late"},
            target_context={"thesis_id": str(thesis_id), "entity_type": "evidence_link"},
            proposed_by_type="ai",
            proposed_by_ref="blocked-test",
            proposed_at=datetime.now(timezone.utc),
            research_case_id=case_id,
        )
        session.add(proposal)
        session.flush()
        return [proposal.id]

    class _SuccessfulAssessment:
        id = uuid.uuid4()
        conclusion = "insufficient_evidence"
        gaps: list[str] = []

    def blocked_assessment(self, thesis_id, cutoff, session, *, before_persist=None):
        provider_entered.set()
        if not allow_provider_return.wait(timeout=5):
            raise RuntimeError("test did not release blocked provider")
        if before_persist is None:
            return _SuccessfulAssessment()
        output_slot_checked.set()
        if not before_persist():
            return None
        raise AssertionError("scope replacement should have cancelled this assessment")

    if task_type == "support":
        monkeypatch.setattr(EvidenceProposer, "propose", blocked_propose)
    else:
        monkeypatch.setattr(AssessmentGenerator, "generate", blocked_assessment)
    monkeypatch.setattr("app.services.auto_research._pending_versions", lambda *_: [])

    def execute_old_run() -> None:
        session = SessionLocal()
        try:
            run = session.get(ResearchRun, old_run_id)
            assert run is not None
            AutoResearchService(session).execute(run)
            session.commit()
        except BaseException as exc:  # surfaced in the test thread
            worker_errors.append(exc)
            session.rollback()
        finally:
            session.close()

    worker = Thread(target=execute_old_run)
    worker.start()
    assert provider_entered.wait(timeout=5)

    def replace_scope() -> None:
        replacement = SessionLocal()
        try:
            EventResearchScopeService(replacement).update(
                case_id,
                [INITIAL_FACTORS[0], "New factor two", "New factor three"],
                "reviewer",
            )
            replacement.commit()
            scope_completed.set()
        except BaseException as exc:  # surfaced in the test thread
            scope_errors.append(exc)
            replacement.rollback()
        finally:
            replacement.close()

    replacement_worker = Thread(target=replace_scope)
    replacement_worker.start()
    try:
        assert scope_completed.wait(timeout=5), (
            "scope replacement must commit while provider call is blocked"
        )
        assert not scope_errors
    finally:
        allow_provider_return.set()
        replacement_worker.join(timeout=5)
        worker.join(timeout=5)
    assert not replacement_worker.is_alive()
    assert not worker.is_alive()
    assert not worker_errors
    assert output_slot_checked.is_set()

    verify = SessionLocal()
    try:
        old_run = verify.get(ResearchRun, old_run_id)
        old_task = verify.get(ResearchTask, target_task_id)
        lifecycle = verify.get(EventResearchLifecycle, case_id)
        assert old_run is not None and old_run.status == "cancelled"
        assert old_task is not None and old_task.status == "cancelled"
        assert old_task.result is None
        old_job = verify.scalar(
            select(Job).where(
                Job.target_type == "research_run", Job.target_id == old_run_id
            )
        )
        assert old_job is not None and old_job.status == "cancelled"
        assert old_job.cancel_requested is True
        assert verify.scalar(
            select(Proposal.id).where(Proposal.research_case_id == case_id)
        ) is None
        assert verify.scalar(
            select(AIAssessment.id)
            .join(EvidenceSnapshot, EvidenceSnapshot.id == AIAssessment.snapshot_id)
            .join(Thesis, Thesis.id == EvidenceSnapshot.thesis_id)
            .where(Thesis.research_case_id == case_id)
        ) is None
        assert lifecycle is not None
        assert lifecycle.status == "continuing"
        assert lifecycle.active_run_id != old_run_id
    finally:
        verify.close()


@pytest.mark.pg_only
def test_postgres_scope_update_serializes_draft_snapshot(engine, monkeypatch) -> None:
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        created = EventResearchService(bootstrap).create(
            CreateEventResearchRequest(
                raw_input="Event input",
                event_title="Draft snapshot concurrency",
                research_question="What explains the event?",
                candidate_factors=INITIAL_FACTORS,
                created_by="tester",
            )
        )
        case_id = uuid.UUID(created.case_id)
        lifecycle = bootstrap.get(EventResearchLifecycle, case_id)
        assert lifecycle is not None
        lifecycle.status = "draft_ready"
        _cover_current_scope(bootstrap, case_id)
        bootstrap.commit()
    finally:
        bootstrap.close()

    scope_has_lock, release_scope = Event(), Event()
    draft_lock_attempted, draft_finished = Event(), Event()
    errors: list[BaseException] = []
    draft_ids: list[uuid.UUID] = []
    draft_thread_id: list[int] = []
    original_continue = EventResearchScopeService._continue_research_if_needed

    def pause_scope(service, lifecycle, active_theses, now) -> None:
        scope_has_lock.set()
        assert release_scope.wait(timeout=5)
        original_continue(service, lifecycle, active_theses, now)

    def observe_draft_lock(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            draft_thread_id
            and get_ident() == draft_thread_id[0]
            and "research_cases" in statement.lower()
            and "for update" in statement.lower()
        ):
            draft_lock_attempted.set()

    monkeypatch.setattr(
        EventResearchScopeService, "_continue_research_if_needed", pause_scope
    )

    def update_scope() -> None:
        session = SessionLocal()
        try:
            EventResearchScopeService(session).update(
                case_id,
                INITIAL_FACTORS,
                "reviewer",
            )
            session.commit()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def create_draft() -> None:
        session = SessionLocal()
        try:
            draft_thread_id.append(get_ident())
            draft = EventConclusionService(session).create_draft(case_id)
            draft_ids.append(draft.id)
            session.commit()
            draft_finished.set()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    scope_thread = Thread(target=update_scope)
    draft_thread = Thread(target=create_draft)
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_draft_lock)
    try:
        scope_thread.start()
        assert scope_has_lock.wait(timeout=5)
        draft_thread.start()
        assert draft_lock_attempted.wait(timeout=5)
        assert not draft_finished.is_set()
        release_scope.set()
        scope_thread.join(timeout=5)
        draft_thread.join(timeout=5)
        assert not scope_thread.is_alive()
        assert not draft_thread.is_alive()
        assert not errors
    finally:
        release_scope.set()
        scope_thread.join(timeout=5)
        draft_thread.join(timeout=5)
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_draft_lock)

    verify = SessionLocal()
    try:
        draft = verify.get(EventResearchConclusion, draft_ids[0])
        assert draft is not None
        scope = verify.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .where(EventResearchScopeVersion.version == 2)
        )
        assert scope is not None
        assert draft.scope_version_id == scope.id
    finally:
        verify.close()


@pytest.mark.pg_only
def test_postgres_scope_update_invalidates_interleaved_stale_conclusion_publish(
    engine, monkeypatch
) -> None:
    """A scope change rejects a draft that was queued to publish concurrently."""
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        created = EventResearchService(bootstrap).create(
            CreateEventResearchRequest(
                raw_input="Event input",
                event_title="Conclusion concurrency",
                research_question="What explains the event?",
                candidate_factors=INITIAL_FACTORS,
                created_by="tester",
            )
        )
        case_id = uuid.UUID(created.case_id)
        lifecycle = bootstrap.get(EventResearchLifecycle, case_id)
        assert lifecycle is not None
        lifecycle.status = "draft_ready"
        lifecycle.current_gap = "Need updated factors"
        lifecycle.next_human_action = "Update factors"
        _cover_current_scope(bootstrap, case_id)
        EventConclusionService(bootstrap).create_draft(case_id)
        bootstrap.commit()
    finally:
        bootstrap.close()

    scope_has_lifecycle, allow_scope_continue = Event(), Event()
    publish_lock_attempted, stale_publish_rejected = Event(), Event()
    errors: list[BaseException] = []
    publisher_thread_id: list[int] = []
    original_continue = EventResearchScopeService._continue_research_if_needed

    def pause_scope_lifecycle_write(service, lifecycle, active_theses, now) -> None:
        scope_has_lifecycle.set()
        assert allow_scope_continue.wait(timeout=5)
        original_continue(service, lifecycle, active_theses, now)

    def observe_publish_lock(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            publisher_thread_id
            and get_ident() == publisher_thread_id[0]
            and "research_cases" in statement.lower()
            and "for update" in statement.lower()
        ):
            publish_lock_attempted.set()

    monkeypatch.setattr(
        EventResearchScopeService,
        "_continue_research_if_needed",
        pause_scope_lifecycle_write,
    )

    def update_scope() -> None:
        session = SessionLocal()
        try:
            EventResearchScopeService(session).update(
                case_id,
                [INITIAL_FACTORS[0], "New factor two", "New factor three"],
                "reviewer",
            )
            session.commit()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def publish_conclusion() -> None:
        session = SessionLocal()
        try:
            publisher_thread_id.append(get_ident())
            EventConclusionService(session).publish(
                case_id,
                text="Human-reviewed conclusion",
                reviewer="reviewer",
            )
            session.commit()
        except ValidationFailedError:
            stale_publish_rejected.set()
            session.rollback()
        except BaseException as exc:  # surfaced in the test thread
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    scope_thread = Thread(target=update_scope)
    publish_thread = Thread(target=publish_conclusion)
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_publish_lock)
    try:
        scope_thread.start()
        assert scope_has_lifecycle.wait(timeout=5)
        publish_thread.start()
        # Publish reached the same root-lock request but cannot pass the scope
        # transaction until the test releases its scope projection.
        assert publish_lock_attempted.wait(timeout=5)
        assert not stale_publish_rejected.is_set()
        allow_scope_continue.set()
        scope_thread.join(timeout=5)
        publish_thread.join(timeout=5)
        assert not scope_thread.is_alive()
        assert not publish_thread.is_alive()
        assert not errors
        assert stale_publish_rejected.is_set()
    finally:
        allow_scope_continue.set()
        scope_thread.join(timeout=5)
        publish_thread.join(timeout=5)
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_publish_lock)

    verify = SessionLocal()
    try:
        lifecycle = verify.get(EventResearchLifecycle, case_id)
        assert lifecycle is not None
        assert lifecycle.status == "draft_ready"
        assert verify.scalar(
            select(EventResearchConclusion.id).where(
                EventResearchConclusion.research_case_id == case_id,
                EventResearchConclusion.state == "published",
            )
        ) is None
    finally:
        verify.close()


def test_scope_update_appends_v2_without_rewriting_v1(cmd_client, cmd_session) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    updated_factors = [
        INITIAL_FACTORS[0],
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": updated_factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "version": 2,
        "factors": [{"statement": statement, "description": None} for statement in updated_factors],
        "reclassified_evidence_count": 0,
        "unmapped_evidence_count": 0,
    }
    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assert [version.version for version in versions] == [1, 2]
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS
    assert _scope_statements(cmd_session, versions[1].id) == updated_factors


def test_scope_service_backfills_legacy_drafts_before_appending_an_update(cmd_session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="Legacy event",
        industry_topic="事件研究",
        created_by="legacy-author",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    cmd_session.add(
        EventResearchBrief(
            research_case_id=case.id,
            raw_input="legacy event input",
            source_url=None,
            event_title="Legacy event",
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            research_question="What explains the event?",
            extraction_state="human_confirmed",
            created_at=now,
        )
    )
    for position, statement in enumerate(INITIAL_FACTORS, start=1):
        cmd_session.add(
            EventResearchFactorDraft(
                research_case_id=case.id,
                statement=statement,
                position=position,
                created_by="legacy-author",
                created_at=now,
            )
        )
    cmd_session.commit()

    updated = EventResearchScopeService(cmd_session).update(
        case.id,
        [INITIAL_FACTORS[0], INITIAL_FACTORS[2], "New legacy scope factor"],
        "reviewer",
    )
    cmd_session.commit()

    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case.id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assert updated.version == 2
    assert [version.version for version in versions] == [1, 2]
    assert _scope_statements(cmd_session, versions[0].id) == INITIAL_FACTORS


def test_legacy_run_scope_falls_back_to_its_own_tasks_not_latest_scope(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    legacy_thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == INITIAL_FACTORS[1],
        )
    )
    assert legacy_thesis is not None
    now = datetime.now(timezone.utc)
    legacy_run = ResearchRun(
        research_case_id=case_id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        stop_reason=None,
        scope_thesis_ids=None,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(legacy_run)
    cmd_session.flush()
    cmd_session.add(
        ResearchTask(
            run_id=legacy_run.id,
            research_case_id=case_id,
            thesis_id=legacy_thesis.id,
            task_type="support",
            query="legacy task",
            created_at=now,
            updated_at=now,
        )
    )
    cmd_session.commit()
    update = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                INITIAL_FACTORS[2],
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    assert update.status_code == 200

    assert AutoResearchService(cmd_session)._run_thesis_ids(legacy_run) == [legacy_thesis.id]


def test_scope_update_keeps_removed_factor_evidence_and_reports_mapping_counts(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    retained_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    removed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[1])

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                INITIAL_FACTORS[2],
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )

    assert response.status_code == 200
    assert response.json()["reclassified_evidence_count"] == 1
    assert response.json()["unmapped_evidence_count"] == 1
    assert cmd_session.get(EvidenceLink, retained_link.id) is not None
    assert cmd_session.get(EvidenceLink, removed_link.id) is not None
    assert cmd_session.get(EvidenceLink, removed_link.id).review_state == "reviewed"


def test_publish_resume_after_scope_snapshot_keeps_one_latest_assignment(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                "广告业务增长弱于市场预期",
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    assert response.status_code == 200
    # Simulate a publisher that had written the formal link before waiting on
    # the case lock and only now resumes its idempotent assignment append.
    append_current_scope_evidence_assignment(
        cmd_session,
        case_id=case_id,
        evidence_link_id=link.id,
        factor_statement=INITIAL_FACTORS[0],
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.commit()

    assignments = list(
        cmd_session.scalars(
            select(EventResearchScopeEvidenceAssignment)
            .join(
                EventResearchScopeVersion,
                EventResearchScopeVersion.id
                == EventResearchScopeEvidenceAssignment.scope_version_id,
            )
            .where(
                EventResearchScopeEvidenceAssignment.evidence_link_id == link.id,
                EventResearchScopeVersion.research_case_id == case_id,
                EventResearchScopeVersion.version == 2,
            )
        )
    )
    assert len(assignments) == 1
    assert assignments[0].disposition == "mapped"


def test_direct_assignment_append_takes_the_case_lifecycle_lock(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    lock_calls: list[uuid.UUID] = []

    def record_lifecycle_lock(session, locked_case_id):
        lock_calls.append(locked_case_id)
        return session.get(EventResearchLifecycle, locked_case_id)

    monkeypatch.setattr(
        "app.services.event_research_scope_evidence.lock_event_research_lifecycle",
        record_lifecycle_lock,
    )

    append_current_scope_evidence_assignment(
        cmd_session,
        case_id=case_id,
        evidence_link_id=link.id,
        factor_statement=INITIAL_FACTORS[0],
        created_at=datetime.now(timezone.utc),
    )

    assert lock_calls == [case_id]


def test_published_event_rejects_scope_update_without_starting_successor(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = "draft_ready"
    _cover_current_scope(cmd_session, case_id)
    EventConclusionService(cmd_session).create_draft(case_id)
    EventConclusionService(cmd_session).publish(
        case_id,
        text="Human-reviewed conclusion",
        reviewer="reviewer",
    )
    cmd_session.commit()
    before_versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion).where(
                EventResearchScopeVersion.research_case_id == case_id
            )
        )
    )
    before_runs = list(
        cmd_session.scalars(
            select(ResearchRun).where(ResearchRun.research_case_id == case_id)
        )
    )

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                "广告业务增长弱于市场预期",
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert len(
        list(
            cmd_session.scalars(
                select(EventResearchScopeVersion).where(
                    EventResearchScopeVersion.research_case_id == case_id
                )
            )
        )
    ) == len(before_versions)
    assert len(
        list(
            cmd_session.scalars(
                select(ResearchRun).where(ResearchRun.research_case_id == case_id)
            )
        )
    ) == len(before_runs)
    assert cmd_session.get(EventResearchLifecycle, case_id).status == "published"


def test_scope_updates_append_auditable_evidence_assignments_and_refresh_lifecycle(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    retained_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    removed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[1])

    second = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                INITIAL_FACTORS[2],
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    third = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[1],
                INITIAL_FACTORS[2],
                "广告业务增长弱于市场预期",
            ],
            "changed_by": "reviewer",
        },
    )

    assert second.status_code == 200
    assert third.status_code == 200
    versions = list(
        cmd_session.scalars(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version)
        )
    )
    assignments = list(
        cmd_session.scalars(
            select(EventResearchScopeEvidenceAssignment)
            .where(
                EventResearchScopeEvidenceAssignment.scope_version_id.in_(
                    [versions[1].id, versions[2].id]
                )
            )
            .order_by(
                EventResearchScopeEvidenceAssignment.scope_version_id,
                EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
        )
    )
    by_scope = {
        scope_id: {
            assignment.evidence_link_id: (
                assignment.factor_statement,
                assignment.disposition,
            )
            for assignment in assignments
            if assignment.scope_version_id == scope_id
        }
        for scope_id in [versions[1].id, versions[2].id]
    }
    assert by_scope[versions[1].id] == {
        retained_link.id: (INITIAL_FACTORS[0], "mapped"),
        removed_link.id: (None, "unmapped"),
    }
    assert by_scope[versions[2].id] == {
        retained_link.id: (None, "unmapped"),
        removed_link.id: (INITIAL_FACTORS[1], "mapped"),
    }
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle.status == "continuing"
    assert lifecycle.status_summary == "已更新因素，正在重新归类证据并继续检索"
    assert lifecycle.current_gap == "已更新因素，正在重新归类证据"
    assert lifecycle.next_human_action is None


@pytest.mark.parametrize("paused_status", ["awaiting_scope", "exhausted"])
def test_scope_update_resumes_research_with_current_scope_factors_only(
    cmd_client, cmd_session, paused_status: str
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    initial_run_id = _start_case_run(cmd_client, cmd_session, case_id)
    reviewed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = paused_status
    lifecycle.current_gap = "需要调整研究范围"
    lifecycle.next_human_action = "补充来源或调整研究范围"
    cmd_session.commit()
    active_factors = [
        INITIAL_FACTORS[0],
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": active_factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 200
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle.status == "continuing"
    assert lifecycle.next_human_action is None
    assert lifecycle.active_run_id is not None
    assert lifecycle.active_run_id != initial_run_id
    successor = cmd_session.get(ResearchRun, lifecycle.active_run_id)
    assert successor is not None
    assert successor.research_case_id == case_id
    task_statements = set(
        cmd_session.scalars(
            select(Thesis.statement)
            .join(ResearchTask, ResearchTask.thesis_id == Thesis.id)
            .where(ResearchTask.run_id == successor.id)
        )
    )
    assert task_statements == set(active_factors)
    assert set(successor.scope_thesis_ids) == {
        str(thesis_id)
        for thesis_id in cmd_session.scalars(
            select(Thesis.id).where(
                Thesis.research_case_id == case_id,
                Thesis.statement.in_(active_factors),
            )
        )
    }
    AutoResearchService(cmd_session)._create_balance_gaps(successor, current_round=1)
    cmd_session.flush()
    all_successor_task_statements = set(
        cmd_session.scalars(
            select(Thesis.statement)
            .join(ResearchTask, ResearchTask.thesis_id == Thesis.id)
            .where(ResearchTask.run_id == successor.id)
        )
    )
    assert all_successor_task_statements == set(active_factors)
    successor.status = "waiting_for_review"
    successor.stop_reason = "max_rounds_reached"
    AutoResearchService(cmd_session).refresh_event_lifecycle(successor)
    next_successor = cmd_session.get(
        ResearchRun, cmd_session.get(EventResearchLifecycle, case_id).active_run_id
    )
    assert next_successor is not None
    assert next_successor.id != successor.id
    assert set(
        cmd_session.scalars(
            select(Thesis.statement)
            .join(ResearchTask, ResearchTask.thesis_id == Thesis.id)
            .where(ResearchTask.run_id == next_successor.id)
        )
    ) == set(active_factors)
    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
    assert workbench.status_code == 200
    assert [item["statement"] for item in workbench.json()["factors"]] == active_factors
    assert cmd_session.get(EvidenceLink, reviewed_link.id) is not None
    assert cmd_session.scalar(
        select(EventResearchConclusion.id).where(
            EventResearchConclusion.research_case_id == case_id
        )
    ) is None
    assert workbench.json()["conclusion"]["state"] == "cannot_conclude"


@pytest.mark.parametrize("old_status", ["queued", "running"])
def test_scope_update_replaces_an_active_run_with_latest_factor_successor(
    cmd_client, cmd_session, old_status: str
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    old_run_id = _start_case_run(cmd_client, cmd_session, case_id)
    old_run = cmd_session.get(ResearchRun, old_run_id)
    assert old_run is not None
    old_run.status = old_status
    old_task_ids = list(
        cmd_session.scalars(
            select(ResearchTask.id).where(ResearchTask.run_id == old_run_id)
        )
    )
    if old_status == "running":
        running_task = cmd_session.get(ResearchTask, old_task_ids[0])
        assert running_task is not None
        running_task.status = "running"
        running_task.stage = "research"
    cmd_session.commit()
    latest_factors = [
        INITIAL_FACTORS[0],
        "广告业务增长弱于市场预期",
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": latest_factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 200
    cmd_session.refresh(old_run)
    assert old_run.status == "cancelled"
    assert old_run.stop_reason == "cancelled"
    old_job = cmd_session.scalar(
        select(Job).where(Job.target_type == "research_run", Job.target_id == old_run_id)
    )
    assert old_job is not None
    assert old_job.status == "cancelled"
    assert old_job.cancel_requested is True
    old_tasks = list(
        cmd_session.scalars(
            select(ResearchTask).where(ResearchTask.id.in_(old_task_ids))
        )
    )
    assert old_tasks
    assert {task.status for task in old_tasks} == {"cancelled"}

    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    assert lifecycle.status == "continuing"
    assert lifecycle.active_run_id != old_run_id
    successor = cmd_session.get(ResearchRun, lifecycle.active_run_id)
    assert successor is not None
    successor_task_factors = set(
        cmd_session.scalars(
            select(Thesis.statement)
            .join(ResearchTask, ResearchTask.thesis_id == Thesis.id)
            .where(ResearchTask.run_id == successor.id)
        )
    )
    assert successor_task_factors == set(latest_factors)
    assert INITIAL_FACTORS[1] not in successor_task_factors

    old_run.status = "waiting_for_review"
    old_run.stop_reason = "max_rounds_reached"
    AutoResearchService(cmd_session).refresh_event_lifecycle(old_run)
    assert cmd_session.get(EventResearchLifecycle, case_id).active_run_id == successor.id


def test_scope_update_makes_removed_factor_pending_proposal_non_actionable(session) -> None:
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Event input",
            event_title="Remove stale review",
            research_question="What explains the event?",
            candidate_factors=INITIAL_FACTORS,
            created_by="tester",
        )
    )
    case_id = uuid.UUID(created.case_id)
    removed_thesis = session.scalar(
        select(Thesis)
        .where(Thesis.research_case_id == case_id)
        .where(Thesis.statement == INITIAL_FACTORS[0])
    )
    assert removed_thesis is not None
    now = datetime.now(timezone.utc)
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        title="Admissible release",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    session.add(document)
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=case_id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="Source evidence for a factor removed from the next scope.",
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="The source supports the original factor.",
        created_at=now,
    )
    session.add(statement)
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": "pending before the scope changed",
            "scope": {"period": "event"},
        },
        target_context={"thesis_id": str(removed_thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=now,
        research_case_id=case_id,
        status="pending",
    )
    session.add(proposal)
    session.flush()
    task = TaskRepository(session).add_task(
        title="Review stale factor evidence",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=proposal.id,
        research_case_id=case_id,
    )
    session.commit()

    EventResearchScopeService(session).update(
        case_id,
        [INITIAL_FACTORS[1], "New factor two", "New factor three"],
        "reviewer",
    )
    session.commit()
    session.refresh(task)
    assert task.status == "done"

    lifecycle = session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    successor = session.get(ResearchRun, lifecycle.active_run_id)
    assert successor is not None
    successor.status = "waiting_for_review"
    successor.stage = "stopped"
    successor.stop_reason = "max_rounds_reached"
    successor.max_rounds = 1
    session.commit()
    AutoResearchService(session).refresh_event_lifecycle(successor)
    session.commit()

    session.refresh(task)
    session.refresh(lifecycle)
    assert task.status == "done"
    assert proposal.status == "pending"
    assert session.scalar(
        select(DomainEvent.id).where(
            DomainEvent.type == "event_evidence_out_of_scope",
            DomainEvent.aggregate_id == str(proposal.id),
        )
    ) is not None
    assert EventResearchLifecycleRepository(session).pending_key_review_count(case_id) == 0
    queue = EventReviewQueueService(session).review_queue(case_id)
    assert queue.items == []
    assert queue.summary.pending == 0
    assert lifecycle.status != "awaiting_key_review"

def test_scope_assignments_exclude_removed_evidence_from_draft_and_citations(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    active_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    removed_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[1])
    active_factors = [
        INITIAL_FACTORS[0],
        INITIAL_FACTORS[2],
        "AI 投入回报周期可能拉长",
    ]

    response = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={"factors": active_factors, "changed_by": "reviewer"},
    )
    assert response.status_code == 200
    _cover_current_scope(cmd_session, case_id)
    draft = EventConclusionService(cmd_session).create_draft(case_id)
    cmd_session.commit()

    assert str(removed_link.id) not in draft.evidence_link_ids
    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
    assert workbench.status_code == 200
    assert INITIAL_FACTORS[1] not in [
        item["factor_statement"] for item in workbench.json()["conclusion"]["citations"]
    ]


@pytest.mark.parametrize(
    ("evidence_factors", "expects_draft"),
    [([], False), ([INITIAL_FACTORS[0]], False), (INITIAL_FACTORS, True)],
    ids=["no-evidence", "one-factor-only", "all-active-factors"],
)
def test_final_key_review_requires_mapped_evidence_for_every_active_factor(
    cmd_client, cmd_session, evidence_factors: list[str], expects_draft: bool
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "awaiting_key_review"
    lifecycle.current_round = 3
    for factor in evidence_factors:
        link = _reviewed_evidence(cmd_session, case_id, factor)
        append_current_scope_evidence_assignment(
            cmd_session,
            case_id=case_id,
            evidence_link_id=link.id,
            factor_statement=factor,
            created_at=datetime.now(timezone.utc),
        )
    cmd_session.commit()

    AutoResearchService(cmd_session).continue_after_key_review(case_id)
    cmd_session.commit()

    drafts = list(
        cmd_session.scalars(
            select(EventResearchConclusion).where(
                EventResearchConclusion.research_case_id == case_id,
                EventResearchConclusion.state == "ai_draft",
            )
        )
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    if expects_draft:
        assert lifecycle.status == "draft_ready"
        assert len(drafts) == 1
    else:
        assert lifecycle.status == "exhausted"
        assert drafts == []


def test_final_key_review_requires_two_links_even_for_one_active_factor(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    now = datetime.now(timezone.utc)
    one_factor_scope = EventResearchScopeVersion(
        research_case_id=case_id,
        version=2,
        changed_by="tester",
        change_summary="threshold fixture",
        created_at=now,
    )
    cmd_session.add(one_factor_scope)
    cmd_session.flush()
    cmd_session.add(
        EventResearchScopeFactor(
            scope_version_id=one_factor_scope.id,
            statement=INITIAL_FACTORS[0],
            position=1,
        )
    )
    cmd_session.commit()
    link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    append_current_scope_evidence_assignment(
        cmd_session,
        case_id=case_id,
        evidence_link_id=link.id,
        factor_statement=INITIAL_FACTORS[0],
        created_at=now,
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "awaiting_key_review"
    lifecycle.current_round = 3
    cmd_session.commit()

    with pytest.raises(ValidationFailedError):
        EventConclusionService(cmd_session).create_draft(case_id)

    AutoResearchService(cmd_session).continue_after_key_review(case_id)
    cmd_session.commit()

    assert cmd_session.get(EventResearchLifecycle, case_id).status == "exhausted"
    assert cmd_session.scalar(
        select(EventResearchConclusion.id).where(
            EventResearchConclusion.research_case_id == case_id,
            EventResearchConclusion.state == "ai_draft",
        )
    ) is None


def test_create_draft_rejects_insufficient_current_scope_coverage(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    append_current_scope_evidence_assignment(
        cmd_session,
        case_id=case_id,
        evidence_link_id=link.id,
        factor_statement=INITIAL_FACTORS[0],
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationFailedError):
        EventConclusionService(cmd_session).create_draft(case_id)

    assert cmd_session.scalar(
        select(EventResearchConclusion.id).where(
            EventResearchConclusion.research_case_id == case_id,
            EventResearchConclusion.state == "ai_draft",
        )
    ) is None


def test_published_workbench_citations_use_conclusion_evidence_snapshot(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    assert lifecycle is not None
    lifecycle.status = "draft_ready"
    _cover_current_scope(cmd_session, case_id)
    EventConclusionService(cmd_session).create_draft(case_id)
    EventConclusionService(cmd_session).publish(
        case_id,
        text="Published with one citation",
        reviewer="reviewer",
    )
    later_link = _reviewed_evidence(cmd_session, case_id, INITIAL_FACTORS[0])
    append_current_scope_evidence_assignment(
        cmd_session,
        case_id=case_id,
        evidence_link_id=later_link.id,
        factor_statement=INITIAL_FACTORS[0],
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.commit()

    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")

    assert workbench.status_code == 200
    citation_factors = [
        citation["factor_statement"] for citation in workbench.json()["conclusion"]["citations"]
    ]
    assert citation_factors.count(INITIAL_FACTORS[0]) == 1
    assert set(citation_factors) == set(INITIAL_FACTORS)


def test_conclusion_publish_takes_the_case_lifecycle_lock(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = "draft_ready"
    _cover_current_scope(cmd_session, case_id)
    EventConclusionService(cmd_session).create_draft(case_id)
    cmd_session.commit()
    calls: list[uuid.UUID] = []

    def record_lock(session, locked_case_id):
        calls.append(locked_case_id)
        return session.get(EventResearchLifecycle, locked_case_id)

    monkeypatch.setattr(
        "app.services.event_conclusion.lock_event_research_lifecycle", record_lock
    )

    published = EventConclusionService(cmd_session).publish(
        case_id,
        text="Human-reviewed conclusion",
        reviewer="reviewer",
    )

    assert calls == [case_id]
    assert published.state == "published"


def test_conclusion_draft_takes_the_case_lifecycle_lock(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    calls: list[uuid.UUID] = []

    def record_lock(session, locked_case_id):
        calls.append(locked_case_id)
        return session.get(EventResearchLifecycle, locked_case_id)

    monkeypatch.setattr(
        "app.services.event_conclusion.lock_event_research_lifecycle", record_lock
    )

    _cover_current_scope(cmd_session, case_id)
    draft = EventConclusionService(cmd_session).create_draft(case_id)

    assert calls == [case_id]
    assert draft.scope_version_id is not None


def test_scope_change_blocks_stale_draft_but_current_scope_draft_can_publish(
    cmd_client, cmd_session
) -> None:
    created = _create_event(cmd_client)
    case_id = uuid.UUID(created["case_id"])
    lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    lifecycle.status = "draft_ready"
    _cover_current_scope(cmd_session, case_id)
    v1_draft = EventConclusionService(cmd_session).create_draft(case_id)
    cmd_session.commit()
    assert v1_draft.scope_version_id is not None

    scope = cmd_client.put(
        f"/api/v1/event-research/{case_id}/scope",
        json={
            "factors": [
                INITIAL_FACTORS[0],
                "广告业务增长弱于市场预期",
                "AI 投入回报周期可能拉长",
            ],
            "changed_by": "reviewer",
        },
    )
    assert scope.status_code == 200
    stale_view = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench")
    assert stale_view.status_code == 200
    assert stale_view.json()["conclusion"]["state"] == "cannot_conclude"

    stale_publish = cmd_client.post(
        f"/api/v1/event-research/{case_id}/conclusion/publish",
        json={"text": "stale draft", "reviewer": "reviewer"},
    )
    assert stale_publish.status_code == 422
    assert cmd_session.get(EventResearchConclusion, v1_draft.id) is not None
    assert cmd_session.scalar(
        select(EventResearchConclusion.id).where(
            EventResearchConclusion.research_case_id == case_id,
            EventResearchConclusion.state == "published",
        )
    ) is None

    _cover_current_scope(cmd_session, case_id)
    v2_draft = EventConclusionService(cmd_session).create_draft(case_id)
    cmd_session.commit()
    assert v2_draft.scope_version_id != v1_draft.scope_version_id
    current_lifecycle = cmd_session.get(EventResearchLifecycle, case_id)
    current_lifecycle.status = "draft_ready"
    cmd_session.commit()

    current_publish = cmd_client.post(
        f"/api/v1/event-research/{case_id}/conclusion/publish",
        json={"text": "current draft", "reviewer": "reviewer"},
    )
    assert current_publish.status_code == 201
    published = cmd_session.get(
        EventResearchConclusion, uuid.UUID(current_publish.json()["conclusion_id"])
    )
    assert published is not None
    assert published.based_on_conclusion_id == v2_draft.id
    assert published.scope_version_id == v2_draft.scope_version_id


@pytest.mark.parametrize(
    "factors",
    [
        INITIAL_FACTORS[:2],
        INITIAL_FACTORS + ["第四项", "第五项", "第六项"],
        [INITIAL_FACTORS[0], "   ", INITIAL_FACTORS[2]],
        [INITIAL_FACTORS[0], f" {INITIAL_FACTORS[0]} ", INITIAL_FACTORS[2]],
    ],
)
def test_scope_update_rejects_invalid_factor_sets(cmd_client, factors: list[str]) -> None:
    created = _create_event(cmd_client)

    response = cmd_client.put(
        f"/api/v1/event-research/{created['case_id']}/scope",
        json={"factors": factors, "changed_by": "reviewer"},
    )

    assert response.status_code == 422


def test_scope_update_rejects_changed_by_longer_than_128_characters(cmd_client) -> None:
    created = _create_event(cmd_client)

    response = cmd_client.put(
        f"/api/v1/event-research/{created['case_id']}/scope",
        json={"factors": INITIAL_FACTORS, "changed_by": "x" * 129},
    )

    assert response.status_code == 422


def test_scope_update_returns_existing_not_found_response_for_unknown_case(cmd_client) -> None:
    response = cmd_client.put(
        f"/api/v1/event-research/{uuid.uuid4()}/scope",
        json={"factors": INITIAL_FACTORS, "changed_by": "reviewer"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
