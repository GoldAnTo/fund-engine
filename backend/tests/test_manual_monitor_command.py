"""Audited, version-bound manual monitor commands; no external providers."""
import uuid

from sqlalchemy import select

from app.models.operational import ResearchRun
from app.models.research_monitor import ResearchRunEvent
from .test_case_monitor_api import _case_with_confirmed_factor, _monitor_payload


def _setup(client, session):
    case, factor = _case_with_confirmed_factor(session)
    path = f'/api/v1/research-cases/{case.id}/monitor'
    assert client.put(path, json=_monitor_payload(factor.id)).status_code == 200
    return case, path


def _command(**overrides):
    return {'actor': 'human:manual', 'change_reason': '核对当前计划后启动一次',
            'expected_version': 1, 'idempotency_key': str(uuid.uuid4()), **overrides}


def test_manual_monitor_retry_returns_one_run_with_actor_audit(cmd_client, cmd_session):
    case, path = _setup(cmd_client, cmd_session)
    command = _command()
    first = cmd_client.post(f'{path}/runs', json=command)
    assert first.status_code == 201, first.text
    repeated = cmd_client.post(f'{path}/runs', json=command)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()['id'] == first.json()['id']
    rows = cmd_session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case.id)).all()
    assert len(rows) == 1
    scope = cmd_session.scalar(select(ResearchRunEvent).where(
        ResearchRunEvent.run_id == rows[0].id, ResearchRunEvent.stage == 'scope'))
    assert scope.payload_json['requested_by'] == command['actor']
    assert scope.payload_json['request_reason'] == command['change_reason']
    assert scope.payload_json['monitor_expected_version'] == 1
    changed = cmd_client.post(f'{path}/runs', json={**command, 'change_reason': '不同操作'})
    assert changed.status_code == 409


def test_manual_monitor_stale_version_creates_no_run(cmd_client, cmd_session):
    case, path = _setup(cmd_client, cmd_session)
    assert cmd_client.post(f'{path}/paused', json={'actor': 'other', 'change_reason': '范围待核对'}).status_code == 200
    stale = cmd_client.post(f'{path}/runs', json=_command())
    assert stale.status_code == 409, stale.text
    assert cmd_session.scalar(select(ResearchRun.id).where(ResearchRun.research_case_id == case.id)) is None


def test_failed_manual_command_does_not_leave_a_poisoned_retry_slot(cmd_client, cmd_session):
    case, path = _setup(cmd_client, cmd_session)
    command = _command(expected_version=2)
    assert cmd_client.post(f'{path}/runs', json=command).status_code == 409
    corrected = cmd_client.post(f'{path}/runs', json={**command, 'expected_version': 1})
    assert corrected.status_code == 201, corrected.text
    assert len(cmd_session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case.id)).all()) == 1


import pytest


@pytest.mark.pg_only
@pytest.mark.parametrize('changed_content', [False, True])
def test_concurrent_manual_command_has_one_winner(engine, session, monkeypatch, changed_content):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import sessionmaker
    from app.errors import ConflictError
    from app.models.operational import ResearchTask, Job
    from app.repositories.operational import IdempotencyRepository
    from app.schemas.v1.case_monitor import StartManualMonitorRunRequest
    from app.services.auto_research import AutoResearchService
    from app.services.manual_monitor_command import start_manual_monitor_command
    from .test_monitor_scheduler import _configured_monitor

    case, _ = _configured_monitor(session)
    case_id = case.id
    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    command = _command()
    first_entered, release_first, second_attempted, second_finished = Event(), Event(), Event(), Event()
    original_start = AutoResearchService.start_from_monitor
    original_acquire = IdempotencyRepository.acquire

    def held_start(service, *args, **kwargs):
        first_entered.set()
        assert release_first.wait(5)
        return original_start(service, *args, **kwargs)

    def observed_acquire(repository, **kwargs):
        if first_entered.is_set():
            second_attempted.set()
        return original_acquire(repository, **kwargs)

    monkeypatch.setattr(AutoResearchService, 'start_from_monitor', held_start)
    monkeypatch.setattr(IdempotencyRepository, 'acquire', observed_acquire)

    def submit(second=False):
        body = {**command, **({'change_reason': '另一个操作'} if second and changed_content else {})}
        try:
            with factory() as db:
                try:
                    result = start_manual_monitor_command(db, case_id, 'race-team', StartManualMonitorRunRequest(**body))
                    return result["id"]
                except ConflictError:
                    return 'conflict'
        finally:
            if second:
                second_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit)
        assert first_entered.wait(5)
        second = pool.submit(submit, True)
        try:
            assert second_attempted.wait(5)
            completed_before_commit = second_finished.wait(0.5)
        finally:
            release_first.set()
        first_id, second_id = first.result(timeout=5), second.result(timeout=5)
    assert not completed_before_commit
    assert second_id == ('conflict' if changed_content else first_id)
    with factory() as db:
        runs = db.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all()
        assert len(runs) == 1
        assert len(db.scalars(select(ResearchTask).where(ResearchTask.run_id == runs[0].id)).all()) == 4
        assert len(db.scalars(select(Job).where(Job.target_id == runs[0].id)).all()) == 1
        assert len(db.scalars(select(ResearchRunEvent).where(ResearchRunEvent.run_id == runs[0].id, ResearchRunEvent.stage == 'scope')).all()) == 1
