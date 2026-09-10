from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.models.research_gateway import (
    GatewayCommand,
    GatewayIdempotencyRequest,
    ResearchConversation,
    ResearchMessage,
    ResearchRunSpec,
)
from app.services.event_extraction import EventExtractionProviderError
from tests.test_research_gateway_service import _Runtime

BASE = "/api/v1/research-conversations"


@pytest.fixture
def gateway_client(cmd_session, monkeypatch):
    from app.api.v1 import research_gateway as api
    from app.db import get_db
    from app.gateway_main import app
    from app.services.research_gateway import ResearchGateway

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps(
            {
                "alice-token": {
                    "tenant_id": "team-a",
                    "subject_id": "alice",
                    "roles": [],
                },
                "bob-token": {
                    "tenant_id": "team-a",
                    "subject_id": "bob",
                    "roles": ["case_administrator"],
                },
                "foreign-token": {
                    "tenant_id": "team-b",
                    "subject_id": "alice",
                    "roles": [],
                },
                "legacy-token": "team-a",
            }
        ),
    )
    runtime = _Runtime(cmd_session)
    monkeypatch.setattr(
        api, "ResearchGateway", lambda db: ResearchGateway(db, runtime=runtime)
    )

    def _override_get_db():
        yield cmd_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[api.get_stream_session_factory] = lambda: sessionmaker(
        bind=cmd_session.get_bind()
    )
    try:
        with TestClient(app, headers={"Authorization": "Bearer alice-token"}) as client:
            yield client, runtime
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(api.get_stream_session_factory, None)


def _start(client, key="create-1", text="英伟达最近季度需求变化"):
    return client.post(
        BASE, headers={"Idempotency-Key": key}, json={"initial_message": text}
    )


def test_gateway_routes_require_bearer_before_exposing_data(gateway_client):
    client, _ = gateway_client
    client.headers.pop("Authorization", None)
    response = client.get(BASE)
    assert response.status_code == 401


def test_initial_message_returns_exact_receipt_and_committed_native_run(
    gateway_client, cmd_session
):
    client, runtime = gateway_client
    response = _start(client)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "conversation_id",
        "intent_id",
        "run_spec_id",
        "native_case_id",
        "native_run_id",
        "status",
        "latest_sequence",
    }
    assert body["status"] == "queued"
    assert body["latest_sequence"] == 4
    assert cmd_session.get(ResearchRun, uuid.UUID(body["native_run_id"])) is not None
    assert (
        cmd_session.get(ResearchCase, uuid.UUID(body["native_case_id"])).created_by
        == "human:alice"
    )
    assert len(runtime.calls) == 1


def test_start_and_message_replay_call_native_once_per_distinct_request(
    gateway_client, cmd_session
):
    client, runtime = gateway_client
    first = _start(client)
    assert _start(client).json() == first.json()
    assert _start(client, text="改变请求内容").status_code == 409
    cid = first.json()["conversation_id"]
    path = f"{BASE}/{cid}/messages"
    changed = client.post(
        path,
        headers={"Idempotency-Key": "message-1"},
        json={"text": "调整范围：最近一个季度"},
    )
    assert changed.status_code == 201
    assert (
        client.post(
            path,
            headers={"Idempotency-Key": "message-1"},
            json={"text": "调整范围：最近一个季度"},
        ).json()
        == changed.json()
    )
    assert len(runtime.calls) == 2
    assert len(list(cmd_session.scalars(select(ResearchConversation)))) == 1
    assert len(list(cmd_session.scalars(select(ResearchRunSpec)))) == 2
    assert len(list(cmd_session.scalars(select(ResearchMessage)))) == 2
    assert "英伟达" in runtime.calls[1][0]


def test_unprefixed_message_returns_a_durable_rejected_intent_receipt(
    gateway_client, cmd_session
):
    client, runtime = gateway_client
    start = _start(client).json()
    path = f"{BASE}/{start['conversation_id']}/messages"
    response = client.post(
        path,
        headers={"Idempotency-Key": "unsupported-1"},
        json={"text": "pause and explain the evidence"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "receipt_kind": "intent",
        "conversation_id": start["conversation_id"],
        "intent_id": body["intent_id"],
        "intent_kind": "unsupported",
        "outcome": "rejected",
        "reason_code": "unsupported_in_gateway_p0",
    }
    assert (
        client.post(
            path,
            headers={"Idempotency-Key": "unsupported-1"},
            json={"text": "pause and explain the evidence"},
        ).json()
        == body
    )
    assert len(runtime.calls) == 1
    assert len(list(cmd_session.scalars(select(ResearchRunSpec)))) == 1
    assert len(list(cmd_session.scalars(select(GatewayCommand)))) == 0
    messages = list(cmd_session.scalars(select(ResearchMessage).order_by(ResearchMessage.sequence)))
    assert [(message.message_kind, message.content) for message in messages[-2:]] == [
        ("user", "pause and explain the evidence"),
        ("system", "This request is not supported in Gateway P0."),
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": "team-b"},
        {"subject_id": "bob"},
        {"created_by": "human:bob"},
        {"role_key": "compilation_checks"},
        {"tools": ["browser"]},
        {"prompt": "override"},
    ],
)
def test_start_rejects_client_owned_identity_roles_and_tools(
    gateway_client, cmd_session, extra
):
    client, runtime = gateway_client
    response = client.post(
        BASE,
        headers={"Idempotency-Key": "extra-1"},
        json={"initial_message": "研究主题", **extra},
    )
    assert response.status_code == 422
    assert runtime.calls == []
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)) is None


@pytest.mark.parametrize(
    "headers", [{}, {"Idempotency-Key": " "}, {"Idempotency-Key": "k" * 513}]
)
def test_start_requires_a_bounded_nonblank_idempotency_key(
    gateway_client, cmd_session, headers
):
    client, runtime = gateway_client
    response = client.post(BASE, headers=headers, json={"initial_message": "研究主题"})
    assert response.status_code == 422
    assert runtime.calls == []
    assert cmd_session.scalar(select(ResearchConversation)) is None


@pytest.mark.parametrize("text", ["", " \t\n", "a" * 20_001, "unsafe\x00"])
def test_start_rejects_invalid_input_without_an_orphan_conversation(
    gateway_client, cmd_session, text
):
    client, runtime = gateway_client
    assert _start(client, text=text).status_code == 422
    assert runtime.calls == []
    assert cmd_session.scalar(select(ResearchConversation)) is None
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)) is None


def test_legacy_bearer_cannot_access_any_gateway_entrypoint(gateway_client):
    client, _ = gateway_client
    response = _start(client)
    body = response.json()
    client.headers["Authorization"] = "Bearer legacy-token"
    assert _start(client, key="legacy").status_code == 403
    assert client.get(BASE).status_code == 403
    cid = body["conversation_id"]
    for suffix in ["", "/snapshot", "/events"]:
        assert client.get(f"{BASE}/{cid}{suffix}").status_code == 403


@pytest.mark.parametrize("token", ["bob-token", "foreign-token"])
def test_other_actor_gets_generic_404_for_every_private_conversation_action(
    gateway_client, cmd_session, token
):
    client, runtime = gateway_client
    body = _start(client).json()
    cid, rid = body["conversation_id"], body["run_spec_id"]
    client.headers["Authorization"] = f"Bearer {token}"
    for suffix in ["", "/snapshot", "/events"]:
        response = client.get(f"{BASE}/{cid}{suffix}")
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "research conversation not found"
    assert client.get(BASE).json() == {"conversations": []}
    assert (
        client.post(
            f"{BASE}/{cid}/messages",
            headers={"Idempotency-Key": "foreign"},
            json={"text": "other"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/{cid}/runs/{rid}/commands",
            headers={"Idempotency-Key": "foreign"},
            json={"kind": "pause"},
        ).status_code
        == 404
    )
    assert len(runtime.calls) == 1
    assert len(list(cmd_session.scalars(select(GatewayIdempotencyRequest)))) == 1


def test_snapshot_and_get_share_typed_safe_contract(gateway_client):
    client, _ = gateway_client
    receipt = _start(client).json()
    cid = receipt["conversation_id"]
    response = client.get(f"{BASE}/{cid}/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert set(snapshot) == {
        "conversation_id",
        "title",
        "created_at",
        "updated_at",
        "latest_sequence",
        "event_retention_floor",
        "messages",
        "runs",
        "roles",
        "events",
    }
    assert snapshot["conversation_id"] == cid
    assert len(snapshot["roles"]) == 4
    assert snapshot["messages"][0]["text"] == "英伟达最近季度需求变化"
    assert snapshot["latest_sequence"] >= 4
    assert client.get(f"{BASE}/{cid}").json() == snapshot
    assert client.get(BASE).json()["conversations"][0]["conversation_id"] == cid
    for event in snapshot["events"]:
        assert not (
            {"payload_json", "source_key", "prompt", "tool_arguments", "raw_trace"}
            & event.keys()
        )


def test_unsupported_command_returns_persisted_safe_rejection_and_replays(
    gateway_client, cmd_session
):
    client, runtime = gateway_client
    body = _start(client).json()
    path = f"{BASE}/{body['conversation_id']}/runs/{body['run_spec_id']}/commands"
    response = client.post(
        path, headers={"Idempotency-Key": "cmd-1"}, json={"kind": "pause"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["command_kind"] == "pause"
    assert result["outcome"] == "rejected"
    assert result["reason_code"] == "unsupported_in_gateway_p0"
    assert (
        client.post(
            path, headers={"Idempotency-Key": "cmd-1"}, json={"kind": "pause"}
        ).json()
        == result
    )
    assert (
        client.post(
            path, headers={"Idempotency-Key": "cmd-1"}, json={"kind": "retry"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path,
            headers={"Idempotency-Key": "cmd-2"},
            json={"kind": "pause", "role_key": "scope_identity"},
        ).status_code
        == 422
    )
    assert len(list(cmd_session.scalars(select(GatewayCommand)))) == 1
    assert len(runtime.calls) == 1


@pytest.mark.parametrize(
    ("raised", "status", "code"),
    [
        (
            EventExtractionProviderError("secret-provider-token"),
            503,
            "upstream_unavailable",
        ),
        (ValueError("secret-provider-value"), 422, "validation_failed"),
    ],
)
def test_native_start_errors_are_safe_and_leave_only_the_request_lease(
    gateway_client, cmd_session, raised, status, code
):
    client, runtime = gateway_client

    def fail():
        raise raised

    runtime.after_start = fail
    response = _start(client)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "secret-provider" not in response.text
    assert cmd_session.scalar(select(ResearchConversation)) is None
    assert cmd_session.scalar(select(ResearchRun)) is None
    assert cmd_session.scalar(select(GatewayIdempotencyRequest)).status == "in_progress"


def test_gateway_openapi_contract_uses_strict_requests_and_event_stream(gateway_client):
    client, _ = gateway_client
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert f"{BASE}/{{conversation_id}}/snapshot" in paths
    event_get = paths[f"{BASE}/{{conversation_id}}/events"]["get"]
    assert "text/event-stream" in event_get["responses"]["200"]["content"]
    for name in ["ConversationCreateRequest", "MessageSendRequest", "CommandRequest"]:
        assert spec["components"]["schemas"][name]["additionalProperties"] is False


def test_events_stream_replays_after_cursor_and_closes_sessions_before_sending(
    gateway_client, cmd_session, monkeypatch
):
    from app.api.v1 import research_gateway as api
    from app.gateway_main import app
    from app.services import research_gateway_stream as stream

    client, _ = gateway_client
    receipt = _start(client).json()
    sessions = []

    class TrackingSession(Session):
        was_closed = False

        def close(self):
            self.was_closed = True
            return super().close()

    def factory():
        session = TrackingSession(bind=cmd_session.get_bind())
        sessions.append(session)
        return session

    original = stream.sse_frames

    async def finite_frames(**kwargs):
        assert sessions and all(session.was_closed for session in sessions)
        async for frame in original(**kwargs, max_polls=1, poll_seconds=0):
            assert all(session.was_closed for session in sessions)
            yield frame

    app.dependency_overrides[api.get_stream_session_factory] = lambda: factory
    monkeypatch.setattr(stream, "sse_frames", finite_frames)
    response = client.get(
        f"{BASE}/{receipt['conversation_id']}/events",
        params={"after_sequence": "1"},
        headers={"Last-Event-ID": "1"},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Accel-Buffering"] == "no"
    ids = [
        int(line.removeprefix("id: "))
        for line in response.text.splitlines()
        if line.startswith("id: ")
    ]
    assert ids and ids[0] == 2
    assert ids == sorted(set(ids))
    assert "event: role_event" in response.text
    assert "stream_error" not in response.text
    for line in response.text.splitlines():
        if line.startswith("data: "):
            event = json.loads(line.removeprefix("data: "))
            if "execution" in event:
                assert set(event) == {"run_spec_id", "execution"}
                assert event["run_spec_id"] == receipt["run_spec_id"]
                continue
            if "event_sequence" in event:
                assert set(event) == {"run_spec_id", "revision", "event_sequence", "status"}
                assert event["run_spec_id"] == receipt["run_spec_id"]
                assert event["revision"] == 1 and event["status"] == "active"
                continue
            assert event["sequence"] > 1
            assert (
                not {"prompt", "payload_json", "tool_arguments", "source_key"}
                & event.keys()
            )
    assert all(session.was_closed for session in sessions)
    assert (
        cmd_session.get(ResearchRun, uuid.UUID(receipt["native_run_id"])).status
        == "queued"
    )


@pytest.mark.parametrize(
    ("query", "header"),
    [
        ("-1", None),
        ("not-a-cursor", None),
        ("1", "2"),
        ("9" * 19, None),
        ("999999", None),
    ],
)
def test_invalid_or_future_stream_cursor_fails_before_stream_construction(
    gateway_client, monkeypatch, query, header
):
    from app.services import research_gateway_stream as stream

    client, _ = gateway_client
    receipt = _start(client).json()

    def unexpected_stream(**kwargs):
        pytest.fail("invalid cursor must not construct the stream")

    monkeypatch.setattr(stream, "sse_frames", unexpected_stream)
    response = client.get(
        f"{BASE}/{receipt['conversation_id']}/events",
        params={"after_sequence": query},
        headers={"Last-Event-ID": header} if header is not None else {},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_stream_does_not_accept_a_query_string_credential(gateway_client):
    client, _ = gateway_client
    receipt = _start(client).json()
    client.headers.pop("Authorization", None)
    response = client.get(
        f"{BASE}/{receipt['conversation_id']}/events",
        params={"authorization": "Bearer alice-token", "tenant_id": "team-a"},
    )
    assert response.status_code == 401
