"""Team notifications are scoped invalidations, never private output payloads."""
import asyncio
import json

from sqlalchemy.orm import sessionmaker

from app.api.v1.tenant_context import ResearchActor
from app.services.research_team import ResearchTeamService
from tests.test_research_gateway_automatic_adapter import seed_spec

ACTOR = ResearchActor("team-a", frozenset(), "alice")


def test_team_progress_is_deduplicated_safe_and_does_not_advance_role_cursor(cmd_session, monkeypatch):
    from app.services.research_gateway import ResearchGateway
    from app.services.research_gateway_stream import sse_frames

    spec, _ = seed_spec(cmd_session)
    service = ResearchTeamService(cmd_session)
    service.ensure_team(spec)
    cmd_session.commit()
    cursor = ResearchGateway(cmd_session).read_conversation(ACTOR, spec.conversation_id).latest_sequence
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))

    async def collect():
        frames = []
        async for frame in sse_frames(session_factory=sessionmaker(bind=cmd_session.get_bind()), actor=ACTOR,
                conversation_id=spec.conversation_id, after_sequence=cursor, authorization="Bearer token",
                poll_seconds=0, max_polls=3):
            frames.append(frame.decode())
            if "event: team_progress" in frames[-1] and len([f for f in frames if "team_progress" in f]) == 1:
                service.command(ACTOR, spec.conversation_id, spec.id, kind="pause", expected_revision=1,
                                idempotency_key="stream-pause")
        return frames

    frames = asyncio.run(collect())
    team = [frame for frame in frames if "event: team_progress" in frame]
    assert len(team) == 2
    assert all(not frame.startswith("id:") for frame in team)
    values = [json.loads(frame.split("data: ", 1)[1]) for frame in team]
    assert [value["status"] for value in values] == ["active", "paused"]
    assert all(set(value) == {"run_spec_id", "revision", "event_sequence", "status"} for value in values)
    assert values[0]["event_sequence"] < values[1]["event_sequence"]
    assert not any(frame.startswith("id:") for frame in frames)
    assert ResearchGateway(cmd_session).read_conversation(ACTOR, spec.conversation_id).latest_sequence == cursor
