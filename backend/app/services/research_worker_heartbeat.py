"""Research-worker liveness, kept separate from the immutable Case ledger."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import ResearchWorkerHeartbeat


class WorkerHeartbeatService:
    stale_after = timedelta(minutes=5)

    def __init__(self, session: Session) -> None:
        self._session = session

    def touch(
        self,
        *,
        worker_id: str,
        mode: str,
        state: str,
        worker_kind: str = "research_run",
        seen_at: datetime | None = None,
    ) -> ResearchWorkerHeartbeat:
        now = seen_at or datetime.now(timezone.utc)
        heartbeat = self._session.get(ResearchWorkerHeartbeat, worker_id)
        if heartbeat is None:
            heartbeat = ResearchWorkerHeartbeat(
                worker_id=worker_id,
                worker_kind=worker_kind,
                mode=mode,
                state=state,
                started_at=now,
                last_seen_at=now,
            )
            self._session.add(heartbeat)
        else:
            heartbeat.worker_kind = worker_kind
            heartbeat.mode = mode
            heartbeat.state = state
            heartbeat.last_seen_at = now
        self._session.flush()
        return heartbeat

    def latest(self, *, worker_kind: str = "research_run") -> ResearchWorkerHeartbeat | None:
        return self._session.scalar(
            select(ResearchWorkerHeartbeat)
            .where(ResearchWorkerHeartbeat.worker_kind == worker_kind)
            .order_by(
                ResearchWorkerHeartbeat.last_seen_at.desc()
            ).limit(1)
        )

    def status(
        self, *, now: datetime | None = None, worker_kind: str = "research_run"
    ) -> dict[str, str | None]:
        heartbeat = self.latest(worker_kind=worker_kind)
        if heartbeat is None:
            return {
                "status": "unavailable",
                "last_seen_at": None,
                "mode": None,
                "state": None,
            }
        observed_at = now or datetime.now(timezone.utc)
        last_seen_at = heartbeat.last_seen_at
        # SQLite returns naive datetimes even for timezone-aware columns.
        # Heartbeats are always written in UTC, so restore that explicit
        # meaning before comparing them with the current UTC clock.
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
        fresh = last_seen_at >= observed_at - self.stale_after
        available = heartbeat.mode == "loop" and fresh
        return {
            "status": "available" if available else "stale",
            "last_seen_at": last_seen_at.isoformat(),
            "mode": heartbeat.mode,
            "state": heartbeat.state,
        }
