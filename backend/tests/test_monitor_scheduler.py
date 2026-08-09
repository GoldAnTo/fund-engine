from datetime import datetime, timezone

from app.models.research_monitor import ResearchRunEvent
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
from app.services.monitor_scheduler import MonitorScheduler
from .test_case_monitor import _case_with_confirmed_factor


def test_scheduler_queues_only_active_due_monitor_once_per_day(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    monitor = CaseMonitorService(session).save(case.id, actor="human", config=CaseMonitorConfig(frequency="daily_20_00", factor_ids=[factor.id], allowed_source_types=["company_disclosure"], next_verification_event="财报", budget=7, change_reason="启用"))
    now = datetime.now(timezone.utc).replace(hour=20, minute=1)
    created = MonitorScheduler(session).dispatch_due(now=now)
    assert len(created) == 1
    assert created[0].monitor_version_id == monitor.id
    assert session.query(ResearchRunEvent).filter_by(run_id=created[0].id).one().payload_json["trigger"] == "schedule"
    assert MonitorScheduler(session).dispatch_due(now=now) == []
    CaseMonitorService(session).set_status(case.id, actor="human", status="paused", reason="暂停")
    assert MonitorScheduler(session).dispatch_due(now=now) == []
