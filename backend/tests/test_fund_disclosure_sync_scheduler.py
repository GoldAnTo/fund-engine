from __future__ import annotations

from datetime import date, datetime, timezone

from app.models.ledger import ResearchCase
from app.services.fund_disclosure_sync import FundDisclosureSyncService
from app.services.fund_disclosure_sync_scheduler import FundDisclosureSyncScheduler


def _case(session) -> ResearchCase:
    case = ResearchCase(
        title="定时基金披露补充",
        industry_topic="事件",
        created_by="tester",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    return case


class _UnavailableClientFactory:
    def __call__(self):
        raise RuntimeError("provider unavailable")


def test_scheduler_creates_one_replayable_weekly_run_and_records_provider_failure(session) -> None:
    case = _case(session)
    FundDisclosureSyncService(session).save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        report_period=date(2025, 6, 30),
        change_reason="每周补充历史披露",
    )
    scheduler = FundDisclosureSyncScheduler(session, client_factory=_UnavailableClientFactory())
    due = datetime(2026, 8, 10, 1, 5, tzinfo=timezone.utc)  # Shanghai Monday 09:05

    created = scheduler.dispatch_due(now=due)

    assert len(created) == 1
    assert created[0].trigger == "scheduled"
    assert created[0].events[-1].stage == "failed"
    assert scheduler.dispatch_due(now=due) == []


def test_next_due_at_is_monday_or_first_calendar_day_at_shanghai_nine() -> None:
    assert FundDisclosureSyncScheduler.next_due_at(
        "weekly", now=datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    assert FundDisclosureSyncScheduler.next_due_at(
        "monthly", now=datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
