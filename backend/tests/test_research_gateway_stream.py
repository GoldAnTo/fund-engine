"""SSE replay, credential revocation and short-session lifecycle."""
import asyncio
import json

import pytest
from sqlalchemy.orm import sessionmaker

from app.api.v1.tenant_context import ResearchActor
from app.errors import ValidationFailedError
from app.models.research_gateway import ResearchConversation
from tests.test_research_gateway_automatic_adapter import seed_spec

ACTOR = ResearchActor("team-a", frozenset(), "alice")


@pytest.mark.parametrize(("query", "header", "expected"), [(None, None, 0), ("0", None, 0), (None, "3", 3), ("4", "4", 4)])
def test_cursor_resolution(query, header, expected):
    from app.services.research_gateway_stream import resolve_cursor
    assert resolve_cursor(query, header) == expected


@pytest.mark.parametrize(("query", "header"), [("-1", None), ("1.1", None), ("1", "2"), (" ", None), ("1e2", None), ("9" * 100, None)])
def test_bad_cursors_are_validation_errors(query, header):
    from app.services.research_gateway_stream import resolve_cursor
    with pytest.raises(ValidationFailedError):
        resolve_cursor(query, header)


def test_stream_replay_closes_session_before_yield_and_respects_cursor(cmd_session, monkeypatch):
    from app.services.research_gateway_stream import sse_frames
    spec, _ = seed_spec(cmd_session)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))
    factory = sessionmaker(bind=cmd_session.get_bind())
    open_sessions = []

    class TrackedSession:
        def __enter__(self):
            self.session = factory()
            open_sessions.append(self.session)
            return self.session
        def __exit__(self, *args):
            self.session.close()
            open_sessions.remove(self.session)

    async def collect():
        frames = []
        async for frame in sse_frames(session_factory=TrackedSession, actor=ACTOR,
                conversation_id=spec.conversation_id, after_sequence=2,
                authorization="Bearer token", poll_seconds=0, max_polls=1):
            assert not open_sessions
            frames.append(frame.decode())
        return frames

    frames = asyncio.run(collect())
    ids = [int(f.splitlines()[0][4:]) for f in frames if "event: role_event" in f]
    assert ids and ids[0] == 3 and ids == sorted(set(ids))
    assert all("prompt" not in frame for frame in frames)


def test_retention_gap_sends_snapshot_required_without_partial_events(cmd_session, monkeypatch):
    from app.services.research_gateway_stream import sse_frames
    spec, _ = seed_spec(cmd_session)
    conversation = cmd_session.get(ResearchConversation, spec.conversation_id)
    conversation.event_retention_floor = 3
    cmd_session.commit()
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))

    async def collect():
        return [f.decode() async for f in sse_frames(
            session_factory=sessionmaker(bind=cmd_session.get_bind()), actor=ACTOR,
            conversation_id=spec.conversation_id, after_sequence=0, authorization="Bearer token",
            poll_seconds=0, max_polls=1)]

    frames = asyncio.run(collect())
    assert len(frames) == 1 and "event: snapshot_required" in frames[0]
    assert "retention_gap" in frames[0]


def test_revoked_credentials_close_stream_without_data_or_database(cmd_session, monkeypatch):
    from app.services.research_gateway_stream import sse_frames
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", "{}")
    import uuid

    def forbidden_factory():
        pytest.fail("credential revocation must be checked before opening database")

    async def collect():
        return [f.decode() async for f in sse_frames(
            session_factory=forbidden_factory, actor=ACTOR, conversation_id=uuid.uuid4(),
            after_sequence=0, authorization="Bearer revoked", poll_seconds=0, max_polls=1)]

    frames = asyncio.run(collect())
    assert frames == ['event: stream_error\ndata: {"code":"access_revoked"}\n\n']


def test_idle_stream_sends_heartbeat_without_duplicate_role_events(cmd_session, monkeypatch):
    from app.services.research_gateway_projection import project_conversation
    from app.services.research_gateway_stream import sse_frames

    spec, _ = seed_spec(cmd_session)
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))

    async def collect():
        return [frame async for frame in sse_frames(
            session_factory=sessionmaker(bind=cmd_session.get_bind()), actor=ACTOR,
            conversation_id=spec.conversation_id, after_sequence=view.latest_sequence,
            authorization="Bearer token", poll_seconds=0, heartbeat_seconds=0, max_polls=2)]

    frames = asyncio.run(collect())
    assert len([frame for frame in frames if b"event: execution_progress" in frame]) == 1
    assert [frame for frame in frames if b"execution_progress" not in frame] == [b": heartbeat\n\n", b": heartbeat\n\n"]


def test_caught_up_stream_flushes_an_immediate_authorized_heartbeat(cmd_session, monkeypatch):
    from app.services.research_gateway_projection import project_conversation
    from app.services.research_gateway_stream import sse_frames

    spec, _ = seed_spec(cmd_session)
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))

    async def collect():
        return [frame async for frame in sse_frames(
            session_factory=sessionmaker(bind=cmd_session.get_bind()), actor=ACTOR,
            conversation_id=spec.conversation_id, after_sequence=view.latest_sequence,
            authorization="Bearer token", poll_seconds=0, max_polls=1)]

    frames = asyncio.run(collect())
    assert frames[0].startswith(b"event: execution_progress\n")
    assert frames[1:] == [b": heartbeat\n\n"]


def test_disconnect_preserves_native_run_and_reconnect_replays_next_event(cmd_session, monkeypatch):
    from app.models.operational import ResearchRun
    from app.services.research_gateway_stream import sse_frames

    spec, run = seed_spec(cmd_session)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))
    factory = sessionmaker(bind=cmd_session.get_bind())
    arguments = {"session_factory": factory, "actor": ACTOR, "conversation_id": spec.conversation_id,
                 "authorization": "Bearer token", "poll_seconds": 0, "max_polls": 1}

    async def disconnect_then_reconnect():
        stream = sse_frames(**arguments, after_sequence=0)
        first = await anext(stream)
        assert first.startswith(b"event: execution_progress\n")
        first = await anext(stream)
        cursor = int(first.decode().splitlines()[0][4:])
        await stream.aclose()
        with factory() as session:
            assert session.get(ResearchRun, run.id).status == "queued"
        replayed = [frame async for frame in sse_frames(**arguments, after_sequence=cursor)]
        first_event = next(frame for frame in replayed if frame.startswith(b"id:"))
        assert int(first_event.decode().splitlines()[0][4:]) == cursor + 1

    asyncio.run(disconnect_then_reconnect())


def test_display_expiry_resets_live_view_instead_of_leaving_stale_evidence(cmd_session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.services import research_gateway_artifacts as artifacts
    from app.services.research_gateway_stream import sse_frames
    from app.services.source_admission import source_contract_is_active
    from tests.test_research_gateway_projection import complete_with_evidence

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    spec, _, _ = complete_with_evidence(cmd_session, expires_at=expires_at)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))

    async def collect():
        frames = []
        async for frame in sse_frames(session_factory=sessionmaker(bind=cmd_session.get_bind()), actor=ACTOR,
                conversation_id=spec.conversation_id, after_sequence=0,
                authorization="Bearer token", poll_seconds=0, heartbeat_seconds=0, max_polls=2):
            frames.append(frame.decode())
            if '"kind":"evidence_link"' in frame.decode():
                monkeypatch.setattr(artifacts, "source_contract_is_active",
                    lambda contract: source_contract_is_active(contract, at=expires_at + timedelta(seconds=1)))
        return frames

    frames = asyncio.run(collect())
    assert 'event: snapshot_required\ndata: {"reason":"artifact_access_changed"}\n\n' in frames
