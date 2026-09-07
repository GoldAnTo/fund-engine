"""Turn due, active CaseMonitor versions into durable scheduled ResearchRuns."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent
from app.services.auto_research import AutoResearchService
from app.services.monitor_frequencies import MONITOR_TARGETS
from app.services.event_research_scope_evidence import lock_event_scope_case


class MonitorScheduler:
    _timezone = ZoneInfo("Asia/Shanghai")
    _targets = MONITOR_TARGETS
    def __init__(self, session: Session) -> None:
        self._session = session

    def dispatch_due(self, *, now: datetime | None = None) -> list[ResearchRun]:
        now = now or datetime.now(timezone.utc)
        # Consistent lock order across Cases prevents competing batch dispatchers
        # from holding each other's Case locks in opposite order.
        case_ids = self._session.scalars(
            select(CaseMonitorVersion.research_case_id).distinct()
            .order_by(CaseMonitorVersion.research_case_id)
        ).all()
        created: list[ResearchRun] = []
        for case_id in case_ids:
            lock_event_scope_case(self._session, case_id)
            # A pause or save may have committed while we waited for the lock.
            monitor = self._session.scalar(
                select(CaseMonitorVersion)
                .where(CaseMonitorVersion.research_case_id == case_id)
                .order_by(CaseMonitorVersion.version.desc()).limit(1)
                .execution_options(populate_existing=True)
            )
            if monitor is None:
                continue
            if monitor.status != "active" or not self._is_due(monitor.frequency, now) or self._already_dispatched(monitor, now):
                continue
            created.append(AutoResearchService(self._session).start(
                monitor.research_case_id, max_rounds=3, budget=monitor.budget,
                monitor_version_id=monitor.id, trigger="schedule", commit=False,
                scope_context={"scheduled_local_date": now.astimezone(self._timezone).date().isoformat()},
            ))
        return created

    def _already_dispatched(self, monitor: CaseMonitorVersion, now: datetime) -> bool:
        local_start = now.astimezone(self._timezone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = local_start.astimezone(timezone.utc)
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        # A configuration/status version change does not authorize a second
        # automatic run in the same Case's daily slot. Manual runs are separate.
        # New runs freeze their dispatch date even if insertion crosses midnight;
        # historical runs without that field retain the created_at fallback.
        return self._session.scalar(
            select(ResearchRun.id)
            .join(ResearchRunEvent, ResearchRunEvent.run_id == ResearchRun.id)
            .where(ResearchRun.research_case_id == monitor.research_case_id)
            .where(or_(
                ResearchRunEvent.payload_json["scheduled_local_date"].as_string()
                == local_start.date().isoformat(),
                and_(
                    ResearchRunEvent.payload_json["scheduled_local_date"].as_string().is_(None),
                    ResearchRun.created_at >= start,
                    ResearchRun.created_at < end,
                ),
            ))
            .where(ResearchRunEvent.stage == "scope")
            .where(ResearchRunEvent.payload_json["trigger"].as_string() == "schedule")
            .limit(1)
        ) is not None

    @classmethod
    def _is_due(cls, frequency: str, now: datetime) -> bool:
        target = cls._targets.get(frequency)
        if target is None:
            return False
        hour, minute, weekday_only = target
        local_now = now.astimezone(cls._timezone)
        return (not weekday_only or local_now.weekday() < 5) and (local_now.hour, local_now.minute) >= (hour, minute)

    @classmethod
    def next_due_at(cls, frequency: str, *, now: datetime | None = None) -> datetime | None:
        target = cls._targets.get(frequency)
        if target is None:
            return None
        now = now or datetime.now(timezone.utc)
        local_now = now.astimezone(cls._timezone)
        hour, minute, weekday_only = target
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        while weekday_only and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
