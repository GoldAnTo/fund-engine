"""Locked, idempotent event-research orchestration state ownership."""
from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from math import inf, nan

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.domain.coverage_decision import (
    BOUNDARY_DECISION_ALTERNATIVES,
    BOUNDARY_DECISION_REASON,
    BOUNDARY_DECISION_RECOMMENDATION,
)
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models.event_research import (
    EventResearchConclusion,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.events import DomainEvent
from app.models.ledger import Base, CaseTenantAdmission, ResearchCase, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_orchestration import (
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.repositories.auto_research import AutoResearchRepository
from app.repositories.documents import DocumentRepository
from app.repositories.research_orchestration import (
    LEGAL_TRANSITIONS,
    OrchestrationTransitionCommand,
    ResearchOrchestrationRepository,
)
from app.services.auto_research import AutoResearchService
from app.services.ingest import DocumentService
from app.services.research_orchestration import (
    AcquisitionProgressSnapshot,
    OrchestrationPrincipal,
    ResearchOrchestrationService,
    ScopeDecision,
)


NOW = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
TENANT = "tenant-alpha"
PRINCIPAL = OrchestrationPrincipal(tenant_id=TENANT, actor="human:alice")


def _decision_context() -> dict:
    return {
        "action": "revise_frozen_research_boundary",
        "attempted_rounds": 2,
        "boundary_codes": ["source_policy"],
        "affected_goals": [
            {
                "goal_id": "goal-authority",
                "reason_codes": ["authority_requirement_unmet"],
            }
        ],
        "missing": [
            {
                "goal_id": "goal-authority",
                "reason_codes": ["authority_requirement_unmet"],
            },
            {
                "goal_id": "goal-contrary",
                "reason_codes": ["search_not_successfully_completed"],
            },
        ],
        "attempted": [
            {"goal_id": "goal-authority", "search_completed": True},
            {"goal_id": "goal-contrary", "search_completed": False},
        ],
        "cannot_continue_reason": BOUNDARY_DECISION_REASON,
        "recommendation": dict(BOUNDARY_DECISION_RECOMMENDATION),
        "alternatives": [dict(item) for item in BOUNDARY_DECISION_ALTERNATIVES],
    }


def _seed_case(session, *, tenant_id: str = TENANT):
    document = DocumentService(DocumentRepository(session)).freeze(
        raw=f"source-{uuid.uuid4()}".encode(),
        source_url=f"https://example.test/{uuid.uuid4()}",
    )
    case = ResearchCase(
        title="Event research",
        industry_topic="semiconductors",
        created_at=NOW,
        created_by="human:alice",
    )
    session.add(case)
    session.flush()
    session.add(
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id=tenant_id,
            initial_document_version_id=document.id,
            admitted_by="human:alice",
            admitted_at=NOW,
        )
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="human:alice",
        change_summary="initial scope",
        created_at=NOW,
    )
    session.add(scope)
    session.flush()
    statement = f"Active scope thesis {case.id}"
    session.add_all(
        [
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
                created_by="human:alice",
                created_at=NOW,
            ),
        ]
    )
    session.add(
        EventResearchLifecycle(
            research_case_id=case.id,
            status="awaiting_key_review",
            active_run_id=None,
            current_round=2,
            status_summary="Awaiting scope confirmation",
            current_gap="Scope not confirmed",
            next_human_action="Confirm scope",
            updated_at=NOW,
        )
    )
    session.commit()
    return case, scope


def _event_count(session, orchestration_id) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ResearchOrchestrationEvent)
        .where(ResearchOrchestrationEvent.orchestration_id == orchestration_id)
    )


def _outbox_count(session, orchestration_id) -> int:
    return session.scalar(
        select(func.count())
        .select_from(DomainEvent)
        .where(
            DomainEvent.aggregate_type == "research_orchestration",
            DomainEvent.aggregate_id == str(orchestration_id),
        )
    )


def _seed_orchestration(
    session,
    case,
    scope,
    *,
    state: str,
    user_stage: str,
    heartbeat: datetime | None = NOW,
    version: int = 0,
    checkpoint: dict | None = None,
):
    next_kind = "scope_decision" if state == "needs_scope_decision" else None
    next_label = "Choose whether to retry this scope" if next_kind else None
    orchestration = ResearchOrchestration(
        tenant_id=TENANT,
        research_case_id=case.id,
        current_scope_version_id=scope.id,
        state=state,
        user_stage=user_stage,
        current_system_action="Waiting for a decision",
        system_action_reason="Coverage remains incomplete",
        checkpoint_json=checkpoint or {},
        next_action_kind=next_kind,
        next_action_label=next_label,
        next_action_payload=_decision_context()
        if next_kind
        else None,
        last_heartbeat_at=heartbeat,
        recovery_status="stale" if state == "retry_wait" else "healthy",
        version=version,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(orchestration)
    session.commit()
    return orchestration


def _transition_command(
    orchestration: ResearchOrchestration,
    *,
    expected_version: int | None = None,
    target_state: str,
    user_stage: str = "acquisition",
    action: str = "Advancing workflow",
    reason: str = "The previous stage completed",
    checkpoint: dict | None = None,
    recovery_status: str | None = "healthy",
    next_action_kind: str | None = None,
    next_action_label: str | None = None,
    next_action_payload: dict | None = None,
    event_transition: str = "test_transition",
    event_payload: dict | None = None,
    idempotency_key: str = "test-transition-1",
) -> OrchestrationTransitionCommand:
    checkpoint = {} if checkpoint is None else checkpoint
    return OrchestrationTransitionCommand(
        expected_version=(
            orchestration.version
            if expected_version is None
            else expected_version
        ),
        target_state=target_state,
        user_stage=user_stage,
        action=action,
        reason=reason,
        checkpoint=checkpoint,
        next_action_kind=next_action_kind,
        next_action_label=next_action_label,
        next_action_payload=next_action_payload,
        recovery_status=recovery_status,
        heartbeat=NOW,
        current_scope_version_id=orchestration.current_scope_version_id,
        current_research_run_id=orchestration.current_research_run_id,
        actor=PRINCIPAL.actor,
        event_transition=event_transition,
        event_message=action,
        event_payload={"source": "test"} if event_payload is None else event_payload,
        idempotency_key=idempotency_key,
    )


def test_confirm_scope_is_idempotent_and_does_not_commit(cmd_session, monkeypatch):
    case, scope = _seed_case(cmd_session)
    service = ResearchOrchestrationService(cmd_session)
    commits: list[None] = []
    monkeypatch.setattr(cmd_session, "commit", lambda: commits.append(None))

    first = service.confirm_scope(case.id, PRINCIPAL, "confirm-1")
    first_version = first.version
    second = service.confirm_scope(case.id, PRINCIPAL, "confirm-1")

    assert first.id == second.id
    assert second.state == "planning_acquisition"
    assert second.current_scope_version_id == scope.id
    assert second.current_research_run_id is not None
    assert second.version == first_version == 1
    assert _event_count(cmd_session, first.id) == 1
    assert _outbox_count(cmd_session, first.id) == 1
    event = cmd_session.scalar(select(ResearchOrchestrationEvent))
    outbox = cmd_session.scalar(select(DomainEvent))
    assert event.transition == "scope_confirmed"
    assert event.sequence == 1
    assert outbox.type == "research.orchestration.scope_confirmed"
    assert outbox.actor == PRINCIPAL.actor
    assert outbox.correlation_id == "confirm-1"
    assert commits == []


def test_confirm_scope_with_a_new_key_after_confirmation_is_a_conflict(cmd_session):
    case, _ = _seed_case(cmd_session)
    service = ResearchOrchestrationService(cmd_session)
    service.confirm_scope(case.id, PRINCIPAL, "confirm-1")

    with pytest.raises(ConflictError, match="already confirmed"):
        service.confirm_scope(case.id, PRINCIPAL, "confirm-2")


def test_confirm_replay_ignores_a_later_scope_but_rejects_another_actor(cmd_session):
    case, scope_v1 = _seed_case(cmd_session)
    service = ResearchOrchestrationService(cmd_session)
    orchestration = service.confirm_scope(case.id, PRINCIPAL, "confirm-stable")
    cmd_session.commit()
    original_version = orchestration.version
    original_event_count = _event_count(cmd_session, orchestration.id)
    original_outbox_count = _outbox_count(cmd_session, orchestration.id)
    scope_v2 = EventResearchScopeVersion(
        research_case_id=case.id,
        version=2,
        changed_by="human:bob",
        change_summary="later server-side scope",
        created_at=NOW + timedelta(minutes=1),
    )
    cmd_session.add(scope_v2)
    cmd_session.commit()

    replay = service.confirm_scope(case.id, PRINCIPAL, "confirm-stable")

    assert replay.id == orchestration.id
    assert replay.current_scope_version_id == scope_v1.id
    assert replay.current_scope_version_id != scope_v2.id
    assert replay.version == original_version
    assert _event_count(cmd_session, orchestration.id) == original_event_count
    assert _outbox_count(cmd_session, orchestration.id) == original_outbox_count
    with pytest.raises(ConflictError, match="different input"):
        service.confirm_scope(
            case.id,
            OrchestrationPrincipal(TENANT, "human:bob"),
            "confirm-stable",
        )


def test_wrong_tenant_cannot_read_or_mutate(cmd_session):
    case, _ = _seed_case(cmd_session)
    service = ResearchOrchestrationService(cmd_session)
    orchestration = service.confirm_scope(case.id, PRINCIPAL, "confirm-1")
    intruder = OrchestrationPrincipal("tenant-beta", "human:mallory")

    with pytest.raises(NotFoundError):
        service.reconcile(case.id, intruder)
    with pytest.raises(NotFoundError):
        service.record_acquisition_progress(
            case.id,
            intruder,
            AcquisitionProgressSnapshot(
                target_state="acquiring",
                user_stage="acquisition",
                action="Collecting sources",
                reason="Scope confirmed",
                checkpoint={"acquisition_round": 1},
            ),
            "progress-intruder",
        )
    assert _event_count(cmd_session, orchestration.id) == 1


def test_wrong_tenant_cannot_append_a_workflow_event(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )

    with pytest.raises(NotFoundError):
        ResearchOrchestrationRepository(cmd_session).append_event(
            case.id,
            "tenant-intruder",
            transition="intruder_event",
            actor="human:mallory",
            message="Attempted unauthorized append",
            payload={"unauthorized": True},
            idempotency_key="intruder-append",
        )

    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_reconcile_does_not_create_an_absent_projection(cmd_session):
    case, _ = _seed_case(cmd_session)

    with pytest.raises(NotFoundError):
        ResearchOrchestrationService(cmd_session).reconcile(case.id, PRINCIPAL)

    assert cmd_session.scalar(
        select(func.count()).select_from(ResearchOrchestration)
    ) == 0


def test_repository_rejects_illegal_and_stale_transitions(cmd_session):
    case, _ = _seed_case(cmd_session)
    orchestration = ResearchOrchestrationService(cmd_session).confirm_scope(
        case.id, PRINCIPAL, "confirm-1"
    )
    repository = ResearchOrchestrationRepository(cmd_session)

    with pytest.raises(ValidationFailedError, match="illegal transition"):
        repository.transition(
            orchestration,
            _transition_command(
                orchestration,
                target_state="monitoring",
                user_stage="report_monitoring",
                action="Monitoring",
                reason="Skipped required stages",
                idempotency_key="illegal-transition",
            ),
        )
    with pytest.raises(ConflictError, match="stale orchestration version"):
        repository.transition(
            orchestration,
            _transition_command(
                orchestration,
                expected_version=0,
                target_state="acquiring",
                action="Collecting sources",
                reason="Scope confirmed",
                idempotency_key="stale-transition",
            ),
        )
    assert orchestration.version == 1
    assert orchestration.state == "planning_acquisition"


def test_acquisition_recovery_edges_are_explicit_and_generic_recover_is_noop(
    cmd_session,
):
    assert "recovering" in LEGAL_TRANSITIONS["planning_acquisition"]
    assert "recovering" in LEGAL_TRANSITIONS["acquiring"]
    assert "planning_acquisition" in LEGAL_TRANSITIONS["recovering"]
    assert "acquiring" in LEGAL_TRANSITIONS["recovering"]
    assert "recovering" not in LEGAL_TRANSITIONS["awaiting_scope_confirmation"]
    assert "recovering" not in LEGAL_TRANSITIONS["assessing_coverage"]

    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
        heartbeat=NOW - timedelta(hours=2),
    )

    result = ResearchOrchestrationService(cmd_session).recover(
        case.id,
        PRINCIPAL,
        NOW - timedelta(hours=1),
    )

    assert result.id == orchestration.id
    assert result.state == "planning_acquisition"
    assert _event_count(cmd_session, orchestration.id) == 0


def test_generic_transition_still_rejects_arbitrary_same_state_restart(
    cmd_session,
):
    case, _ = _seed_case(cmd_session)
    orchestration = ResearchOrchestrationService(cmd_session).confirm_scope(
        case.id,
        PRINCIPAL,
        "confirm-before-same-state",
    )

    with pytest.raises(
        ValidationFailedError,
        match="same-state transition",
    ):
        ResearchOrchestrationRepository(cmd_session).transition(
            orchestration,
            _transition_command(
                orchestration,
                target_state="planning_acquisition",
                action="Arbitrary replanning",
                reason="No scope revision proof",
                event_transition="not_scope_revised",
                idempotency_key="arbitrary-same-state",
            ),
        )

    assert orchestration.version == 1
    assert orchestration.state == "planning_acquisition"


def test_coverage_checkpoint_event_records_same_state_without_opening_generic_restart(
    cmd_session,
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage",
        user_stage="acquisition",
    )
    command = _transition_command(
        orchestration,
        target_state="assessing_coverage",
        checkpoint={"coverage_decision": {"decision": "ready"}},
        action="Coverage is ready",
        reason="Every mandatory goal passed the frozen policy",
        event_transition="coverage_ready",
        idempotency_key="coverage-ready-1",
    )

    result = ResearchOrchestrationRepository(cmd_session).record_checkpoint_event(
        orchestration, command
    )

    assert result.state == "assessing_coverage"
    assert result.version == 1
    assert result.checkpoint_json == {
        "coverage_decision": {"decision": "ready"}
    }
    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1
    checkpoint_event = cmd_session.scalar(
        select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration.id
        )
    )
    assert checkpoint_event.payload_json["from_state"] == "assessing_coverage"
    assert checkpoint_event.payload_json["to_state"] == "assessing_coverage"

    replay = ResearchOrchestrationRepository(cmd_session).record_checkpoint_event(
        orchestration, command
    )
    assert replay.version == 1
    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1


def test_public_transition_atomically_updates_all_projections(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)

    result = repository.transition(
        orchestration,
        _transition_command(
            orchestration,
            target_state="acquiring",
            action="Collecting authoritative sources",
            reason="The acquisition plan is ready",
            checkpoint={"acquisition_round": 1},
            event_transition="acquisition_started",
            idempotency_key="direct-atomic-1",
        ),
    )

    assert result.state == "acquiring"
    assert result.version == 1
    assert _event_count(cmd_session, result.id) == 1
    assert _outbox_count(cmd_session, result.id) == 1
    event = cmd_session.scalar(select(ResearchOrchestrationEvent))
    assert event.payload_json["from_state"] == "planning_acquisition"
    assert event.payload_json["to_state"] == "acquiring"
    fingerprint = event.payload_json["command_fingerprint"]
    assert fingerprint["action"] == (
        "Collecting authoritative sources"
    )
    assert {
        "target_state",
        "user_stage",
        "action",
        "reason",
        "checkpoint",
        "recovery_status",
        "next_action_kind",
        "next_action_label",
        "next_action_payload",
        "current_scope_version_id",
        "current_research_run_id",
    } <= fingerprint.keys()
    assert "compatibility" not in fingerprint
    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert lifecycle.status == "researching"
    assert lifecycle.status_summary == "正在搜索并获取外部资料"
    assert lifecycle.current_round == 1


def test_transition_values_are_frozen(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    command = _transition_command(orchestration, target_state="acquiring")

    with pytest.raises(FrozenInstanceError):
        command.action = "mutated"


def test_locked_read_refreshes_stale_identity_and_rejects_stale_writer(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh-lock.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    try:
        with sessions() as setup:
            case, scope = _seed_case(setup)
            orchestration = _seed_orchestration(
                setup,
                case,
                scope,
                state="planning_acquisition",
                user_stage="acquisition",
            )
            case_id = case.id
            orchestration_id = orchestration.id

        with sessions() as session_a, sessions() as session_b:
            stale = session_b.get(ResearchOrchestration, orchestration_id)
            assert stale.version == 0
            session_b.commit()

            current = ResearchOrchestrationRepository(session_a).get_for_update(
                case_id, TENANT
            )
            ResearchOrchestrationRepository(session_a).transition(
                current,
                _transition_command(
                    current,
                    expected_version=0,
                    target_state="acquiring",
                    idempotency_key="session-a-transition",
                ),
            )
            session_a.commit()

            repository_b = ResearchOrchestrationRepository(session_b)
            refreshed = repository_b.get_for_update(case_id, TENANT)
            assert refreshed is stale
            assert refreshed.state == "acquiring"
            assert refreshed.version == 1
            with pytest.raises(ConflictError, match="stale orchestration version"):
                repository_b.transition(
                    refreshed,
                    _transition_command(
                        refreshed,
                        expected_version=0,
                        target_state="freezing_sources",
                        idempotency_key="stale-session-b-transition",
                    ),
                )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_orchestration_repository_uses_the_shared_protocol_case_lock(
    cmd_session,
    monkeypatch,
):
    case, scope = _seed_case(cmd_session)
    _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    from app.services.event_research_scope_evidence import (
        lock_event_scope_case as real_lock_event_scope_case,
    )

    locked_case_ids: list[uuid.UUID] = []

    def tracked_case_lock(session, case_id):
        locked_case_ids.append(case_id)
        return real_lock_event_scope_case(session, case_id)

    monkeypatch.setattr(
        "app.repositories.research_orchestration.lock_event_scope_case",
        tracked_case_lock,
    )

    locked = ResearchOrchestrationRepository(cmd_session).get_for_update(
        case.id,
        TENANT,
    )

    assert locked is not None
    assert locked_case_ids == [case.id]


def test_create_rejects_a_scope_owned_by_another_case(cmd_session):
    case, _ = _seed_case(cmd_session)
    foreign_case, foreign_scope = _seed_case(cmd_session)

    with pytest.raises(ValidationFailedError, match="scope version"):
        ResearchOrchestrationRepository(cmd_session).create_if_absent(
            case.id,
            TENANT,
            foreign_scope.id,
            initial_state="awaiting_scope_confirmation",
            initial_user_stage="scope_confirmation",
            initial_action="Waiting for scope confirmation",
            initial_reason="Acquisition requires a confirmed scope",
        )

    assert cmd_session.scalar(
        select(func.count())
        .select_from(ResearchOrchestration)
        .where(ResearchOrchestration.research_case_id == case.id)
    ) == 0
    assert foreign_case.id != case.id


@pytest.mark.parametrize("pointer", ["scope", "run"])
def test_transition_rejects_cross_case_pointers_without_partial_writes(
    cmd_session, pointer
):
    case, scope = _seed_case(cmd_session)
    foreign_case, foreign_scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    foreign_run = ResearchRun(
        research_case_id=foreign_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        created_at=NOW,
        updated_at=NOW,
    )
    cmd_session.add(foreign_run)
    cmd_session.commit()
    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    original_lifecycle = (
        lifecycle.status,
        lifecycle.status_summary,
        lifecycle.active_run_id,
        lifecycle.current_round,
        lifecycle.current_gap,
        lifecycle.next_human_action,
    )
    command = _transition_command(
        orchestration,
        target_state="acquiring",
        idempotency_key=f"cross-case-{pointer}",
    )
    command = replace(
        command,
        **(
            {"current_scope_version_id": foreign_scope.id}
            if pointer == "scope"
            else {"current_research_run_id": foreign_run.id}
        ),
    )

    with pytest.raises(ValidationFailedError, match=pointer):
        ResearchOrchestrationRepository(cmd_session).transition(
            orchestration, command
        )
    cmd_session.rollback()

    persisted = cmd_session.get(ResearchOrchestration, orchestration.id)
    persisted_lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert persisted.version == 0
    assert persisted.state == "planning_acquisition"
    assert persisted.current_scope_version_id == scope.id
    assert persisted.current_research_run_id is None
    assert _event_count(cmd_session, persisted.id) == 0
    assert _outbox_count(cmd_session, persisted.id) == 0
    assert (
        persisted_lifecycle.status,
        persisted_lifecycle.status_summary,
        persisted_lifecycle.active_run_id,
        persisted_lifecycle.current_round,
        persisted_lifecycle.current_gap,
        persisted_lifecycle.next_human_action,
    ) == original_lifecycle


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("user_stage", "evidence_synthesis"),
        ("action", "Changed action"),
        ("reason", "Changed reason"),
        ("recovery_status", "stale"),
        ("checkpoint", {"cursor": "changed"}),
        (
            "next_action_payload",
            {
                **_decision_context(),
                "recommendation": "Stop instead of retrying",
            },
        ),
    ],
)
def test_replay_rejects_any_changed_behavior_input(
    cmd_session, field, replacement
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage",
        user_stage="acquisition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)
    command = _transition_command(
        orchestration,
        target_state="needs_scope_decision",
        action="Waiting for a scope decision",
        reason="Required goals remain unresolved",
        checkpoint={},
        next_action_kind="scope_decision",
        next_action_label="调整研究边界",
        next_action_payload=_decision_context(),
        event_transition="scope_decision_requested",
        idempotency_key="full-fingerprint-1",
    )
    repository.transition(orchestration, command)

    with pytest.raises(ConflictError, match="different input"):
        repository.transition(orchestration, replace(command, **{field: replacement}))

    assert orchestration.version == 1
    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1


def test_replay_fingerprint_includes_scope_and_run_updates(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)
    command = _transition_command(
        orchestration,
        target_state="acquiring",
        idempotency_key="identity-fingerprint",
    )
    repository.transition(orchestration, command)
    variants = [
        replace(command, current_scope_version_id=uuid.uuid4()),
        replace(command, current_research_run_id=uuid.uuid4()),
    ]

    for changed in variants:
        with pytest.raises(ConflictError, match="different input"):
            repository.transition(orchestration, changed)


@pytest.mark.parametrize(
    "round_value",
    [None, -1, True, False, "2", 1.5],
)
def test_compatibility_round_resets_invalid_or_missing_values(
    cmd_session, round_value
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    checkpoint = {} if round_value is None else {"acquisition_round": round_value}

    ResearchOrchestrationRepository(cmd_session).transition(
        orchestration,
        _transition_command(
            orchestration,
            target_state="acquiring",
            checkpoint=checkpoint,
            idempotency_key=f"invalid-round-{round_value!r}",
        ),
    )

    assert cmd_session.get(EventResearchLifecycle, case.id).current_round == 0


def _invalid_decision_contexts() -> list[object]:
    valid = _decision_context()
    contexts: list[object] = []
    for field in valid:
        contexts.append({key: value for key, value in valid.items() if key != field})
    contexts.extend(
        [
            {"options": ["revise_scope", "keep_scope", "stop"]},
            {**valid, "attempted_rounds": -1},
            {**valid, "attempted_rounds": True},
            {**valid, "attempted_rounds": "2"},
            {**valid, "action": "invalid_boundary_action"},
            {**valid, "boundary_codes": []},
            {**valid, "affected_goals": []},
            {**valid, "missing": []},
            {**valid, "attempted": []},
            {**valid, "cannot_continue_reason": ""},
            {**valid, "recommendation": {"kind": "keep_scope"}},
            {**valid, "alternatives": []},
        ]
    )
    return contexts


@pytest.mark.parametrize("payload", _invalid_decision_contexts())
def test_needs_scope_decision_rejects_incomplete_context(cmd_session, payload):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage",
        user_stage="acquisition",
    )

    with pytest.raises(ValidationFailedError, match="decision"):
        ResearchOrchestrationRepository(cmd_session).transition(
            orchestration,
            _transition_command(
                orchestration,
                target_state="needs_scope_decision",
                next_action_kind="scope_decision",
                next_action_label="调整研究边界",
                next_action_payload=payload,
                idempotency_key=f"invalid-decision-{uuid.uuid4()}",
            ),
        )

    assert orchestration.state == "assessing_coverage"
    assert orchestration.version == 0
    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_event_sequence_is_monotonic_and_key_is_unique(cmd_session):
    case, _ = _seed_case(cmd_session)
    orchestration = ResearchOrchestrationService(cmd_session).confirm_scope(
        case.id, PRINCIPAL, "confirm-1"
    )
    repository = ResearchOrchestrationRepository(cmd_session)

    second = repository.append_event(
        case.id,
        TENANT,
        transition="test_checkpoint",
        actor=PRINCIPAL.actor,
        message="Checkpoint recorded",
        payload={"n": 2},
        idempotency_key="event-2",
    )
    replay = repository.append_event(
        case.id,
        TENANT,
        transition="test_checkpoint",
        actor=PRINCIPAL.actor,
        message="Checkpoint recorded",
        payload={"n": 2},
        idempotency_key="event-2",
    )
    third = repository.append_event(
        case.id,
        TENANT,
        transition="test_checkpoint",
        actor=PRINCIPAL.actor,
        message="Another checkpoint",
        payload={"n": 3},
        idempotency_key="event-3",
    )

    assert replay.id == second.id
    assert [second.sequence, third.sequence] == [2, 3]
    assert [
        row.sequence
        for row in cmd_session.scalars(
            select(ResearchOrchestrationEvent)
            .where(ResearchOrchestrationEvent.orchestration_id == orchestration.id)
            .order_by(ResearchOrchestrationEvent.sequence)
        )
    ] == [1, 2, 3]
    assert _outbox_count(cmd_session, orchestration.id) == 3

    with pytest.raises(ConflictError, match="different input"):
        repository.append_event(
            case.id,
            TENANT,
            transition="test_checkpoint",
            actor=PRINCIPAL.actor,
            message="Changed checkpoint",
            payload={"n": 2},
            idempotency_key="event-2",
        )


def test_public_append_canonical_replay_survives_session_reload(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'canonical-append.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    try:
        with sessions() as setup:
            case, scope = _seed_case(setup)
            orchestration = _seed_orchestration(
                setup,
                case,
                scope,
                state="planning_acquisition",
                user_stage="acquisition",
            )
            setup.commit()
            case_id = case.id
            orchestration_id = orchestration.id

        with sessions() as first_session:
            first = ResearchOrchestrationRepository(first_session).append_event(
                case_id,
                TENANT,
                transition="checkpoint_observed",
                actor=PRINCIPAL.actor,
                message="Observed a checkpoint",
                payload={
                    "z": ({"b": 2, "a": (True, None)},),
                    "a": {"nested": (3, 4)},
                },
                idempotency_key="canonical-append",
            )
            first_session.commit()
            first_id = first.id

        with sessions() as replay_session:
            replay = ResearchOrchestrationRepository(replay_session).append_event(
                case_id,
                TENANT,
                transition="checkpoint_observed",
                actor=PRINCIPAL.actor,
                message="Observed a checkpoint",
                payload={
                    "a": {"nested": [3, 4]},
                    "z": [{"a": [True, None], "b": 2}],
                },
                idempotency_key="canonical-append",
            )

            assert replay.id == first_id
            assert replay.payload_json["z"] == [
                {"a": [True, None], "b": 2}
            ]
            assert _event_count(replay_session, orchestration_id) == 1
            assert _outbox_count(replay_session, orchestration_id) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize(
    "changes",
    [
        {"transition": "changed_transition"},
        {"actor": "human:bob"},
        {"message": "Changed message"},
        {"payload": {"value": 2}},
    ],
    ids=["transition", "actor", "message", "payload"],
)
def test_public_append_rejects_changed_replay_input(cmd_session, changes):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)
    values = {
        "transition": "checkpoint_observed",
        "actor": PRINCIPAL.actor,
        "message": "Observed a checkpoint",
        "payload": {"value": 1},
    }
    repository.append_event(
        case.id,
        TENANT,
        **values,
        idempotency_key="append-conflict",
    )

    with pytest.raises(ConflictError, match="different input"):
        repository.append_event(
            case.id,
            TENANT,
            **{**values, **changes},
            idempotency_key="append-conflict",
        )

    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1


def test_public_append_outbox_failure_rolls_back_workflow_event(
    cmd_session, monkeypatch
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="planning_acquisition",
        user_stage="acquisition",
    )

    def fail_emit(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(
        "app.repositories.research_orchestration.emit_event", fail_emit
    )
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        ResearchOrchestrationRepository(cmd_session).append_event(
            case.id,
            TENANT,
            transition="checkpoint_observed",
            actor=PRINCIPAL.actor,
            message="Observed a checkpoint",
            payload={"value": 1},
            idempotency_key="append-outbox-failure",
        )
    cmd_session.rollback()

    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_transition_canonicalizes_every_json_surface(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage",
        user_stage="acquisition",
    )
    context = _decision_context()
    context["attempted"] = tuple(context["attempted"])
    context["alternatives"] = tuple(context["alternatives"])
    command = _transition_command(
        orchestration,
        target_state="needs_scope_decision",
        action="Waiting for a scope decision",
        reason="Required goals remain unresolved",
        checkpoint={"z": ({"b": 2, "a": 1},)},
        next_action_kind="scope_decision",
        next_action_label="调整研究边界",
        next_action_payload=context,
        event_transition="scope_decision_requested",
        event_payload={"details": ({"b": 2, "a": 1},)},
        idempotency_key="canonical-transition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)

    result = repository.transition(orchestration, command)
    replay = repository.transition(
        result,
        replace(
            command,
            checkpoint={"z": [{"a": 1, "b": 2}]},
            next_action_payload={
                **_decision_context(),
                "alternatives": list(_decision_context()["alternatives"]),
            },
            event_payload={"details": [{"a": 1, "b": 2}]},
        ),
    )

    assert replay.id == result.id
    assert result.checkpoint_json == {"z": [{"a": 1, "b": 2}]}
    assert isinstance(result.next_action_payload["attempted"], list)
    assert isinstance(result.next_action_payload["alternatives"], list)
    event = cmd_session.scalar(select(ResearchOrchestrationEvent))
    assert event.payload_json["details"] == [{"a": 1, "b": 2}]
    assert _event_count(cmd_session, result.id) == 1
    assert _outbox_count(cmd_session, result.id) == 1


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("checkpoint", {"bad": uuid.uuid4()}),
        ("next_action_payload", {"bad": NOW}),
        ("event_payload", {"bad": b"bytes"}),
        ("event_payload", {"bad": {"set-value"}}),
        ("event_payload", {"bad": nan}),
        ("event_payload", {"bad": inf}),
        ("event_payload", {"bad": -inf}),
        ("event_payload", {"bad": object()}),
        ("event_payload", {1: "non-string key"}),
    ],
    ids=[
        "uuid",
        "datetime",
        "bytes",
        "set",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "unsupported-object",
        "non-string-key",
    ],
)
def test_transition_rejects_noncanonical_json_before_writes(
    cmd_session, field, invalid
):
    case, scope = _seed_case(cmd_session)
    needs_decision = field == "next_action_payload"
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage" if needs_decision else "planning_acquisition",
        user_stage="acquisition",
    )
    kwargs = {
        "target_state": (
            "needs_scope_decision" if needs_decision else "acquiring"
        ),
        "event_payload": {"source": "test"},
        "idempotency_key": f"invalid-json-{field}-{uuid.uuid4()}",
    }
    if field == "checkpoint":
        kwargs["checkpoint"] = invalid
    elif field == "event_payload":
        kwargs["event_payload"] = invalid
    else:
        kwargs.update(
            next_action_kind="scope_decision",
            next_action_label="调整研究边界",
            next_action_payload={**_decision_context(), **invalid},
        )

    with pytest.raises(ValidationFailedError, match="JSON"):
        ResearchOrchestrationRepository(cmd_session).transition(
            orchestration,
            _transition_command(orchestration, **kwargs),
        )

    assert orchestration.version == 0
    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_acquisition_progress_updates_automatic_projection_and_round(cmd_session):
    case, _ = _seed_case(cmd_session)
    service = ResearchOrchestrationService(cmd_session)
    service.confirm_scope(case.id, PRINCIPAL, "confirm-1")

    orchestration = service.record_acquisition_progress(
        case.id,
        PRINCIPAL,
        AcquisitionProgressSnapshot(
            target_state="acquiring",
            user_stage="acquisition",
            action="Collecting authoritative sources",
            reason="The confirmed scope needs evidence",
            checkpoint={"acquisition_round": 3, "query": "capacity expansion"},
            recovery_status="healthy",
        ),
        "progress-1",
    )

    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert orchestration.state == "acquiring"
    assert orchestration.next_action_kind is None
    assert orchestration.next_action_label is None
    assert orchestration.next_action_payload is None
    assert lifecycle.status == "researching"
    assert lifecycle.current_round == 3
    assert lifecycle.current_gap is None
    assert lifecycle.next_human_action is None
    assert _event_count(cmd_session, orchestration.id) == 2
    assert _outbox_count(cmd_session, orchestration.id) == 2


def test_progress_snapshot_rejects_blank_and_disallowed_values():
    with pytest.raises(ValidationFailedError, match="action"):
        AcquisitionProgressSnapshot(
            target_state="acquiring",
            user_stage="acquisition",
            action="  ",
            reason="reason",
            checkpoint={},
        )
    with pytest.raises(ValidationFailedError, match="target_state"):
        AcquisitionProgressSnapshot(
            target_state="monitoring",
            user_stage="report_monitoring",
            action="Monitor",
            reason="reason",
            checkpoint={},
        )


def test_needs_scope_decision_requires_one_complete_action(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="assessing_coverage",
        user_stage="acquisition",
    )
    repository = ResearchOrchestrationRepository(cmd_session)

    with pytest.raises(ValidationFailedError, match="next action"):
        repository.transition(
            orchestration,
            _transition_command(
                orchestration,
                target_state="needs_scope_decision",
                action="Waiting for scope decision",
                reason="Coverage is incomplete",
                next_action_kind="scope_decision",
                next_action_label=None,
                next_action_payload=_decision_context(),
                idempotency_key="missing-decision-label",
            ),
        )

    repository.transition(
        orchestration,
        _transition_command(
            orchestration,
            target_state="needs_scope_decision",
            action="Waiting for scope decision",
            reason="Coverage is incomplete",
            next_action_kind="scope_decision",
            next_action_label="调整研究边界",
            next_action_payload=_decision_context(),
            idempotency_key="valid-decision-context",
        ),
    )
    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert orchestration.next_action_kind == "scope_decision"
    assert orchestration.next_action_label == "调整研究边界"
    assert orchestration.next_action_payload == _decision_context()
    assert lifecycle.status == "awaiting_scope"
    assert lifecycle.next_human_action == "调整研究边界"
    assert lifecycle.current_gap == "Coverage is incomplete"


def test_keep_scope_continues_to_honest_insufficient_synthesis_idempotently(
    cmd_session,
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="needs_scope_decision",
        user_stage="acquisition",
        version=4,
    )
    run = AutoResearchService(cmd_session).start(
        case.id,
        enqueue=False,
        commit=False,
    )
    orchestration.current_research_run_id = run.id
    cmd_session.commit()
    service = ResearchOrchestrationService(cmd_session)

    first = service.decide_scope(
        case.id,
        PRINCIPAL,
        ScopeDecision(kind="keep_scope", reason="Keep unresolved goals unknown"),
        "decision-1",
        expected_version=4,
    )
    replay = service.decide_scope(
        case.id,
        PRINCIPAL,
        ScopeDecision(kind="keep_scope", reason="Keep unresolved goals unknown"),
        "decision-1",
        expected_version=4,
    )

    assert first.id == orchestration.id == replay.id
    assert replay.version == 5
    assert replay.state == "synthesizing_evidence"
    assert replay.next_action_kind is None
    assert replay.checkpoint_json["scope_decision"] == {
        "kind": "keep_scope",
        "unresolved_facts_remain_unknown": True,
    }
    assert AutoResearchRepository(cmd_session).job_for_run(run.id) is not None
    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1


def test_keep_scope_rejects_a_stale_expected_version(
    cmd_session,
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="needs_scope_decision",
        user_stage="scope_confirmation",
        version=4,
    )
    with pytest.raises(ConflictError, match="version"):
        ResearchOrchestrationService(cmd_session).decide_scope(
            case.id,
            PRINCIPAL,
            ScopeDecision(kind="keep_scope", reason="Keep unresolved goals unknown"),
            "stale-scope-decision",
            expected_version=3,
        )

    assert orchestration.state == "needs_scope_decision"
    assert orchestration.version == 4
    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_stop_scope_decision_cancels_without_generating_a_report(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="needs_scope_decision",
        user_stage="acquisition",
    )

    ResearchOrchestrationService(cmd_session).decide_scope(
        case.id,
        PRINCIPAL,
        ScopeDecision(kind="stop", reason="The available evidence is insufficient"),
        "decision-stop",
        expected_version=orchestration.version,
    )

    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert orchestration.state == "cancelled"
    assert lifecycle.status == "exhausted"
    assert lifecycle.current_gap is None
    assert lifecycle.status_summary == "研究流程已取消"
    assert lifecycle.next_human_action is None
    assert orchestration.checkpoint_json["scope_decision"] == {
        "kind": "stop",
        "unresolved_facts_remain_unknown": False,
    }
    assert cmd_session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == case.id
        )
    ) is None


def test_monitoring_lifecycle_is_explicitly_system_generated(cmd_session):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="generating_report",
        user_stage="report_monitoring",
    )

    ResearchOrchestrationRepository(cmd_session).transition(
        orchestration,
        _transition_command(
            orchestration,
            target_state="monitoring",
            user_stage="report_monitoring",
            action="Monitoring report updates",
            reason="System report generation completed",
            event_payload={"claimed_human_review": True},
            idempotency_key="monitoring-transition",
        ),
    )

    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert lifecycle.status == "draft_ready"
    assert lifecycle.status_summary == "系统报告已生成并进入持续监测；未经人工审核"
    assert "人工审核" in lifecycle.status_summary


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_summary"),
    [
        ("intake", "extracting", "正在建立事件研究"),
        (
            "awaiting_scope_confirmation",
            "awaiting_key_review",
            "等待确认可验证命题与研究范围",
        ),
        ("planning_acquisition", "researching", "正在根据已确认范围规划资料获取"),
        ("acquiring", "researching", "正在搜索并获取外部资料"),
        ("freezing_sources", "researching", "正在冻结并去重已获取资料"),
        ("assessing_coverage", "researching", "正在评估各研究目标的证据覆盖"),
        ("synthesizing_evidence", "continuing", "正在归并当前范围内的证据"),
        ("adjudicating_thesis", "continuing", "正在判定命题与竞争性解释"),
        ("generating_report", "continuing", "正在生成带来源的系统研究报告"),
        (
            "monitoring",
            "draft_ready",
            "系统报告已生成并进入持续监测；未经人工审核",
        ),
        ("retry_wait", "continuing", "系统正在等待自动重试，无需人工操作"),
        ("recovering", "continuing", "系统正在从最近检查点恢复，无需人工操作"),
        ("needs_scope_decision", "awaiting_scope", "研究边界需要你的决定"),
        ("exhausted", "exhausted", "限定检索范围已穷尽，未解决项保持未知"),
        ("cancelled", "exhausted", "研究流程已取消"),
        ("failed", "exhausted", "研究流程失败，已保留最近检查点"),
    ],
)
def test_every_state_has_the_required_compatibility_mapping(
    cmd_session, state, expected_status, expected_summary
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state=state,
        user_stage="acquisition",
    )

    lifecycle = ResearchOrchestrationRepository(cmd_session)._derive_compatibility(
        orchestration
    )

    assert lifecycle.status == expected_status
    assert lifecycle.status_summary == expected_summary
    gap_states = {"needs_scope_decision", "exhausted", "failed"}
    assert (lifecycle.current_gap is not None) == (state in gap_states)
    assert (lifecycle.next_human_action is not None) == (
        state == "needs_scope_decision"
    )


def test_outbox_failure_can_be_rolled_back_by_caller(cmd_session, monkeypatch):
    case, _ = _seed_case(cmd_session)
    original_lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    original_summary = original_lifecycle.status_summary

    def fail_emit(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("app.repositories.research_orchestration.emit_event", fail_emit)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        ResearchOrchestrationService(cmd_session).confirm_scope(
            case.id, PRINCIPAL, "confirm-fails"
        )
    cmd_session.rollback()

    assert cmd_session.scalar(
        select(func.count()).select_from(ResearchOrchestration)
    ) == 0
    assert cmd_session.scalar(
        select(func.count()).select_from(ResearchOrchestrationEvent)
    ) == 0
    assert cmd_session.scalar(select(func.count()).select_from(DomainEvent)) == 0
    lifecycle = cmd_session.get(EventResearchLifecycle, case.id)
    assert lifecycle.status == "awaiting_key_review"
    assert lifecycle.status_summary == original_summary


def test_recover_stale_workflow_once_and_replay_is_a_noop(cmd_session):
    case, scope = _seed_case(cmd_session)
    stale = NOW - timedelta(hours=2)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state="retry_wait",
        user_stage="evidence_synthesis",
        heartbeat=stale,
        version=7,
        checkpoint={"acquisition_round": 2, "cursor": "batch-4"},
    )
    service = ResearchOrchestrationService(cmd_session)

    first = service.recover(case.id, PRINCIPAL, NOW - timedelta(hours=1))
    replay = service.recover(case.id, PRINCIPAL, NOW - timedelta(hours=1))

    assert first.id == replay.id == orchestration.id
    assert replay.state == "recovering"
    assert replay.recovery_status == "recovering"
    assert replay.version == 8
    assert replay.next_action_kind is None
    assert _event_count(cmd_session, orchestration.id) == 1
    assert _outbox_count(cmd_session, orchestration.id) == 1
    event = cmd_session.scalar(select(ResearchOrchestrationEvent))
    assert event.idempotency_key.startswith("recover:")


@pytest.mark.parametrize(
    ("state", "heartbeat"),
    [("retry_wait", NOW), ("exhausted", NOW - timedelta(days=1))],
)
def test_recover_leaves_fresh_and_terminal_workflows_unchanged(
    cmd_session, state, heartbeat
):
    case, scope = _seed_case(cmd_session)
    orchestration = _seed_orchestration(
        cmd_session,
        case,
        scope,
        state=state,
        user_stage="acquisition",
        heartbeat=heartbeat,
        version=3,
    )

    result = ResearchOrchestrationService(cmd_session).recover(
        case.id, PRINCIPAL, NOW - timedelta(hours=1)
    )

    assert result.id == orchestration.id
    assert result.state == state
    assert result.version == 3
    assert _event_count(cmd_session, orchestration.id) == 0
    assert _outbox_count(cmd_session, orchestration.id) == 0


def test_find_reconcilable_is_bounded_deterministic_and_records_heartbeat(cmd_session):
    stale = NOW - timedelta(hours=2)
    rows = []
    for index in range(3):
        case, scope = _seed_case(cmd_session)
        rows.append(
            _seed_orchestration(
                cmd_session,
                case,
                scope,
                state="retry_wait",
                user_stage="acquisition",
                heartbeat=stale + timedelta(minutes=index),
            )
        )
    repository = ResearchOrchestrationRepository(cmd_session)

    found = repository.find_reconcilable(
        now=NOW, stale_before=NOW - timedelta(hours=1), limit=2
    )

    assert [row.id for row in found] == [rows[0].id, rows[1].id]
    old_version = rows[0].version
    repository.record_heartbeat(rows[0], old_version, NOW)
    assert rows[0].last_heartbeat_at == NOW
    assert rows[0].version == old_version + 1


def test_future_retry_wait_rows_cannot_starve_an_executable_batch(cmd_session):
    repository = ResearchOrchestrationRepository(cmd_session)
    future_rows = []
    for index in range(4):
        case, scope = _seed_case(cmd_session)
        auto_research = AutoResearchRepository(cmd_session)
        run = auto_research.create_run(research_case_id=case.id)
        job = auto_research.enqueue_run_job(run)
        run.status = "failed"
        run.stage = "failed"
        job.status = "failed"
        job.failure_count = 1
        job.next_retry_at = NOW + timedelta(hours=1)
        orchestration = _seed_orchestration(
            cmd_session,
            case,
            scope,
            state="retry_wait",
            user_stage="evidence_synthesis",
            heartbeat=NOW - timedelta(hours=4, minutes=index),
        )
        orchestration.current_research_run_id = run.id
        orchestration.updated_at = NOW - timedelta(hours=4, minutes=index)
        cmd_session.commit()
        future_rows.append(orchestration)

    executable_case, executable_scope = _seed_case(cmd_session)
    executable = _seed_orchestration(
        cmd_session,
        executable_case,
        executable_scope,
        state="planning_acquisition",
        user_stage="acquisition",
        heartbeat=NOW,
    )

    before_due = repository.find_reconcilable(
        now=NOW,
        stale_before=NOW - timedelta(hours=1),
        limit=2,
    )

    assert [row.id for row in before_due] == [executable.id]

    after_due = repository.find_reconcilable(
        now=NOW + timedelta(hours=2),
        stale_before=NOW - timedelta(hours=1),
        limit=10,
    )

    assert {row.id for row in future_rows} <= {row.id for row in after_due}


def test_reconcilable_batch_and_heartbeat_refresh_a_cached_row(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh-batch.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    try:
        with sessions() as setup:
            case, scope = _seed_case(setup)
            orchestration = _seed_orchestration(
                setup,
                case,
                scope,
                state="retry_wait",
                user_stage="acquisition",
                heartbeat=NOW - timedelta(hours=2),
                version=0,
            )
            orchestration_id = orchestration.id

        with sessions() as session_a, sessions() as session_b:
            cached = session_b.get(ResearchOrchestration, orchestration_id)
            assert cached.version == 0
            session_b.commit()

            advanced = session_a.get(ResearchOrchestration, orchestration_id)
            advanced.version = 5
            advanced.updated_at = NOW
            advanced.last_heartbeat_at = NOW - timedelta(hours=2)
            session_a.commit()

            repository_b = ResearchOrchestrationRepository(session_b)
            found = repository_b.find_reconcilable(
                now=NOW + timedelta(minutes=1),
                stale_before=NOW - timedelta(hours=1),
                limit=10,
            )
            assert [row.id for row in found] == [orchestration_id]
            assert found[0] is cached
            assert found[0].version == 5

            heartbeat = repository_b.record_heartbeat(found[0], 5, NOW)
            assert heartbeat.version == 6
            session_b.commit()

        with sessions() as verification:
            persisted = verification.get(ResearchOrchestration, orchestration_id)
            assert persisted.version == 6
            assert persisted.version != 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_service_command_methods_never_commit(cmd_session, monkeypatch):
    progress_case, _ = _seed_case(cmd_session)
    decision_case, decision_scope = _seed_case(cmd_session)
    recovery_case, recovery_scope = _seed_case(cmd_session)
    decision_orchestration = _seed_orchestration(
        cmd_session,
        decision_case,
        decision_scope,
        state="needs_scope_decision",
        user_stage="acquisition",
    )
    _seed_orchestration(
        cmd_session,
        recovery_case,
        recovery_scope,
        state="retry_wait",
        user_stage="acquisition",
        heartbeat=NOW - timedelta(hours=2),
    )
    commits: list[None] = []
    monkeypatch.setattr(cmd_session, "commit", lambda: commits.append(None))
    service = ResearchOrchestrationService(cmd_session)
    transitions: list[OrchestrationTransitionCommand] = []
    original_transition = service._repository.transition

    def record_transition(orchestration, command):
        transitions.append(command)
        return original_transition(orchestration, command)

    monkeypatch.setattr(service._repository, "transition", record_transition)

    service.confirm_scope(progress_case.id, PRINCIPAL, "no-commit-confirm")
    service.record_acquisition_progress(
        progress_case.id,
        PRINCIPAL,
        AcquisitionProgressSnapshot(
            target_state="acquiring",
            user_stage="acquisition",
            action="Collecting sources",
            reason="Scope confirmed",
            checkpoint={},
        ),
        "no-commit-progress",
    )
    service.reconcile(progress_case.id, PRINCIPAL)
    service.decide_scope(
        decision_case.id,
        PRINCIPAL,
        ScopeDecision("stop", "No more acquisition is warranted"),
        "no-commit-decision",
        expected_version=decision_orchestration.version,
    )
    service.recover(recovery_case.id, PRINCIPAL, NOW - timedelta(hours=1))

    assert commits == []
    assert len(transitions) == 5
