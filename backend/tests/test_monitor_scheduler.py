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


def _configured_monitor(session):
    case, factor = _case_with_confirmed_factor(session)
    monitor = CaseMonitorService(session).save(case.id, actor="human", config=CaseMonitorConfig(
        frequency="daily_20_00", factor_ids=[factor.id], allowed_source_types=["company_disclosure"],
        next_verification_event="财报", budget=7, change_reason="启用"))
    return case, monitor


def test_manual_run_does_not_consume_the_scheduled_daily_slot(session, monkeypatch):
    from app.services.auto_research import AutoResearchService
    now = datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('app.repositories.auto_research._utcnow', lambda: now)
    case, monitor = _configured_monitor(session)
    manual = AutoResearchService(session).start(case.id, monitor_version_id=monitor.id, commit=False)
    scheduled = MonitorScheduler(session).dispatch_due(now=now)
    assert len(scheduled) == 1
    assert scheduled[0].id != manual.id
    assert MonitorScheduler(session).dispatch_due(now=now) == []


def test_pause_resume_does_not_reset_the_same_cases_daily_slot(session, monkeypatch):
    now = datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('app.repositories.auto_research._utcnow', lambda: now)
    case, monitor = _configured_monitor(session)
    assert len(MonitorScheduler(session).dispatch_due(now=now)) == 1
    service = CaseMonitorService(session)
    service.set_status(case.id, actor="human", status="paused", reason="暂停")
    service.set_status(case.id, actor="human", status="active", reason="恢复")
    assert MonitorScheduler(session).dispatch_due(now=now) == []
    tomorrow = datetime(2026, 9, 8, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('app.repositories.auto_research._utcnow', lambda: tomorrow)
    assert len(MonitorScheduler(session).dispatch_due(now=tomorrow)) == 1


def test_daily_slot_uses_shanghai_day_and_excludes_future_runs(session):
    from app.services.auto_research import AutoResearchService
    now = datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc)
    case, monitor = _configured_monitor(session)
    run = AutoResearchService(session).start(case.id, monitor_version_id=monitor.id, trigger="schedule", commit=False)
    run.created_at = datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)
    session.flush()
    scheduler = MonitorScheduler(session)
    assert scheduler._already_dispatched(monitor, now)
    run.created_at = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    session.flush()
    assert not scheduler._already_dispatched(monitor, now)


import pytest


@pytest.mark.pg_only
def test_concurrent_dispatchers_create_one_daily_run(engine, session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import sessionmaker
    from app.services.auto_research import AutoResearchService

    _configured_monitor(session)
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr('app.repositories.auto_research._utcnow', lambda: now)
    first_start, release_first, second_done = Event(), Event(), Event()
    original = AutoResearchService.start

    def held_start(service, *args, **kwargs):
        if not first_start.is_set():
            first_start.set()
            assert release_first.wait(5)
        return original(service, *args, **kwargs)

    monkeypatch.setattr(AutoResearchService, 'start', held_start)

    def dispatch(second=False):
        try:
            with factory() as db:
                result = MonitorScheduler(db).dispatch_due(now=now)
                db.commit()
                return len(result)
        finally:
            if second:
                second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(dispatch)
        assert first_start.wait(5)
        second = pool.submit(dispatch, True)
        try:
            # Without a transaction lock the second command commits a duplicate
            # while the first is paused immediately before creating its run.
            second_completed_before_release = second_done.wait(1)
        finally:
            release_first.set()
        counts = [first.result(timeout=5), second.result(timeout=5)]
    assert not second_completed_before_release
    assert sorted(counts) == [0, 1]


@pytest.mark.pg_only
@pytest.mark.parametrize('operation', ['dispatch', 'resume'])
def test_monitor_command_waits_for_pending_pause(engine, session, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import sessionmaker

    case, _ = _configured_monitor(session)
    case_id = case.id
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    finished = Event()
    started = Event()
    with factory() as pausing:
        paused = CaseMonitorService(pausing).set_status(case_id, actor='first', status='paused', reason='暂停')
        paused_version = paused.version

        def command():
            started.set()
            try:
                with factory() as db:
                    if operation == 'dispatch':
                        result = len(MonitorScheduler(db).dispatch_due(
                            now=datetime(2026, 9, 7, 12, 1, tzinfo=timezone.utc)))
                    else:
                        result = CaseMonitorService(db).set_status(case_id, actor='second', status='active', reason='恢复').version
                    db.commit()
                    return result
            finally:
                finished.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(command)
            assert started.wait(5)
            try:
                completed_before_commit = finished.wait(1)
            finally:
                pausing.commit()
            result = future.result(timeout=5)
    assert not completed_before_commit
    assert result == (0 if operation == 'dispatch' else paused_version + 1)


def test_scheduled_slot_is_recorded_independently_of_run_creation_clock(session):
    from sqlalchemy import select
    case, monitor = _configured_monitor(session)
    # A queued batch can cross midnight before its run is inserted. Its slot
    # belongs to the dispatch decision, while created_at remains the true audit time.
    slot_time = datetime(2030, 9, 9, 15, 59, tzinfo=timezone.utc)
    scheduler = MonitorScheduler(session)
    runs = scheduler.dispatch_due(now=slot_time)
    assert len(runs) == 1
    scope = session.scalar(select(ResearchRunEvent).where(
        ResearchRunEvent.run_id == runs[0].id, ResearchRunEvent.stage == 'scope'))
    assert scope.payload_json['scheduled_local_date'] == '2030-09-09'
    runs[0].created_at = datetime(2030, 9, 9, 16, 0, tzinfo=timezone.utc)
    session.flush()
    assert scheduler.dispatch_due(now=slot_time) == []
    # Yesterday's slot must not consume today merely because insertion happened
    # just after midnight. Explicit slot metadata takes priority over created_at.
    assert len(scheduler.dispatch_due(now=datetime(2030, 9, 10, 12, 1, tzinfo=timezone.utc))) == 1
