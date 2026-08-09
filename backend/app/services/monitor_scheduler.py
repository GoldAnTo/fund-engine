"""Turn due, active CaseMonitor versions into durable scheduled ResearchRuns."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion
from app.services.auto_research import AutoResearchService


class MonitorScheduler:
    def __init__(self, session: Session) -> None:
        self._session = session

    def dispatch_due(self, *, now: datetime | None = None) -> list[ResearchRun]:
        now = now or datetime.now(timezone.utc)
        latest: dict[object, CaseMonitorVersion] = {}
        for monitor in self._session.scalars(select(CaseMonitorVersion).order_by(CaseMonitorVersion.research_case_id, CaseMonitorVersion.version.desc())):
            latest.setdefault(monitor.research_case_id, monitor)
        created: list[ResearchRun] = []
        for monitor in latest.values():
            if monitor.status != "active" or not self._is_due(monitor.frequency, now) or self._already_dispatched(monitor, now):
                continue
            created.append(AutoResearchService(self._session).start(monitor.research_case_id, max_rounds=3, budget=monitor.budget, monitor_version_id=monitor.id, trigger="schedule", commit=False))
        return created

    def _already_dispatched(self, monitor: CaseMonitorVersion, now: datetime) -> bool:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._session.scalar(select(ResearchRun.id).where(ResearchRun.monitor_version_id == monitor.id).where(ResearchRun.created_at >= start).limit(1)) is not None

    @staticmethod
    def _is_due(frequency: str, now: datetime) -> bool:
        targets = {"weekday_08_30": (8, 30, True), "weekday_12_30": (12, 30, True), "daily_20_00": (20, 0, False)}
        target = targets.get(frequency)
        if target is None:
            return False
        hour, minute, weekday_only = target
        return (not weekday_only or now.weekday() < 5) and (now.hour, now.minute) >= (hour, minute)
