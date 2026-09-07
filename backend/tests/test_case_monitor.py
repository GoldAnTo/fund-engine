from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, update

from app.models.ledger import ImmutableLedgerError, ResearchCase, Thesis
from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion
from app.queries.case_monitor import CaseMonitorQuery
from app.services.case_monitor import (
    CaseMonitorConfig,
    CaseMonitorService,
    ResearchRunEventRepository,
)


def _case_with_confirmed_factor(session) -> tuple[ResearchCase, Thesis]:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="验证监控配置的事件",
        industry_topic="事件研究",
        created_by="tester",
        created_at=now,
    )
    session.add(case)
    session.flush()
    factor = Thesis(
        research_case_id=case.id,
        statement="资本开支指引是否高于市场预期",
        created_by="human:lin",
        created_at=now,
        creator_type="human",
        review_state="confirmed",
    )
    session.add(factor)
    session.flush()
    return case, factor


def _monitor_config(factor_id, **overrides) -> CaseMonitorConfig:
    values = {
        "frequency": "weekday_08_30",
        "factor_ids": [factor_id],
        "allowed_source_types": ["licensed_provider"],
        "next_verification_event": "2026Q1 财报披露",
        "budget": 20,
        "change_reason": "建立首个可复现的事件监控范围",
    }
    values.update(overrides)
    return CaseMonitorConfig(**values)


def test_changed_monitor_configuration_appends_a_new_version(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    service = CaseMonitorService(session)

    first = service.save(
        case.id,
        actor="human:lin",
        config=_monitor_config(factor.id),
    )
    session.commit()
    second = service.save(
        case.id,
        actor="human:lin",
        config=_monitor_config(factor.id, frequency="weekday_12_30"),
    )
    session.commit()

    assert (first.version, second.version) == (1, 2)
    assert session.get(CaseMonitorVersion, first.id).frequency == "weekday_08_30"
    assert session.get(CaseMonitorVersion, second.id).frequency == "weekday_12_30"
    assert session.get(CaseMonitorVersion, first.id).change_reason == "建立首个可复现的事件监控范围"
    assert list(
        session.scalars(
            select(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case.id)
            .order_by(CaseMonitorVersion.version)
        )
    ) == [first, second]


def test_monitor_requires_confirmed_factor_source_and_next_event(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    service = CaseMonitorService(session)

    with pytest.raises(ValueError, match="confirmed factor"):
        service.save(
            case.id,
            actor="human:lin",
            config=_monitor_config(factor.id, factor_ids=[]),
        )
    with pytest.raises(ValueError, match="allowed source"):
        service.save(
            case.id,
            actor="human:lin",
            config=_monitor_config(factor.id, allowed_source_types=[]),
        )
    with pytest.raises(ValueError, match="next verification event"):
        service.save(
            case.id,
            actor="human:lin",
            config=_monitor_config(factor.id, next_verification_event=""),
        )


def test_monitor_versions_are_append_only(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    version = CaseMonitorService(session).save(
        case.id,
        actor="human:lin",
        config=_monitor_config(factor.id),
    )
    session.commit()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(CaseMonitorVersion)
            .where(CaseMonitorVersion.id == version.id)
            .values(frequency="manual")
        )
    with pytest.raises(ImmutableLedgerError):
        session.execute(delete(CaseMonitorVersion).where(CaseMonitorVersion.id == version.id))


def test_pausing_and_resuming_append_new_monitor_versions(session) -> None:
    case, factor = _case_with_confirmed_factor(session)
    service = CaseMonitorService(session)
    first = service.save(case.id, actor="human:lin", config=_monitor_config(factor.id))
    paused = service.set_status(case.id, actor="human:lin", status="paused", reason="暂时停止定时补证")
    resumed = service.set_status(case.id, actor="human:lin", status="active", reason="恢复定时补证")

    assert (first.version, paused.version, resumed.version) == (1, 2, 3)
    assert (first.status, paused.status, resumed.status) == ("active", "paused", "active")


def test_confirmed_factors_uses_the_monitor_instance_passed_by_the_detail_query(session) -> None:
    case, first_factor = _case_with_confirmed_factor(session)
    second_factor = Thesis(
        research_case_id=case.id,
        statement="第二个已确认因素",
        created_by="human:lin",
        created_at=datetime.now(timezone.utc),
        creator_type="human",
        review_state="confirmed",
    )
    session.add(second_factor)
    session.flush()
    service = CaseMonitorService(session)
    first_monitor = service.save(case.id, actor="human:lin", config=_monitor_config(first_factor.id))
    service.save(case.id, actor="human:lin", config=_monitor_config(second_factor.id))

    factors = CaseMonitorQuery(session).confirmed_factors(case.id, first_monitor)

    assert factors == [first_factor]


def test_run_events_are_ordered_and_append_only(session) -> None:
    case, _ = _case_with_confirmed_factor(session)
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        research_case_id=case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=20,
        budget_used=0,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    events = ResearchRunEventRepository(session)

    planned = events.append(
        run.id,
        stage="scope",
        status="started",
        message="已加载监控版本 1",
        payload_json={"monitor_version": 1, "factor_count": 1},
    )
    retrieved = events.append(
        run.id,
        stage="retrieve",
        status="completed",
        message="已排除 2 个未获授权来源",
        payload_json={"excluded_sources": 2},
    )
    session.commit()

    assert (planned.seq, retrieved.seq) == (1, 2)
    assert retrieved.payload_json == {"excluded_sources": 2}
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(type(retrieved))
            .where(type(retrieved).id == retrieved.id)
            .values(message="changed")
        )

@pytest.mark.parametrize("frequency", ["daily:20:00", "daily", "unknown"])
def test_monitor_rejects_frequency_that_scheduler_cannot_execute(session, frequency):
    case, factor = _case_with_confirmed_factor(session)
    with pytest.raises(ValueError, match="unsupported monitor frequency"):
        CaseMonitorService(session).save(case.id, actor="tester",
            config=_monitor_config(factor.id, frequency=frequency))
    assert session.scalar(select(CaseMonitorVersion).where(
        CaseMonitorVersion.research_case_id == case.id)) is None


def test_legacy_invalid_frequency_can_be_paused_but_not_reactivated(session):
    case, factor = _case_with_confirmed_factor(session)
    legacy = CaseMonitorVersion(research_case_id=case.id, version=1, status='active',
        frequency='weekly_monday', factor_ids=[str(factor.id)],
        allowed_source_types=['company_disclosure'], next_verification_event='财报',
        budget=20, changed_by='legacy', change_reason='历史配置', created_at=datetime.now(timezone.utc))
    session.add(legacy)
    session.flush()
    service = CaseMonitorService(session)
    paused = service.set_status(case.id, actor='human', status='paused', reason='停用无效计划')
    assert paused.version == 2
    with pytest.raises(ValueError, match='unsupported monitor frequency'):
        service.set_status(case.id, actor='human', status='active', reason='尝试恢复')
    assert session.scalar(select(CaseMonitorVersion.version).where(
        CaseMonitorVersion.research_case_id == case.id).order_by(CaseMonitorVersion.version.desc()).limit(1)) == 2
