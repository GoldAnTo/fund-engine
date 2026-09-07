"""Document commands authorize the research attachment before any side effect."""
import uuid
import pytest
from sqlalchemy import func, select
from app.models.ledger import AIRun, AtomicClaimCandidate, DocumentVersion, CaseDocumentVersion
from tests.event_case_factory import create_event_case


@pytest.mark.parametrize('operation', ['extract', 'supplements'])
@pytest.mark.parametrize('credential,status', [('',401), ('Bearer foreign',404)])
def test_denied_document_commands_do_not_invoke_model_or_write(api_client, session, monkeypatch, operation, credential, status):
    monkeypatch.setenv('RESEARCH_TENANT_TOKENS','{"test-tenant-token":"test-team","foreign":"other-team"}')
    case_id = uuid.UUID(create_event_case(api_client))
    doc_id = session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == case_id))
    def forbidden():
        pytest.fail('denied document command constructed model')
    monkeypatch.setattr('app.api.v1.commands.engine.LLMClient.from_env', forbidden)
    models = (DocumentVersion, CaseDocumentVersion, AIRun, AtomicClaimCandidate)
    before = [session.scalar(select(func.count()).select_from(m)) for m in models]
    body = {'case_id':str(case_id), 'raw_text':'recovery text', 'claimed_page_reference':'page 1','created_by':'tester'} if operation=='supplements' else None
    response = api_client.post(f'/api/v1/documents/{doc_id}/{operation}',json=body,headers={'Authorization':credential})
    assert response.status_code==status, response.text
    assert before == [session.scalar(select(func.count()).select_from(m)) for m in models]


def test_unattached_document_cannot_be_extracted(api_client, session, document, monkeypatch):
    def forbidden():
        pytest.fail('unattached document constructed model')
    monkeypatch.setattr('app.api.v1.commands.engine.LLMClient.from_env', forbidden)
    response = api_client.post(f'/api/v1/documents/{document.id}/extract')
    assert response.status_code == 404
    assert session.scalar(select(func.count()).select_from(AIRun)) == 0


@pytest.mark.parametrize('operation', ['extract', 'supplements'])
def test_owned_case_cannot_write_document_attached_only_to_another_case(api_client, session, monkeypatch, operation):
    own = uuid.UUID(create_event_case(api_client))
    other = uuid.UUID(create_event_case(api_client, title='另一个自有研究', raw_input='另一份研究原文：本季研发开支增加，研发投入的产出仍待核验。'))
    doc_id = session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == own))

    def forbidden():
        pytest.fail('unattached case document constructed model')
    monkeypatch.setattr('app.api.v1.commands.engine.LLMClient.from_env', forbidden)
    models = (DocumentVersion, CaseDocumentVersion, AIRun, AtomicClaimCandidate)
    before = [session.scalar(select(func.count()).select_from(m)) for m in models]
    kwargs = {'params': {'case_id': str(other)}} if operation == 'extract' else {
        'json': {'case_id': str(other), 'raw_text': 'recovery text',
                 'claimed_page_reference': 'page 1', 'created_by': 'tester'}}
    response = api_client.post(f'/api/v1/documents/{doc_id}/{operation}', **kwargs)
    assert response.status_code == 404, response.text
    assert before == [session.scalar(select(func.count()).select_from(m)) for m in models]


def test_shared_source_extraction_does_not_approve_or_advance_any_case(api_client, session, monkeypatch):
    from app.models.ledger import SourceStatement
    from app.models.research_preparation import ResearchPreparation
    monkeypatch.setenv('RESEARCH_TENANT_TOKENS','{"test-tenant-token":"test-team","foreign":"other-team"}')
    own = uuid.UUID(create_event_case(api_client))
    api_client.headers['Authorization'] = 'Bearer foreign'
    try:
        other = uuid.UUID(create_event_case(api_client))
    finally:
        api_client.headers['Authorization'] = 'Bearer test-tenant-token'
    docs = [session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == c)) for c in (own, other)]
    assert docs[0] == docs[1]
    def states():
        return sorted((str(p.research_case_id), p.status, p.version, p.parse_claims_state, p.claim_review_state) for p in session.scalars(select(ResearchPreparation)))
    before = states()
    response = api_client.post(f'/api/v1/documents/{docs[0]}/extract', params={'case_id':str(own)})
    assert response.status_code == 201, response.text
    assert response.json()['candidate_count'] > 0
    session.expire_all()
    assert states() == before
    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 0
    assert api_client.post(f'/api/v1/documents/{docs[0]}/extract', params={'case_id':str(other)}).status_code == 404


@pytest.mark.parametrize('failure', [ValueError, RuntimeError])
def test_document_model_configuration_failure_is_safe_and_audited(api_client, session, monkeypatch, failure):
    case_id = uuid.UUID(create_event_case(api_client))
    doc_id = session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == case_id))
    before = session.scalar(select(func.count()).select_from(AtomicClaimCandidate))
    def unavailable():
        raise failure('private endpoint and credential configuration')
    monkeypatch.setattr('app.api.v1.commands.engine.LLMClient.from_env', unavailable)
    response = api_client.post(f'/api/v1/documents/{doc_id}/extract', params={'case_id': str(case_id)})
    assert response.status_code == 503, response.text
    assert 'private endpoint' not in response.text
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == before
    audit = session.scalars(select(AIRun).where(AIRun.kind == 'extract')).all()
    assert len(audit) == 1
    assert audit[0].status == 'failed'
    assert audit[0].model_version == 'not_run'
    assert audit[0].input_ref['document_version_id'] == str(doc_id)
    assert 'private endpoint' not in audit[0].error
