from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1.tenant_context import ResearchActor
from app.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.models.ledger import ResearchCase
from app.models.operational import Job, ResearchRun
from app.models.research_gateway import (
    ROLE_KEYS,
    GatewayCommand,
    GatewayIdempotencyRequest,
    ResearchConversation,
    ResearchIntent,
    ResearchMessage,
    ResearchRunSpec,
    RoleEvent,
    RoleRun,
)
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.event_extraction import EventExtraction

ALICE = ResearchActor("team-a", frozenset(), "alice")
BOB = ResearchActor("team-a", frozenset({"case_administrator"}), "bob")
FOREIGN = ResearchActor("team-b", frozenset(), "alice")


class _Extractor:
    def extract(self, raw_input: str, source_url: str | None) -> EventExtraction:
        return EventExtraction(
            event_title=raw_input[:80],
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


@dataclass(frozen=True)
class _NativeRef:
    case_id: uuid.UUID
    research_run_id: uuid.UUID


class _Runtime:
    def __init__(self, session):
        self.session = session
        self.calls = []
        self.after_start = None

    def start(self, *, text, tenant_id, actor_subject_id, commit):
        self.calls.append((text, tenant_id, actor_subject_id, commit))
        assert commit is False
        result = AutomaticResearchIntakeService(
            self.session, extractor=_Extractor()
        ).start(
            text, tenant_id=tenant_id, actor_subject_id=actor_subject_id, commit=commit
        )
        if self.after_start:
            self.after_start()
        return _NativeRef(uuid.UUID(result.case_id), uuid.UUID(result.run_id))


def _gateway(session, runtime=None):
    from app.services.research_gateway import ResearchGateway

    return ResearchGateway(session, runtime=runtime or _Runtime(session))


def _send(gateway, text="英伟达需求变化", key="start-1", conversation_id=None):
    return gateway.send_message(
        actor=ALICE, conversation_id=conversation_id, text=text, idempotency_key=key
    )


def test_policy_manifests_are_frozen_and_server_owned():
    from app.services.research_gateway_policy import (
        CAPABILITY_MANIFEST_VERSION,
        ROLE_MANIFEST_VERSION,
        frozen_manifest,
        require_role,
        supported_command,
    )

    assert ROLE_MANIFEST_VERSION == "fundclaw-roles.v1"
    assert CAPABILITY_MANIFEST_VERSION == "fundclaw-capabilities.v1"
    expected = {
        "scope_identity": (("resolve_scope",), ("identity_decision", "blocked")),
        "sources_evidence": (
            ("request_acquisition", "read_frozen_evidence"),
            ("artifact_ref", "rejected_source"),
        ),
        "analysis_counter_evidence": (
            ("propose_candidate_claim", "propose_counter_evidence_task"),
            ("candidate", "gap"),
        ),
        "compilation_checks": (
            ("compile_provisional_draft", "validate_lineage"),
            ("draft_ref", "validation"),
        ),
    }
    manifest = frozen_manifest()
    assert tuple(manifest) == ROLE_KEYS
    for key, (capabilities, outputs) in expected.items():
        assert require_role(key).capabilities == capabilities
        assert require_role(key).outputs == outputs
    with pytest.raises(TypeError):
        manifest["scope_identity"] = None
    with pytest.raises(ValueError):
        require_role("user-selected-super-agent")
    assert supported_command("pause") is False
    assert supported_command("retry") is False


def test_start_freezes_authorized_native_scope_and_four_queued_roles(cmd_session):
    runtime = _Runtime(cmd_session)
    receipt = _send(_gateway(cmd_session, runtime))
    spec = cmd_session.get(ResearchRunSpec, receipt.run_spec_id)
    native = cmd_session.get(ResearchRun, receipt.native_run_id)
    assert (
        spec.frozen_scope
        == load_automatic_research_scope(cmd_session, native).snapshot()
    )
    assert spec.role_manifest_version == "fundclaw-roles.v1"
    assert spec.capability_manifest_version == "fundclaw-capabilities.v1"
    assert spec.frozen_cutoff == {
        "evidence_cutoff": native.created_at.replace(tzinfo=UTC).isoformat(),
        "case_evidence_cutoff": None,
    }
    assert spec.frozen_source_policy["allowed_source_types"] == []
    assert all(
        ref["case_id"] == str(receipt.native_case_id)
        for ref in spec.input_artifact_refs
    )
    roles = list(cmd_session.scalars(select(RoleRun)))
    events = list(cmd_session.scalars(select(RoleEvent).order_by(RoleEvent.sequence)))
    assert {role.role_key for role in roles} == set(ROLE_KEYS)
    assert {role.status for role in roles} == {"queued"}
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert all(event.event_type == "role_queued" for event in events)
    assert receipt.latest_sequence == 4
    assert runtime.calls == [("英伟达需求变化", "team-a", "alice", False)]
    assert cmd_session.scalar(select(ResearchIntent)).intent_kind == "start"
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)).status == "completed"


def test_same_key_replays_without_second_native_start_and_payload_conflicts(
    cmd_session,
):
    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    receipt = _send(gateway)
    assert _send(gateway) == receipt
    with pytest.raises(ConflictError):
        _send(gateway, text="different subject")
    assert len(runtime.calls) == 1
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 1


@pytest.mark.parametrize("other", [BOB, FOREIGN])
def test_every_conversation_access_checks_private_owner(cmd_session, other):
    gateway = _gateway(cmd_session)
    receipt = _send(gateway)
    accesses = [
        lambda: gateway.require_read_access(other, receipt.conversation_id),
        lambda: gateway.read_conversation(other, receipt.conversation_id),
        lambda: gateway.read_run_spec(other, receipt.run_spec_id),
        lambda: gateway.send_message(
            other, receipt.conversation_id, "followup", "other"
        ),
        lambda: gateway.issue_command(
            other, receipt.conversation_id, receipt.run_spec_id, "pause", "cmd"
        ),
    ]
    for access in accesses:
        with pytest.raises(NotFoundError, match="research conversation not found"):
            access()
    assert gateway.list_conversations(other) == []
    assert len(list(cmd_session.scalars(select(GatewayIdempotencyRequest)))) == 1


def test_linked_native_case_authorization_is_required_for_reads_and_replay(cmd_session):
    gateway = _gateway(cmd_session)
    receipt = _send(gateway)
    # Simulate imported/corrupt historical provenance; normal ledger writes
    # correctly forbid changing an admission after it has been recorded.
    cmd_session.connection().exec_driver_sql(
        "UPDATE case_tenant_admissions SET tenant_id = 'team-b'"
    )
    cmd_session.commit()
    for access in [
        lambda: gateway.require_read_access(ALICE, receipt.conversation_id),
        lambda: gateway.read_run_spec(ALICE, receipt.run_spec_id),
        lambda: _send(gateway),
    ]:
        with pytest.raises(NotFoundError):
            access()
    assert gateway.list_conversations(ALICE) == []


@pytest.mark.parametrize("text", ["", " \t\n", "a" * 20_001, "bad\x00", "bad\u0085"])
def test_invalid_text_has_no_lease_or_native_writes(cmd_session, text):
    runtime = _Runtime(cmd_session)
    with pytest.raises(ValidationFailedError):
        _send(_gateway(cmd_session, runtime), text=text)
    assert runtime.calls == []
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)) is None


def test_legacy_actor_cannot_create_private_gateway_records(cmd_session):
    actor = ResearchActor("team-a", frozenset())
    with pytest.raises(PermissionDeniedError):
        _gateway(cmd_session).create_conversation(actor, "Private")
    assert cmd_session.scalar(select(ResearchConversation)) is None


@pytest.mark.parametrize(
    "command",
    [
        "pause",
        "resume",
        "grounded_question",
        "request_counter_evidence",
        "scope_change",
        "cancel",
        "retry",
    ],
)
def test_unsupported_commands_persist_a_replayable_rejection_without_native_effects(
    cmd_session, command
):
    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    receipt = _send(gateway)
    jobs = [row.id for row in cmd_session.scalars(select(Job))]
    result = gateway.issue_command(
        ALICE, receipt.conversation_id, receipt.run_spec_id, command, "command-1"
    )
    assert result.outcome == "rejected"
    assert result.reason_code == "unsupported_in_gateway_p0"
    assert (
        gateway.issue_command(
            ALICE, receipt.conversation_id, receipt.run_spec_id, command, "command-1"
        )
        == result
    )
    assert len(list(cmd_session.scalars(select(GatewayCommand)))) == 1
    assert [row.id for row in cmd_session.scalars(select(Job))] == jobs
    assert len(runtime.calls) == 1
    assert cmd_session.get(ResearchRun, receipt.native_run_id).status == "queued"
    assert {row.status for row in cmd_session.scalars(select(RoleRun))} == {"queued"}


def test_successor_preserves_parent_subject_and_immutable_history(cmd_session):
    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    first = _send(gateway)
    original_scope = copy.deepcopy(
        cmd_session.get(ResearchRunSpec, first.run_spec_id).frozen_scope
    )
    second = _send(
        gateway,
        text="调整范围：把时间范围改成最近一个季度",
        key="scope-2",
        conversation_id=first.conversation_id,
    )
    successor = cmd_session.get(ResearchIntent, second.intent_id)
    assert successor.intent_kind == "scope_change"
    assert successor.parent_intent_id == first.intent_id
    assert "英伟达需求变化" in runtime.calls[1][0]
    assert "把时间范围改成最近一个季度" in runtime.calls[1][0]
    assert "调整范围：" not in runtime.calls[1][0]
    assert (
        cmd_session.get(ResearchRunSpec, first.run_spec_id).frozen_scope
        == original_scope
    )
    assert len(list(cmd_session.scalars(select(ResearchRunSpec)))) == 2
    assert [
        m.content
        for m in cmd_session.scalars(
            select(ResearchMessage).order_by(ResearchMessage.sequence)
        )
    ] == ["英伟达需求变化", "调整范围：把时间范围改成最近一个季度"]
    assert len(list(cmd_session.scalars(select(RoleRun)))) == 8


def test_stale_scope_parent_after_native_start_does_not_publish_a_sibling(
    cmd_session, monkeypatch
):
    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    first = _send(gateway)
    newer = _send(
        gateway,
        text="调整范围：只分析海外收入",
        key="scope-newer",
        conversation_id=first.conversation_id,
    )
    initial_parent = cmd_session.get(ResearchRunSpec, first.run_spec_id)
    newer_parent = cmd_session.get(ResearchRunSpec, newer.run_spec_id)
    assert initial_parent is not None
    assert newer_parent is not None
    parents = iter((initial_parent, newer_parent))
    monkeypatch.setattr(
        gateway,
        "_latest_run_spec",
        lambda _conversation_id: next(parents),
    )

    with pytest.raises(ConflictError, match="research_scope_changed_retry"):
        _send(
            gateway,
            text="调整范围：只分析北美收入",
            key="scope-stale",
            conversation_id=first.conversation_id,
        )

    assert len(runtime.calls) == 3
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 2
    assert len(list(cmd_session.scalars(select(ResearchRunSpec)))) == 2
    assert [
        message.content
        for message in cmd_session.scalars(
            select(ResearchMessage).order_by(ResearchMessage.sequence)
        )
    ] == ["英伟达需求变化", "调整范围：只分析海外收入"]
    stale_request = cmd_session.scalar(
        select(GatewayIdempotencyRequest).where(
            GatewayIdempotencyRequest.client_key == "scope-stale"
        )
    )
    assert stale_request is not None
    assert stale_request.status == "in_progress"


def test_unprefixed_followup_is_a_durable_rejected_intent_without_native_work(
    cmd_session,
):
    from app.repositories.research_gateway import GatewayIntentReceipt

    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    first = _send(gateway)

    rejected = _send(
        gateway,
        text="暂停并说明当前证据",
        key="unsupported-followup",
        conversation_id=first.conversation_id,
    )

    assert isinstance(rejected, GatewayIntentReceipt)
    assert rejected.conversation_id == first.conversation_id
    assert rejected.intent_kind == "unsupported"
    assert rejected.outcome == "rejected"
    assert rejected.reason_code == "unsupported_in_gateway_p0"
    assert (
        _send(
            gateway,
            text="暂停并说明当前证据",
            key="unsupported-followup",
            conversation_id=first.conversation_id,
        )
        == rejected
    )
    rejected_intent = cmd_session.get(ResearchIntent, rejected.intent_id)
    assert rejected_intent is not None
    assert rejected_intent.status == "rejected"
    assert rejected_intent.gateway_request_id is not None
    messages = list(
        cmd_session.scalars(
            select(ResearchMessage).order_by(ResearchMessage.sequence)
        )
    )
    assert [(message.message_kind, message.content) for message in messages] == [
        ("user", "英伟达需求变化"),
        ("user", "暂停并说明当前证据"),
        ("system", "This request is not supported in Gateway P0."),
    ]
    assert len(runtime.calls) == 1
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 1
    assert len(list(cmd_session.scalars(select(ResearchRunSpec)))) == 1
    assert len(list(cmd_session.scalars(select(GatewayCommand)))) == 0
    assert len(list(cmd_session.scalars(select(RoleEvent)))) == 4


def test_native_failure_rolls_back_intake_and_gateway_records_but_retains_lease(
    cmd_session,
):
    runtime = _Runtime(cmd_session)

    def fail():
        raise RuntimeError("provider-secret-error")

    runtime.after_start = fail
    with pytest.raises(RuntimeError):
        _send(_gateway(cmd_session, runtime))
    assert cmd_session.scalar(select(ResearchCase)) is None
    assert cmd_session.scalar(select(ResearchRun)) is None
    assert cmd_session.scalar(select(ResearchConversation)) is None
    assert cmd_session.scalar(select(ResearchMessage)) is None
    request = cmd_session.scalar(select(GatewayIdempotencyRequest))
    assert request.status == "in_progress"
    assert request.receipt_json is None
    assert request.lease_expires_at is not None


def test_runtime_cannot_link_foreign_native_case(cmd_session):
    runtime = _Runtime(cmd_session)
    foreign = runtime.start(
        text="private foreign topic",
        tenant_id="team-b",
        actor_subject_id="alice",
        commit=False,
    )
    cmd_session.commit()

    class ForeignRuntime:
        def start(self, **kwargs):
            return foreign

    with pytest.raises(NotFoundError):
        _send(_gateway(cmd_session, ForeignRuntime()))
    assert cmd_session.scalar(select(ResearchRunSpec)) is None
    assert cmd_session.scalar(select(ResearchConversation)) is None


def test_expired_native_attempt_cannot_publish_and_can_be_retried(cmd_session):
    import app.services.research_gateway as gateway_module

    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    original_now = gateway_module._utcnow
    with pytest.MonkeyPatch.context() as patch:
        runtime.after_start = lambda: patch.setattr(
            gateway_module, "_utcnow", lambda: original_now() + timedelta(hours=1)
        )
        with pytest.raises(ConflictError, match="lease"):
            _send(gateway)
    assert cmd_session.scalar(select(ResearchRun)) is None
    assert cmd_session.scalar(select(ResearchRunSpec)) is None
    request = cmd_session.scalar(select(GatewayIdempotencyRequest))
    request.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    cmd_session.commit()
    runtime.after_start = None
    _send(gateway)
    assert len(runtime.calls) == 2
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 1
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)).attempt == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt", 2),
        ("status", "completed"),
        ("request_fingerprint", "b" * 64),
        ("client_key", "different-original-key"),
    ],
)
def test_changed_lease_owner_or_request_scope_cannot_publish(cmd_session, field, value):
    runtime = _Runtime(cmd_session)

    def change_lease():
        request = cmd_session.scalar(select(GatewayIdempotencyRequest))
        setattr(request, field, value)
        cmd_session.flush()

    runtime.after_start = change_lease
    with pytest.raises(ConflictError, match="lease|scope"):
        _send(_gateway(cmd_session, runtime))
    assert cmd_session.scalar(select(ResearchRunSpec)) is None
    assert cmd_session.scalar(select(ResearchRun)) is None


def test_active_failed_lease_returns_conflict_without_second_provider_call(cmd_session):
    runtime = _Runtime(cmd_session)

    def fail():
        raise RuntimeError("failed intake")

    runtime.after_start = fail
    gateway = _gateway(cmd_session, runtime)
    with pytest.raises(RuntimeError):
        _send(gateway)
    runtime.after_start = None
    with pytest.raises(ConflictError, match="in_progress"):
        _send(gateway)
    assert len(runtime.calls) == 1


def test_replay_recovers_receipt_from_records_instead_of_trusting_mutable_cache(
    cmd_session,
):
    runtime = _Runtime(cmd_session)
    gateway = _gateway(cmd_session, runtime)
    receipt = _send(gateway)
    request = cmd_session.scalar(select(GatewayIdempotencyRequest))
    request.receipt_json = {"unsafe": "untrusted cache"}
    cmd_session.commit()
    assert _send(gateway) == receipt
    assert len(runtime.calls) == 1
    assert len(list(cmd_session.scalars(select(RoleEvent)))) == 4


def test_same_command_key_conflicts_on_different_command_or_target(cmd_session):
    gateway = _gateway(cmd_session)
    first = _send(gateway)
    second = _send(gateway, text="new topic", key="new-start")
    gateway.issue_command(
        ALICE, first.conversation_id, first.run_spec_id, "pause", "cmd"
    )
    with pytest.raises(ConflictError):
        gateway.issue_command(
            ALICE, first.conversation_id, first.run_spec_id, "resume", "cmd"
        )
    with pytest.raises(ConflictError):
        gateway.issue_command(
            ALICE, second.conversation_id, second.run_spec_id, "pause", "cmd"
        )
    with pytest.raises(NotFoundError):
        gateway.issue_command(
            ALICE, second.conversation_id, first.run_spec_id, "pause", "cross-target"
        )
    assert len(list(cmd_session.scalars(select(GatewayCommand)))) == 1


def test_read_conversation_returns_original_messages_and_safe_role_events(cmd_session):
    gateway = _gateway(cmd_session)
    receipt = _send(gateway, text="line one\nline two\tcontext")
    view = gateway.read_conversation(ALICE, receipt.conversation_id)
    view = view.model_dump(mode="json")
    assert view["conversation_id"] == str(receipt.conversation_id)
    assert view["messages"][0]["text"] == "line one\nline two\tcontext"
    assert len(view["roles"]) == 4
    assert view["latest_sequence"] >= 4
    assert view["event_retention_floor"] == 1
    assert all("source_key" not in item for item in view["events"])
