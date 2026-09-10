"""Research-worker liveness, kept separate from the immutable Case ledger."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import ResearchWorkerHeartbeat


class WorkerHeartbeatStatus(TypedDict):
    status: str
    last_seen_at: str | None
    mode: str | None
    state: str | None
    configuration_status: str
    configuration_issues: dict[str, list[str]]


class WorkerHeartbeatService:
    stale_after = timedelta(minutes=5)
    max_clock_skew = timedelta(seconds=30)
    healthy_states = frozenset({"polling", "executing", "idle", "running"})

    def __init__(self, session: Session) -> None:
        self._session = session

    def touch(
        self,
        *,
        worker_id: str,
        mode: str,
        state: str,
        configuration_status: str = "unknown",
        configuration_issues: dict[str, list[str]] | None = None,
        seen_at: datetime | None = None,
    ) -> ResearchWorkerHeartbeat:
        now = seen_at or datetime.now(timezone.utc)
        heartbeat = self._session.get(ResearchWorkerHeartbeat, worker_id)
        if heartbeat is None:
            heartbeat = ResearchWorkerHeartbeat(
                worker_id=worker_id,
                mode=mode,
                state=state,
                configuration_status=configuration_status,
                configuration_issues=dict(configuration_issues or {}),
                started_at=now,
                last_seen_at=now,
            )
            self._session.add(heartbeat)
        else:
            heartbeat.mode = mode
            heartbeat.state = state
            heartbeat.configuration_status = configuration_status
            heartbeat.configuration_issues = dict(configuration_issues or {})
            heartbeat.last_seen_at = now
        self._session.flush()
        return heartbeat

    def latest(self, *, service: str = "research-worker") -> ResearchWorkerHeartbeat | None:
        return self._session.scalar(
            select(ResearchWorkerHeartbeat)
            .where(ResearchWorkerHeartbeat.worker_id.startswith(f"{service}:"))
            .order_by(
                ResearchWorkerHeartbeat.last_seen_at.desc()
            ).limit(1)
        )

    def status(
        self,
        *,
        now: datetime | None = None,
        service: str = "research-worker",
    ) -> WorkerHeartbeatStatus:
        heartbeats = list(
            self._session.scalars(
                select(ResearchWorkerHeartbeat)
                .where(ResearchWorkerHeartbeat.worker_id.startswith(f"{service}:"))
                .order_by(ResearchWorkerHeartbeat.last_seen_at.desc())
                .limit(100)
            )
        )
        if not heartbeats:
            return {
                "status": "unavailable",
                "last_seen_at": None,
                "mode": None,
                "state": None,
                "configuration_status": "unknown",
                "configuration_issues": {},
            }
        observed_at = now or datetime.now(timezone.utc)
        selected = heartbeats[0]
        fresh_loop_instances: list[ResearchWorkerHeartbeat] = []
        for candidate in heartbeats:
            candidate_seen_at = self._as_utc(candidate.last_seen_at)
            age = observed_at - candidate_seen_at
            if (
                candidate.mode == "loop"
                and -self.max_clock_skew <= age <= self.stale_after
            ):
                fresh_loop_instances.append(candidate)
        available_instances = [
            candidate
            for candidate in fresh_loop_instances
            if candidate.state in self.healthy_states
        ]
        if available_instances:
            selected = available_instances[0]
        configuration_instances = fresh_loop_instances or [selected]
        configuration_statuses = {
            candidate.configuration_status or "unknown"
            for candidate in configuration_instances
        }
        configuration_status = (
            next(iter(configuration_statuses))
            if len(configuration_statuses) == 1
            else "mixed"
        )
        configuration_issues = {
            key: sorted(
                {
                    str(issue)
                    for candidate in configuration_instances
                    for issue in (candidate.configuration_issues or {}).get(key, [])
                }
            )
            for key in ("missing", "invalid")
        }
        last_seen_at = self._as_utc(selected.last_seen_at)
        return {
            "status": "available" if available_instances else "stale",
            "last_seen_at": last_seen_at.isoformat(),
            "mode": selected.mode,
            "state": selected.state,
            "configuration_status": configuration_status,
            "configuration_issues": configuration_issues,
        }

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        # SQLite returns naive datetimes even for timezone-aware columns.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
