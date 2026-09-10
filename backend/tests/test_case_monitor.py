from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import sessionmaker

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


def test_default_monitor_reuses_only_the_exact_canonical_frozen_scope(session) -> None:
    case, old_factor = _case_with_confirmed_factor(session)
    current_factor = Thesis(
        research_case_id=case.id,
        statement="当前冻结范围中的因素",
        created_by="human:lin",
        created_at=datetime.now(timezone.utc),
        creator_type="human",
        review_state="confirmed",
    )
    session.add(current_factor)
    session.flush()
    service = CaseMonitorService(session)
    old_factor_monitor = service.save(
        case.id,
        actor="human:lin",
        config=_monitor_config(
            old_factor.id,
            allowed_source_types=["licensed_provider"],
        ),
    )

    current_factor_monitor = service.ensure_default(
        case.id,
        actor="system:event-research",
        factor_ids=[current_factor.id],
        allowed_source_types=["licensed_provider"],
    )
    current_source_monitor = service.ensure_default(
        case.id,
        actor="system:event-research",
        factor_ids=[current_factor.id],
        allowed_source_types=[
            "uploaded_file",
            "company_disclosure",
            "uploaded_file",
        ],
    )
    replay = service.ensure_default(
        case.id,
        actor="system:event-research",
        factor_ids=[current_factor.id],
        allowed_source_types=[
            "company_disclosure",
            "uploaded_file",
            "company_disclosure",
        ],
    )

    assert [
        old_factor_monitor.version,
        current_factor_monitor.version,
        current_source_monitor.version,
    ] == [1, 2, 3]
    assert replay.id == current_source_monitor.id
    assert current_factor_monitor.factor_ids == [str(current_factor.id)]
    assert current_source_monitor.allowed_source_types == [
        "company_disclosure",
        "uploaded_file",
    ]
    history = list(
        session.scalars(
            select(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case.id)
            .order_by(CaseMonitorVersion.version)
        )
    )
    assert [monitor.id for monitor in history] == [
        old_factor_monitor.id,
        current_factor_monitor.id,
        current_source_monitor.id,
    ]


@pytest.mark.pg_only
def test_concurrent_default_monitor_creation_reuses_one_active_version(
    engine, session
) -> None:
    case, factor = _case_with_confirmed_factor(session)
    session.commit()
    case_id, factor_id = case.id, factor.id
    session_local = sessionmaker(bind=engine, future=True)
    first = session_local()
    first_monitor = CaseMonitorService(first).ensure_default(
        case_id,
        actor="system:first-worker",
        factor_ids=[factor_id],
        allowed_source_types=["company_disclosure"],
    )
    second_started = Event()
    second_pid: list[int] = []

    def create_same_monitor() -> object:
        with session_local() as second:
            second.execute(text("SET LOCAL lock_timeout = '5s'"))
            second_pid.append(int(second.scalar(text("SELECT pg_backend_pid()"))))
            second_started.set()
            monitor = CaseMonitorService(second).ensure_default(
                case_id,
                actor="system:second-worker",
                factor_ids=[factor_id],
                allowed_source_types=["company_disclosure"],
            )
            second.commit()
            return monitor.id

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(create_same_monitor)
            assert second_started.wait(timeout=5)
            deadline = monotonic() + 5
            wait_event_type = None
            while monotonic() < deadline:
                with engine.connect() as observer:
                    wait_event_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :pid"
                        ),
                        {"pid": second_pid[0]},
                    )
                if wait_event_type == "Lock":
                    break
                second_started.wait(timeout=0.02)
            assert wait_event_type == "Lock"
            first.commit()
            assert future.result(timeout=5) == first_monitor.id
    finally:
        first.rollback()
        first.close()

    with session_local() as check:
        assert check.scalar(
            select(func.count()).select_from(CaseMonitorVersion).where(
                CaseMonitorVersion.research_case_id == case_id
            )
        ) == 1


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
