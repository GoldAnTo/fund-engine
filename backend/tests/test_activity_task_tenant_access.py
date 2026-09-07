import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.ledger import ResearchCase
from app.models.events import DomainEvent
from app.models.operational import TaskItem
from app.repositories.operational import TaskRepository
from app.repositories.outbox import emit_event
from app.services.proposals import ProposalService
from tests.tenant_admission import admit_case


@pytest.fixture
def scoped(cmd_session, cmd_client, monkeypatch):
    monkeypatch.setenv('RESEARCH_TENANT_TOKENS', '{"test-tenant-token":"test-team","foreign":"other-team"}')
    cases = []
    for tenant in ['test-team', 'test-team', 'other-team', None]:
        case = ResearchCase(title='case', industry_topic='test', created_by='test', created_at=datetime.now(timezone.utc))
        cmd_session.add(case)
        cmd_session.flush()
        if tenant:
            admit_case(cmd_session, case.id, tenant_id=tenant)
        cases.append(case)
    cmd_session.commit()
    return cases


@pytest.mark.parametrize('path', ['/tasks', '/activity', '/evidence-changes'])
@pytest.mark.parametrize('token,status', [('',401),('Bearer invalid',403)])
def test_feeds_require_credentials(cmd_client, path, token, status):
    assert cmd_client.get('/api/v1'+path, headers={'Authorization':token}).status_code == status


def test_tasks_scope_and_denied_writes(cmd_client, cmd_session, scoped):
    repo = TaskRepository(cmd_session)
    tasks = [repo.add_task(title='task',task_type='counter_research',research_case_id=c.id) for c in scoped]
    tasks.append(repo.add_task(title='null', task_type='counter_research'))
    cmd_session.commit()
    assert {r['id'] for r in cmd_client.get('/api/v1/tasks').json()['items']} == {str(t.id) for t in tasks[:2]}
    count = cmd_session.scalar(select(func.count()).select_from(TaskItem))
    for case in [scoped[2], scoped[3]]:
        assert cmd_client.post('/api/v1/tasks', json={'title':'deny','research_case_id':str(case.id)}).status_code == 404
    assert cmd_client.post('/api/v1/tasks', json={'title':'deny'}).status_code == 422
    for task in tasks[2:]:
        assert cmd_client.patch(f'/api/v1/tasks/{task.id}', json={'status':'done'}).status_code == 404
        cmd_session.refresh(task)
        assert task.status == 'open'
    assert cmd_session.scalar(select(func.count()).select_from(TaskItem)) == count
    assert cmd_client.patch(f'/api/v1/tasks/{tasks[0].id}', json={'status':'done'}).status_code == 200
    assert cmd_client.post('/api/v1/tasks', json={'title':'owner','research_case_id':str(scoped[0].id)}).status_code == 201


def test_task_reference_same_case(cmd_client, cmd_session, scoped):
    proposals = [ProposalService(cmd_session).create_proposal(kind='statement', payload={}, target_context={}, research_case_id=c.id) for c in scoped[:3]]
    cmd_session.commit()
    for i,p in enumerate(proposals):
        response = cmd_client.post('/api/v1/tasks', json={'title':'review','research_case_id':str(scoped[0].id),'ref_type':'proposal','ref_id':str(p.id)})
        assert response.status_code == (201 if i == 0 else 404), response.text
    assert cmd_client.post('/api/v1/tasks',json={'title':'bad','research_case_id':str(scoped[0].id),'ref_type':'unsupported','ref_id':str(uuid.uuid4())}).status_code == 422
    assert cmd_session.scalar(select(func.count()).select_from(TaskItem)) == 1


@pytest.mark.parametrize('feed', ['activity','evidence-changes'])
def test_activity_sql_scope_cursor_and_shared_document(cmd_client, cmd_session, scoped, feed):
    from app.models.ledger import CaseDocumentVersion
    now = datetime.now(timezone.utc)
    expected = []
    foreign = None
    for i,case in enumerate(scoped):
        p = ProposalService(cmd_session).create_proposal(kind='statement', payload={},target_context={},research_case_id=case.id)
        e = emit_event(cmd_session,type='proposal_decided',aggregate_type='proposal',aggregate_id=p.id,payload={'case_id':str(scoped[0].id)},actor='test-team')
        e.id = uuid.UUID(int=100+i)
        e.created_at = now - timedelta(minutes=i)
        if i < 2: expected.append(str(e.id))
        else: foreign = str(e.id)
    doc = cmd_session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == scoped[0].id))
    # All admission fixtures reuse the same source. Only the event's actual case is visible.
    emit_event(cmd_session,type='review_decision_recorded',aggregate_type='published_material_decision',aggregate_id=doc,ref_type='research_case',ref_id=scoped[2].id,payload={'case_id':str(scoped[0].id)})
    cmd_session.commit()
    params={'event_type':'proposal_decided'} if feed == 'activity' else {}
    response = cmd_client.get('/api/v1/'+feed,params={**params,'limit':1})
    assert response.status_code == 200
    assert response.json()['items'][0]['event_id'] == expected[0]
    response = cmd_client.get('/api/v1/'+feed,params={**params,'after':expected[0],'limit':1})
    assert [r['event_id'] for r in response.json()['items']] == expected[1:]
    assert not response.json()['has_more']
    for cursor in [foreign, str(uuid.uuid4())]:
        assert cmd_client.get('/api/v1/'+feed,params={**params,'after':cursor}).status_code == 404
    assert cmd_client.get('/api/v1/'+feed,params={'after':'invalid'}).status_code == 422
    assert cmd_client.get('/api/v1/'+feed,params={'case_id':str(scoped[2].id)}).status_code == 404


def test_task_cursor_scope(cmd_client, cmd_session, scoped):
    tasks=[TaskRepository(cmd_session).add_task(title='t',task_type='counter_research',research_case_id=c.id) for c in scoped]
    cmd_session.commit()
    for cursor in [str(tasks[2].id),str(uuid.uuid4())]:
        assert cmd_client.get('/api/v1/tasks',params={'after':cursor}).status_code == 404
    assert cmd_client.get('/api/v1/tasks',params={'after':str(tasks[1].id),'case_id':str(scoped[0].id)}).status_code == 404
    assert cmd_client.get('/api/v1/tasks',params={'after':'invalid'}).status_code == 422


@pytest.mark.parametrize('method,path,body', [('post','/tasks',{'title':'x'}),('patch','/tasks/00000000-0000-0000-0000-000000000001',{'status':'done'})])
@pytest.mark.parametrize('token,status', [('',401),('Bearer invalid',403)])
def test_task_writes_require_credentials(cmd_client, cmd_session, method, path, body, token, status):
    before = cmd_session.scalar(select(func.count()).select_from(TaskItem))
    assert getattr(cmd_client, method)('/api/v1'+path,json=body,headers={'Authorization':token}).status_code == status
    assert cmd_session.scalar(select(func.count()).select_from(TaskItem)) == before


def test_legitimate_event_families_and_filtered_cursors(cmd_client, cmd_session, scoped):
    from app.models.ledger import CaseDocumentVersion
    from app.services.jobs import JobService
    p=ProposalService(cmd_session).create_proposal(kind='statement',payload={},target_context={},research_case_id=scoped[0].id)
    JobService(cmd_session).create(kind='extract',research_case_id=scoped[0].id)
    expected={'proposal_created','job_created'}
    for event_type in ['evidence_link_proposed','evidence_link_rejected']:
        emit_event(cmd_session,type=event_type,aggregate_type='evidence_link',aggregate_id=p.id,ref_type='proposal',ref_id=p.id,payload={})
        expected.add(event_type)
    doc=cmd_session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id==scoped[0].id))
    e=emit_event(cmd_session,type='event_material_attached',aggregate_type='document_version',aggregate_id=doc,ref_type='research_case',ref_id=scoped[0].id,payload={},actor='alice')
    expected.add(e.type)
    cmd_session.commit()
    response=cmd_client.get('/api/v1/activity',params={'case_id':str(scoped[0].id)})
    assert {r['type'] for r in response.json()['items']} == expected
    for filters in [{'actor_id':'bob'}, {'event_type':'proposal_created'}, {'case_id':str(scoped[1].id)}]:
        assert cmd_client.get('/api/v1/activity',params={**filters,'after':str(e.id)}).status_code == 404
    assert cmd_client.get('/api/v1/evidence-changes',params={'after':str(e.id)}).status_code == 404


def test_seeded_task_reference_types(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment, CaseTenantAdmission, EvidenceLink, Thesis
    case_id=cmd_seeded.scalar(select(CaseTenantAdmission.research_case_id))
    refs=[('thesis',cmd_seeded.scalar(select(Thesis.id).where(Thesis.research_case_id==case_id))),('evidence_link',cmd_seeded.scalar(select(EvidenceLink.id).join(Thesis).where(Thesis.research_case_id==case_id))),('ai_assessment',cmd_seeded.scalar(select(AIAssessment.id)))]
    for kind,ref in refs:
        assert ref is not None
        response=cmd_client.post('/api/v1/tasks',json={'title':'review','research_case_id':str(case_id),'ref_type':kind,'ref_id':str(ref)})
        assert response.status_code == 201,response.text


def test_task_cursor_filters_and_tuple_order(cmd_client, cmd_session, scoped):
    now=datetime.now(timezone.utc)
    repo=TaskRepository(cmd_session)
    tasks=[]
    for i in range(3):
        t=repo.add_task(title='task',task_type='counter_research',research_case_id=scoped[0].id,assignee='alice')
        t.id=uuid.UUID(int=i+1)
        t.created_at=now-timedelta(minutes=i)
        tasks.append(t)
    cmd_session.commit()
    seen=[]
    cursor=None
    for i in range(3):
        params={'limit':1}
        if cursor: params['after']=cursor
        response=cmd_client.get('/api/v1/tasks',params=params).json()
        seen.extend(r['id'] for r in response['items'])
        cursor=response['next_cursor']
    assert seen == [str(t.id) for t in tasks]
    for filters in [{'status':'done'},{'assignee':'bob'}]:
        assert cmd_client.get('/api/v1/tasks',params={**filters,'after':str(tasks[0].id)}).status_code == 404


def test_seeded_formal_event_ownership(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment, CaseTenantAdmission, EvidenceLink, Thesis
    case_id=cmd_seeded.scalar(select(CaseTenantAdmission.research_case_id))
    for kind,model,event_type in [('ai_assessment',AIAssessment,'ai_assessment_frozen'),('evidence_link',EvidenceLink,'evidence_link_published')]:
        ref=cmd_seeded.scalar(select(model.id))
        assert ref is not None
        event=emit_event(cmd_seeded,type=event_type,aggregate_type=kind,aggregate_id=ref,payload={})
        cmd_seeded.commit()
        response=cmd_client.get('/api/v1/evidence-changes',params={'case_id':str(case_id)})
        assert str(event.id) in {r['event_id'] for r in response.json()['items']}


def test_equal_timestamp_event_cursor(cmd_client, cmd_session, scoped):
    now=datetime.now(timezone.utc)
    ids=[]
    for i in [3,1,2]:
        e=emit_event(cmd_session,type='research_case_version_appended',aggregate_type='research_case',aggregate_id=scoped[0].id,payload={})
        e.created_at=now
        e.id=uuid.UUID(int=i)
        ids.append(str(e.id))
    cmd_session.commit()
    seen=[]
    params={'limit':1}
    for _ in range(3):
        page=cmd_client.get('/api/v1/activity',params=params).json()
        seen += [r['event_id'] for r in page['items']]
        params['after']=page['next_cursor']
    assert seen == sorted(ids,reverse=True)


def test_atomic_claim_task_reference_remains_supported(cmd_client, cmd_seeded):
    from app.models.ledger import AtomicClaimCandidate, CaseDocumentVersion, CaseTenantAdmission, SourceSpan
    case_id=cmd_seeded.scalar(select(CaseTenantAdmission.research_case_id))
    span_id=cmd_seeded.scalar(select(SourceSpan.id).join(CaseDocumentVersion, CaseDocumentVersion.document_version_id==SourceSpan.document_version_id).where(CaseDocumentVersion.research_case_id==case_id))
    assert span_id is not None
    candidate=AtomicClaimCandidate(source_span_id=span_id, canonical_key=uuid.uuid4().hex,quote='claim',quote_start=0,quote_end=5,quote_sha256='a'*64,normalized_text='claim',claim_type='fact',authority_level='primary',structured_fields={},validation_result={},created_at=datetime.now(timezone.utc))
    cmd_seeded.add(candidate)
    cmd_seeded.commit()
    response=cmd_client.post('/api/v1/tasks',json={'title':'review claim','research_case_id':str(case_id),'ref_type':'atomic_claim_candidate','ref_id':str(candidate.id)})
    assert response.status_code == 201,response.text


@pytest.mark.parametrize('target', ['foreign', 'same_tenant_wrong_case', 'missing'])
def test_malformed_proposal_targets_are_hidden_and_cannot_create_tasks(cmd_client, cmd_session, scoped, target):
    from app.models.ledger import Thesis
    thesis_id=uuid.uuid4()
    if target != 'missing':
        thesis=Thesis(research_case_id=scoped[2 if target=='foreign' else 1].id, statement='wrong target',created_at=datetime.now(timezone.utc),created_by='test')
        cmd_session.add(thesis)
        cmd_session.flush()
        thesis_id=thesis.id
    p=ProposalService(cmd_session).create_proposal(kind='evidence_link',payload={},target_context={'thesis_id':str(thesis_id)},research_case_id=scoped[0].id)
    e=emit_event(cmd_session,type='evidence_link_proposed',aggregate_type='evidence_link',aggregate_id=p.id,ref_type='proposal',ref_id=p.id,payload={})
    cmd_session.commit()
    before=cmd_session.scalar(select(func.count()).select_from(TaskItem))
    assert cmd_client.get('/api/v1/activity').json()['items'] == []
    assert cmd_client.get('/api/v1/evidence-changes').json()['items'] == []
    assert cmd_client.get('/api/v1/activity',params={'after':str(e.id)}).status_code == 404
    response=cmd_client.post('/api/v1/tasks',json={'title':'deny','research_case_id':str(scoped[0].id),'ref_type':'proposal','ref_id':str(p.id)})
    assert response.status_code == 404,response.text
    assert cmd_session.scalar(select(func.count()).select_from(TaskItem)) == before
