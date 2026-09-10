from datetime import datetime, timezone

from app.models.research_monitor import ResearchRunEvent
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
from app.services.monitor_scheduler import MonitorScheduler
from .test_case_monitor import _case_with_confirmed_factor


def test_scheduler_queues_only_active_due_monitor_once_per_day(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    monitor = CaseMonitorService(session).save(case.id, actor="human", config=CaseMonitorConfig(frequency="daily_20_00", factor_ids=[factor.id], allowed_source_types=["company_disclosure"], next_verification_event="财报", budget=7, change_reason="启用"))
    now = datetime.now(timezone.utc).replace(hour=12, minute=1)
    created = MonitorScheduler(session).dispatch_due(now=now)
    assert len(created) == 1
    assert created[0].monitor_version_id == monitor.id
    assert session.query(ResearchRunEvent).filter_by(run_id=created[0].id).one().payload_json["trigger"] == "schedule"
    assert MonitorScheduler(session).dispatch_due(now=now) == []
    CaseMonitorService(session).set_status(case.id, actor="human", status="paused", reason="暂停")
    assert MonitorScheduler(session).dispatch_due(now=now) == []


def test_next_due_time_is_calculated_in_shanghai_time() -> None:
    # Friday 08:31 in Shanghai: the next weekday 08:30 is Monday, not Saturday.
    now = datetime(2026, 8, 7, 0, 31, tzinfo=timezone.utc)

    assert MonitorScheduler.next_due_at("weekday_08_30", now=now) == datetime(
        2026, 8, 10, 0, 30, tzinfo=timezone.utc
    )


def test_five_minute_monitor_dispatches_once_per_time_bucket(
    session, monkeypatch
) -> None:
    monkeypatch.setenv("LIVE_ACCEPTANCE_FAST_SCHEDULER", "true")
    case, factor = _case_with_confirmed_factor(session)
    monitor = CaseMonitorService(session).save(
        case.id,
        actor="human",
        config=CaseMonitorConfig(
            frequency="acceptance_every_5_minutes",
            factor_ids=[factor.id],
            allowed_source_types=["company_disclosure"],
            next_verification_event="验收调度窗口",
            budget=7,
            change_reason="验证 scheduler 的真实调度与重启恢复",
        ),
    )
    now = datetime(2026, 8, 17, 4, 12, 30, tzinfo=timezone.utc)

    first = MonitorScheduler(session).dispatch_due(now=now)

    assert len(first) == 1
    assert first[0].monitor_version_id == monitor.id
    assert MonitorScheduler(session).dispatch_due(
        now=now.replace(minute=14, second=59)
    ) == []
    assert len(
        MonitorScheduler(session).dispatch_due(now=now.replace(minute=15, second=0))
    ) == 1


def test_five_minute_monitor_reports_next_bucket_boundary(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_ACCEPTANCE_FAST_SCHEDULER", "true")
    now = datetime(2026, 8, 17, 4, 12, 30, tzinfo=timezone.utc)

    assert MonitorScheduler.next_due_at(
        "acceptance_every_5_minutes", now=now
    ) == datetime(
        2026, 8, 17, 4, 15, tzinfo=timezone.utc
    )


def test_acceptance_frequency_is_inert_without_explicit_flag(monkeypatch) -> None:
    monkeypatch.delenv("LIVE_ACCEPTANCE_FAST_SCHEDULER", raising=False)
    now = datetime(2026, 8, 17, 4, 12, 30, tzinfo=timezone.utc)

    assert MonitorScheduler._is_due("acceptance_every_5_minutes", now) is False
    assert (
        MonitorScheduler.next_due_at("acceptance_every_5_minutes", now=now) is None
    )
