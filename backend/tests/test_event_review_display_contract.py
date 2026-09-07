from datetime import datetime, timezone, timedelta
import pytest

from app.models.source_governance import SourceContract
from app.models.ledger import EvidenceLink
from app.queries.review_queue import proposal_evidence_context
from app.services.event_review_queue import EventReviewQueueService
from tests.test_event_review_queue import _case, _seed_evidence_proposal


@pytest.mark.parametrize('restriction', ['display_denied', 'expired', 'not_effective'])
def test_review_queue_withholds_contract_restricted_original_and_ai_derivatives(session, api_client, restriction):
    now = datetime.now(timezone.utc)
    case = _case(session)
    proposal = _seed_evidence_proposal(session, case=case, proposed_at=now, source_url='https://example.test/restricted')
    context = proposal_evidence_context(session, proposal)
    document = context.document
    link = EvidenceLink(thesis_id=context.thesis.id, source_statement_id=context.statement.id,
                            role='supports', reason='restricted legacy rationale', scope={},
                            available_at=now, created_at=now)
    session.add(link)
    session.add(SourceContract(
        document_version_id=document.id, source_type='licensed_provider', research_source_type='licensed_provider',
        provider_or_tenant='test', allow_ai_processing=True, allow_display=restriction != 'display_denied', allow_export=False,
        allow_api=False, region='test', retention_policy='case_retained', deletion_policy='test',
        downstream_restrictions=[], intake_metadata={}, declared_by='test', created_at=now,
        effective_until=now-timedelta(days=1) if restriction == 'expired' else None,
        effective_from=now+timedelta(days=1) if restriction == 'not_effective' else None,
    ))
    session.commit()
    item = EventReviewQueueService(session).review_queue(case.id).items[0]
    assert item.can_accept is False
    assert item.verbatim_text is None
    assert item.statement_text is None
    assert item.document_source_url is None
    assert item.ai_reason == ''
    assert item.ai_scope == {}
    assert item.display_withheld is True
    response = api_client.get(f'/api/v1/event-research/{case.id}/review-queue')
    assert response.status_code == 200, response.text
    assert 'Evidence excerpt' not in response.text
    assert 'Evidence statement' not in response.text
    assert 'supports the factor' not in response.text
    legacy = api_client.get('/api/v1/review-queue', params={'case_id':str(case.id)})
    assert legacy.status_code == 200
    assert legacy.json()['items'] == []
    raw = api_client.get('/api/v1/review-proposals', params={'case_id':str(case.id)})
    assert raw.status_code == 200
    assert raw.json()['items'][0]['payload'] == {}
    assert raw.json()['items'][0]['display_withheld'] is True
    body = {'outcome':'confirmed', 'relation':'supports', 'factor_role':'test',
            'scope_boundary':'test scope', 'reason':'test review', 'reviewer':'tester'}
    denied = api_client.post(f'/api/v1/evidence-links/{link.id}/reviews', json=body)
    assert denied.status_code == 422, denied.text
    from app.models.ledger import EvidenceReview
    from sqlalchemy import select, func
    assert session.scalar(select(func.count()).select_from(EvidenceReview)) == 0
    from app.services.review import ReviewService
    from app.repositories.research import ResearchRepository
    from app.models.ledger import ValidationError
    with pytest.raises(ValidationError, match='contract'):
        ReviewService(ResearchRepository(session)).review_link(link.id, **body)
    rejected = api_client.post(f'/api/v1/evidence-links/{link.id}/reviews', json={**body, 'outcome':'rejected'})
    assert rejected.status_code == 201, rejected.text


def test_legacy_queue_requires_source_attachment_to_target_case(session, api_client):
    from app.repositories.research import ResearchRepository
    from app.services.research import ResearchService
    from tests.tenant_admission import admit_case
    from app.models.ledger import Thesis, CaseDocumentVersion, CaseTenantAdmission
    from sqlalchemy import select
    now = datetime.now(timezone.utc)
    source_case = _case(session)
    proposal = _seed_evidence_proposal(session, case=source_case, proposed_at=now,
                                     source_url='https://investor.tsmc.com/english/restricted-case-source')
    context = proposal_evidence_context(session, proposal)
    other = ResearchService(ResearchRepository(session)).add_case(title='other owned case', industry_topic='test', created_by='test')
    initial_id = session.scalar(select(CaseTenantAdmission.initial_document_version_id).where(CaseTenantAdmission.research_case_id == source_case.id))
    assert initial_id != context.document.id
    admit_case(session, other.id, document_version_id=initial_id)
    thesis = Thesis(research_case_id=other.id, statement='other target', created_by='test', created_at=now)
    session.add(thesis)
    session.flush()
    link = EvidenceLink(thesis_id=thesis.id, source_statement_id=context.statement.id,
                            role='supports', reason='wrong case source', scope={}, available_at=now, created_at=now)
    session.add(link)
    session.commit()
    response = api_client.get('/api/v1/review-queue', params={'case_id':str(other.id)})
    assert response.status_code == 200
    assert response.json()['items'] == []
    body = {'outcome':'confirmed', 'relation':'supports', 'factor_role':'test',
            'scope_boundary':'test scope', 'reason':'test review', 'reviewer':'tester'}
    assert api_client.post(f'/api/v1/evidence-links/{link.id}/reviews', json=body).status_code == 422
    session.add(CaseDocumentVersion(research_case_id=other.id,
                document_version_id=context.document.id, linked_at=now))
    session.commit()
    visible = api_client.get('/api/v1/review-queue', params={'case_id':str(other.id), 'limit':1})
    assert visible.status_code == 200
    assert len(visible.json()['items']) == 1
    assert visible.json()['items'][0]['verbatim_text'] == 'Evidence excerpt'
    assert api_client.post(f'/api/v1/evidence-links/{link.id}/reviews', json=body).status_code == 201
