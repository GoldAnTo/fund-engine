"""Every professional output read rechecks the evidence it actually consumed."""
import hashlib

import pytest
from app.api.v1.tenant_context import ResearchActor
from app.errors import ConflictError
from sqlalchemy import select

from tests.test_research_gateway_artifact_authorization import (
    complete_authorized_evidence,
)
from tests.test_research_gateway_content import _contract

ACTOR = ResearchActor('team-a', frozenset(), 'alice')


def complete_team(session):
    from app.models.research_team import ProfessionalOutput, ProfessionalTask
    from app.services.research_gateway_content import ResearchGatewayContent
    from app.services.research_team import ResearchTeamService
    spec, _, links = complete_authorized_evidence(session)
    ResearchTeamService(session).ensure_team(spec)
    detail = ResearchGatewayContent(session).read_evidence(ACTOR, spec.conversation_id, spec.id, links[0].id)
    tasks = {task.role: task for task in session.scalars(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == spec.id))}
    outputs = {}
    for role in ('industry', 'finance', 'strategy', 'quality'):
        task = tasks[role]
        parents = {'industry': [], 'finance': [], 'strategy': ['industry', 'finance'], 'quality': ['industry', 'finance', 'strategy']}[role]
        content = {'summary': f'{role}的证据判断', 'findings': [{'statement': detail.statement, 'basis': 'supported', 'citations': [{'evidence_link_id': detail.evidence_link_id, 'quote': detail.quote}]}], 'gaps': [], 'checks': [], 'limitations': ['未经人工复核']}
        if role == 'quality':
            content['checks'] = [{'kind': kind, 'status': 'warning', 'detail': '仍需人工确认', 'task_ids': [str(tasks[parent].id) for parent in parents]} for kind in ['citations', 'periods', 'units', 'source_independence', 'counter_evidence', 'completeness']]
        output = ProfessionalOutput(task_id=task.id, run_spec_id=spec.id, content=content, evidence_manifest=[{'evidence_link_id': detail.evidence_link_id, 'content_sha256': detail.content_sha256, 'quote_sha256': hashlib.sha256(detail.quote.encode()).hexdigest()}], dependency_output_ids=[str(outputs[parent].id) for parent in parents], input_sha256='a' * 64)
        session.add(output)
        task.status = 'succeeded'
        session.flush()
        outputs[role] = output
    session.commit()
    return spec, links, outputs


def test_team_output_exposes_citations_only_while_source_is_authorized(cmd_session):
    from app.services.research_team import ResearchTeamService
    spec, links, _ = complete_team(cmd_session)
    service = ResearchTeamService(cmd_session)
    visible = service.read(ACTOR, spec.conversation_id, spec.id)
    assert len(visible['tasks']) == 4
    assert all(task['output_state'] == 'available' and task['output'] for task in visible['tasks'])
    contract = _contract(cmd_session, links[0])
    # Simulate a rights revocation in the disposable test database, as the
    # existing Gateway source-authorization regression suite does.
    from sqlalchemy import text
    cmd_session.execute(text('UPDATE source_contracts SET allow_display = 0 WHERE id = :id'), {'id': contract.id.hex})
    cmd_session.commit()
    withdrawn = service.read(ACTOR, spec.conversation_id, spec.id)
    assert all(task['output'] is None and task['output_state'] == 'withheld' for task in withdrawn['tasks'])


def test_human_review_is_bound_to_current_authorized_outputs_and_identity(cmd_session):
    from app.models.research_team import ProfessionalReview
    from app.services.research_team import ResearchTeamService
    spec, _, outputs = complete_team(cmd_session)
    service = ResearchTeamService(cmd_session)
    ids = [output.id for output in outputs.values()]
    receipt = service.review(ACTOR, spec.conversation_id, spec.id, expected_revision=1, output_ids=ids, decision='approved', comment='已逐项检查原文与期间', idempotency_key='review-1')
    record = cmd_session.scalar(select(ProfessionalReview))
    assert record.reviewed_by == 'alice'
    assert set(record.output_ids) == {str(value) for value in ids}
    assert service.review(ACTOR, spec.conversation_id, spec.id, expected_revision=1, output_ids=ids, decision='approved', comment='已逐项检查原文与期间', idempotency_key='review-1') == receipt
    service.message(ACTOR, spec.conversation_id, spec.id, text='重新核验单位', recipient='finance', expected_revision=1, idempotency_key='followup')
    with pytest.raises(ConflictError):
        service.review(ACTOR, spec.conversation_id, spec.id, expected_revision=1, output_ids=ids, decision='approved', comment='仍是旧版本', idempotency_key='review-stale')


def test_directed_question_receives_its_previous_completed_role_output(cmd_session):
    from app.models.research_team import ProfessionalDependency, ProfessionalTask
    from app.services.research_team import ResearchTeamService
    spec, _, outputs = complete_team(cmd_session)
    ResearchTeamService(cmd_session).message(ACTOR, spec.conversation_id, spec.id, text='上一版的收入属于哪个期间？', recipient='finance', expected_revision=1, idempotency_key='grounded-question')
    successor = cmd_session.scalar(select(ProfessionalTask).where(ProfessionalTask.role == 'finance', ProfessionalTask.revision == 2))
    parents = set(cmd_session.scalars(select(ProfessionalDependency.parent_task_id).where(ProfessionalDependency.task_id == successor.id)))
    assert parents == {outputs['finance'].task_id}
