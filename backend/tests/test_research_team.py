"""Professional roles are durable tasks, not renamed execution stages."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.errors import NotFoundError
from tests.test_research_gateway_service import ALICE, BOB, FOREIGN, _gateway, _send


def _spec(session):
    from app.models.research_gateway import ResearchRunSpec
    receipt = _send(_gateway(session))
    return session.get(ResearchRunSpec, receipt.run_spec_id)


def test_initial_team_is_durable_idempotent_and_has_real_dependency_graph(cmd_session):
    from app.models.research_team import ProfessionalDependency, ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    service = ResearchTeamService(cmd_session)
    first = service.ensure_team(spec)
    cmd_session.commit()
    tasks = list(cmd_session.scalars(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == spec.id)))
    by_role = {task.role: task for task in tasks}
    assert set(by_role) == {'industry', 'finance', 'strategy', 'quality'}
    assert len({task.id for task in tasks}) == 4
    assert all(task.status == 'queued' and task.attempt == 0 for task in tasks)
    edges = list(cmd_session.scalars(select(ProfessionalDependency)))
    parents = {role: {edge.parent_task_id for edge in edges if edge.task_id == task.id} for role, task in by_role.items()}
    assert parents['industry'] == parents['finance'] == set()
    assert parents['strategy'] == {by_role['industry'].id, by_role['finance'].id}
    assert parents['quality'] == {by_role[r].id for r in ('industry', 'finance', 'strategy')}
    assert service.ensure_team(spec).run_spec_id == first.run_spec_id
    cmd_session.commit()
    assert {task.id for task in cmd_session.scalars(select(ProfessionalTask))} == {task.id for task in tasks}


@pytest.mark.parametrize('actor', [BOB, FOREIGN])
def test_team_read_preserves_private_owner_and_tenant_boundary(cmd_session, actor):
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    service = ResearchTeamService(cmd_session)
    service.ensure_team(spec)
    cmd_session.commit()
    with pytest.raises(NotFoundError):
        service.read(actor, spec.conversation_id, spec.id)


def test_gateway_start_creates_professional_tasks_in_the_same_commit(cmd_session):
    from app.models.research_team import ProfessionalTask, ResearchTeam
    spec = _spec(cmd_session)
    assert cmd_session.get(ResearchTeam, spec.id) is not None
    assert len(list(cmd_session.scalars(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == spec.id)))) == 4


def test_foreign_run_cannot_be_read_through_another_conversation(cmd_session):
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    with pytest.raises(NotFoundError):
        ResearchTeamService(cmd_session).read(ALICE, uuid.uuid4(), spec.id)


def test_professional_output_and_dependency_are_append_only(cmd_session):
    from app.models.ledger import ImmutableLedgerError
    from app.models.research_team import (
        ProfessionalDependency,
        ProfessionalOutput,
        ProfessionalTask,
    )
    spec = _spec(cmd_session)
    task = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == spec.id))
    output = ProfessionalOutput(task_id=task.id, run_spec_id=spec.id, content={'summary': 'frozen'}, evidence_manifest=[], dependency_output_ids=[], input_sha256='a' * 64)
    cmd_session.add(output)
    cmd_session.commit()
    output.content = {'summary': 'changed'}
    with pytest.raises(ImmutableLedgerError):
        cmd_session.flush()
    cmd_session.rollback()
    edge = cmd_session.scalar(select(ProfessionalDependency).where(ProfessionalDependency.run_spec_id == spec.id))
    cmd_session.delete(edge)
    with pytest.raises(ImmutableLedgerError):
        cmd_session.flush()


def test_targeted_followup_creates_only_affected_successors_and_replays_key(cmd_session):
    from app.models.research_team import ProfessionalDependency, ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    service = ResearchTeamService(cmd_session)
    original = {task.role: task for task in cmd_session.scalars(select(ProfessionalTask))}
    receipt = service.message(ALICE, spec.conversation_id, spec.id, text='补充核验产业链价格', recipient='industry', expected_revision=1, idempotency_key='followup-1')
    assert receipt['revision'] == 2
    successor = {task.role: task for task in cmd_session.scalars(select(ProfessionalTask).where(ProfessionalTask.revision == 2))}
    assert set(successor) == {'industry', 'strategy', 'quality'}
    assert successor['industry'].instruction == '补充核验产业链价格'
    assert original['industry'].status == 'cancelled'
    assert original['finance'].status == 'queued'
    parents = set(cmd_session.scalars(select(ProfessionalDependency.parent_task_id).where(ProfessionalDependency.task_id == successor['strategy'].id)))
    assert parents == {successor['industry'].id, original['finance'].id}
    assert service.message(ALICE, spec.conversation_id, spec.id, text='补充核验产业链价格', recipient='industry', expected_revision=1, idempotency_key='followup-1') == receipt
    from app.errors import ConflictError
    with pytest.raises(ConflictError):
        service.message(ALICE, spec.conversation_id, spec.id, text='另一条请求', recipient='industry', expected_revision=1, idempotency_key='followup-1')


def test_pause_revokes_active_lease_and_resume_does_not_fake_completion(cmd_session):
    from datetime import UTC, datetime, timedelta

    from app.models.research_team import ProfessionalTask, ResearchTeam
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    task = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'industry'))
    task.status, task.lease_token = 'running', uuid.uuid4()
    task.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    cmd_session.commit()
    service = ResearchTeamService(cmd_session)
    service.command(ALICE, spec.conversation_id, spec.id, kind='pause', expected_revision=1, idempotency_key='pause-1')
    assert cmd_session.get(ResearchTeam, spec.id).status == 'paused'
    assert task.lease_token is None and task.status == 'queued'
    service.command(ALICE, spec.conversation_id, spec.id, kind='resume', expected_revision=1, idempotency_key='resume-1')
    assert cmd_session.get(ResearchTeam, spec.id).status == 'active'
    assert task.status == 'queued'


def test_stale_team_revision_cannot_submit_new_work(cmd_session):
    from app.errors import ConflictError
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    with pytest.raises(ConflictError):
        ResearchTeamService(cmd_session).message(ALICE, spec.conversation_id, spec.id, text='核验', recipient='team', expected_revision=0, idempotency_key='stale')


def test_cancel_stops_native_run_and_professional_tasks_atomically(cmd_session):
    from app.models.operational import ResearchRun
    from app.models.research_team import ProfessionalTask, ResearchTeam
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    ResearchTeamService(cmd_session).command(ALICE, spec.conversation_id, spec.id, kind='cancel', expected_revision=1, idempotency_key='cancel-1')
    assert cmd_session.get(ResearchTeam, spec.id).status == 'cancelled'
    assert cmd_session.get(ResearchRun, spec.native_run_id).status == 'cancelled'
    assert all(task.status == 'cancelled' for task in cmd_session.scalars(select(ProfessionalTask)))


def test_retry_preserves_the_failed_role_instruction(cmd_session):
    from app.models.research_team import ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec = _spec(cmd_session)
    service = ResearchTeamService(cmd_session)
    service.message(ALICE, spec.conversation_id, spec.id, text='仅核验最近季度，不得混入全年口径', recipient='finance', expected_revision=1, idempotency_key='instruct')
    failed = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'finance', ProfessionalTask.revision == 2))
    failed.status = 'failed'
    cmd_session.commit()
    service.command(ALICE, spec.conversation_id, spec.id, kind='retry', expected_revision=2, task_id=failed.id, idempotency_key='retry')
    retried = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'finance', ProfessionalTask.revision == 3))
    assert retried.instruction == failed.instruction
    assert failed.status == 'failed'


def test_retry_preserves_directed_same_role_history_without_failed_task_edge(cmd_session):
    from tests.test_research_team_read import ACTOR, complete_team

    from app.models.research_team import ProfessionalDependency, ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec, _, outputs = complete_team(cmd_session)
    service = ResearchTeamService(cmd_session)
    service.message(ACTOR, spec.conversation_id, spec.id, text='复核上一版质控判断', recipient='quality', expected_revision=1, idempotency_key='quality-directed')
    failed = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'quality', ProfessionalTask.revision == 2))
    failed.status = 'failed'
    cmd_session.commit()

    service.command(ACTOR, spec.conversation_id, spec.id, kind='retry', expected_revision=2, task_id=failed.id, idempotency_key='quality-retry')

    retried = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'quality', ProfessionalTask.revision == 3))
    parents = set(cmd_session.scalars(select(ProfessionalDependency.parent_task_id).where(ProfessionalDependency.task_id == retried.id)))
    upstream = set(cmd_session.scalars(select(ProfessionalTask.id).where(ProfessionalTask.run_spec_id == spec.id, ProfessionalTask.revision == 1, ProfessionalTask.role.in_(('industry', 'finance', 'strategy')))))
    assert parents == {*upstream, outputs['quality'].task_id}
    assert failed.id not in parents
    assert retried.instruction == failed.instruction
    assert cmd_session.get(type(outputs['quality']), outputs['quality'].id).task_id == outputs['quality'].task_id


def test_upstream_refresh_preserves_downstream_directed_history_edge(cmd_session):
    from tests.test_research_team_read import ACTOR, complete_team

    from app.models.research_team import ProfessionalDependency, ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec, _, outputs = complete_team(cmd_session)
    service = ResearchTeamService(cmd_session)
    service.message(ACTOR, spec.conversation_id, spec.id, text='复核上一版质控判断', recipient='quality', expected_revision=1, idempotency_key='quality-directed')
    quality_with_history = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'quality', ProfessionalTask.revision == 2))
    service.message(ACTOR, spec.conversation_id, spec.id, text='刷新产业链数据', recipient='industry', expected_revision=2, idempotency_key='industry-refresh')

    created = {task.role: task for task in cmd_session.scalars(select(ProfessionalTask).where(ProfessionalTask.revision == 3))}
    parents = set(cmd_session.scalars(select(ProfessionalDependency.parent_task_id).where(ProfessionalDependency.task_id == created['quality'].id)))
    assert parents == {
        created['industry'].id,
        outputs['finance'].task_id,
        created['strategy'].id,
        outputs['quality'].task_id,
    }
    assert quality_with_history.id not in parents
    assert created['quality'].instruction == quality_with_history.instruction
