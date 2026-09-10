"""Resumable safe SSE using short transactions; disconnect never cancels work."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import NamedTuple

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import (
    ResearchActor,
    require_gateway_actor,
    require_research_actor,
)
from app.errors import (
    AuthenticationRequiredError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.models.research_team import ResearchTeam
from app.schemas.v1.research_gateway import (
    ConversationSnapshotDTO,
    GatewayExecutionProgressDTO,
    SafeRoleEventDTO,
)
from app.schemas.v1.research_team import TeamProgressDTO
from app.services.research_gateway import ResearchGateway


class ReplayResult(NamedTuple):
    events: tuple[SafeRoleEventDTO, ...]
    latest_sequence: int
    snapshot_required: bool
    snapshot: ConversationSnapshotDTO | None
    visible_artifacts: frozenset[tuple[str, str, str, str]]
    executions: tuple[GatewayExecutionProgressDTO, ...]
    teams: tuple[TeamProgressDTO, ...] = ()


def resolve_cursor(after_sequence: str | None, last_event_id: str | None) -> int:
    values = []
    for value in (after_sequence, last_event_id):
        if value is None:
            continue
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,18}", value) is None:
            raise ValidationFailedError("invalid event cursor")
        values.append(int(value))
    if len(values) == 2 and values[0] != values[1]:
        raise ValidationFailedError("event cursors must agree")
    return values[0] if values else 0


def replay(*, session_factory: Callable[[], Session], actor: ResearchActor,
           conversation_id: uuid.UUID, after_sequence: int) -> ReplayResult:
    with session_factory() as session:
        view = ResearchGateway(session).read_conversation(
            actor, conversation_id,
        )
        if after_sequence > view.latest_sequence:
            raise ValidationFailedError("event cursor is ahead of conversation")
        gap = after_sequence < view.event_retention_floor - 1
        visible = frozenset((ref.kind, str(ref.id), str(ref.case_id), str(ref.locator_available))
                            for event in view.events for ref in event.artifacts)
        authorized_run_ids = [uuid.UUID(run.run_spec_id) for run in view.runs]
        teams = tuple(TeamProgressDTO(run_spec_id=str(team.run_spec_id), revision=team.revision,
                                     event_sequence=team.event_sequence, status=team.status)
                      for team in session.scalars(select(ResearchTeam).where(
                          ResearchTeam.run_spec_id.in_(authorized_run_ids)).order_by(ResearchTeam.run_spec_id)))
        return ReplayResult(() if gap else tuple(e for e in view.events if e.sequence > after_sequence),
                            view.latest_sequence, gap, view if gap else None, visible,
                            tuple(GatewayExecutionProgressDTO(run_spec_id=run.run_spec_id, execution=run.execution)
                                  for run in view.runs if run.execution is not None), teams)


async def sse_frames(*, session_factory: Callable[[], Session], actor: ResearchActor,
                     conversation_id: uuid.UUID, after_sequence: int,
                     authorization: str, poll_seconds: float = 1.0,
                     heartbeat_seconds: float = 15.0,
                     max_polls: int | None = None) -> AsyncIterator[bytes]:
    """Preauthorize in the route, then revalidate credentials and ownership each poll.

    Database work runs off the ASGI event loop. No Session or transaction survives
    a yield or sleep. A cancellation closes only this iterator, not the native job.
    max_polls is an internal finite-run seam used by lifecycle tests.
    """
    polls = 0
    heartbeat_at = time.monotonic()
    visible_artifacts = None
    execution_payloads: dict[str, str] = {}
    team_payloads: dict[str, str] = {}
    while max_polls is None or polls < max_polls:
        try:
            current = require_gateway_actor(require_research_actor(authorization))
            if current != actor:
                raise PermissionDeniedError("research access changed")
            result = await to_thread.run_sync(lambda cursor=after_sequence: replay(
                session_factory=session_factory, actor=actor,
                conversation_id=conversation_id, after_sequence=cursor,
            ))
        except (AuthenticationRequiredError, PermissionDeniedError, NotFoundError):
            yield b'event: stream_error\ndata: {"code":"access_revoked"}\n\n'
            return
        except Exception:  # noqa: BLE001 - headers sent; contain failures without leaking raw exception details
            # HTTP status is already committed. Never serialize exception text.
            yield b'event: stream_error\ndata: {"code":"projection_unavailable"}\n\n'
            return
        polls += 1
        if result.snapshot_required:
            yield b'event: snapshot_required\ndata: {"reason":"retention_gap"}\n\n'
            return
        if visible_artifacts is not None and visible_artifacts - result.visible_artifacts:
            # Display rights can expire without a new native event. Force the
            # client to drop previously delivered references, not only future ones.
            yield b'event: snapshot_required\ndata: {"reason":"artifact_access_changed"}\n\n'
            return
        visible_artifacts = result.visible_artifacts
        for progress in result.executions:
            payload = progress.model_dump_json()
            if execution_payloads.get(progress.run_spec_id) != payload:
                # This replaces the complete authorized execution state, even
                # when access loss removes every task. It is not a ledger event
                # and must not advance the durable role-event replay cursor.
                yield f"event: execution_progress\ndata: {payload}\n\n".encode()
                execution_payloads[progress.run_spec_id] = payload
        for progress in result.teams:
            payload = progress.model_dump_json()
            if team_payloads.get(progress.run_spec_id) != payload:
                # Invalidate the separately authorized team read. Never include
                # output, instruction, quote or provider details in this stream.
                yield f"event: team_progress\ndata: {payload}\n\n".encode()
                team_payloads[progress.run_spec_id] = payload
        for event in result.events:
            yield f"id: {event.sequence}\nevent: role_event\ndata: {event.model_dump_json()}\n\n".encode()
            after_sequence = event.sequence
            heartbeat_at = time.monotonic()
        # Flush an authenticated first body immediately when already caught up;
        # proxies/browser fetch must not wait 15 seconds to confirm connection.
        if not result.events and (polls == 1 or time.monotonic() - heartbeat_at >= heartbeat_seconds):
            yield b": heartbeat\n\n"
            heartbeat_at = time.monotonic()
        if max_polls is None or polls < max_polls:
            await asyncio.sleep(poll_seconds)
