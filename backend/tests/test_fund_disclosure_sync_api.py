from __future__ import annotations

from datetime import datetime, timezone

from app.models.ledger import ResearchCase
from app.models.fund_disclosure_sync import FundDisclosureSyncConfigVersion, FundDisclosureSyncRun
from app.services.fund_disclosure_sync import FundDisclosureSyncService


def _case(session) -> ResearchCase:
    case = ResearchCase(
        title="基金披露补充配置验证",
        industry_topic="事件研究",
        created_by="tester",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    return case


def test_saving_configurations_appends_versions_and_manual_run_uses_latest(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)

    first = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="每周核验",
    )
    second = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827", "110011"],
        frequency="monthly",
        change_reason="调整范围",
    )
    run = service.start_manual_run(case.id)
    session.commit()

    assert (first.version, second.version, run.config_version_id) == (1, 2, second.id)
    assert session.get(FundDisclosureSyncConfigVersion, first.id).fund_codes == ["005827"]
    assert session.get(FundDisclosureSyncConfigVersion, second.id).frequency == "monthly"
    assert session.get(FundDisclosureSyncRun, run.id).trigger == "manual"
    assert run.fund_codes == ["005827", "110011"]
