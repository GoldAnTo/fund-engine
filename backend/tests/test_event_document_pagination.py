import pytest
import uuid
from datetime import datetime, timezone

from app.models.ledger import DocumentVersion, CaseDocumentVersion
from .test_case_monitor_api import _case_with_confirmed_factor


def test_event_documents_page_through_all_attached_versions(cmd_client, cmd_session):
    case, _ = _case_with_confirmed_factor(cmd_session)
    for _ in range(2):
        doc = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url='https://example.test/paging',
                              available_at=datetime.now(timezone.utc), acquired_at=datetime.now(timezone.utc), parser_version='test')
        cmd_session.add(doc)
        cmd_session.flush()
        cmd_session.add(CaseDocumentVersion(research_case_id=case.id, document_version_id=doc.id, linked_at=datetime.now(timezone.utc)))
    cmd_session.commit()
    path = f'/api/v1/event-research/{case.id}/documents'
    ids = []
    cursor = None
    for index in range(3):
        page = cmd_client.get(path, params={'limit': 1, **({'cursor': cursor} if cursor else {})})
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body['items']) == 1
        ids.append(body['items'][0]['id'])
        assert body['page']['has_more'] == (index < 2)
        cursor = body['page']['next_cursor']
    assert len(set(ids)) == 3
    assert cursor is None
    assert cmd_client.get(path, params={'limit': 0}).status_code == 422
    assert cmd_client.get(path, params={'limit': 101}).status_code == 422
    assert cmd_client.get(path, params={'cursor': 'invalid'}).status_code == 422


def test_event_document_cursor_must_belong_to_current_case(cmd_client, cmd_session):
    from sqlalchemy import select
    from app.queries.documents import _encode_cursor
    case, _ = _case_with_confirmed_factor(cmd_session)
    other, _ = _case_with_confirmed_factor(cmd_session)
    document = cmd_session.scalar(select(DocumentVersion).join(CaseDocumentVersion,
        CaseDocumentVersion.document_version_id == DocumentVersion.id).where(CaseDocumentVersion.research_case_id == other.id))
    cursor = _encode_cursor(document.available_at, document.id)
    response = cmd_client.get(f'/api/v1/event-research/{case.id}/documents', params={'cursor': cursor})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("entity_metadata", [False, True])
def test_document_page_batches_span_reads_and_statement_counts(cmd_client, cmd_session, record_testsuite_property, entity_metadata):
    from sqlalchemy import event
    from app.models.ledger import SourceSpan, SourceStatement
    case, _ = _case_with_confirmed_factor(cmd_session)
    for index in range(10):
        now = datetime.now(timezone.utc)
        document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url='https://example.test/batch',
            available_at=now, acquired_at=now, parser_version='test')
        cmd_session.add(document)
        cmd_session.flush()
        span = SourceSpan(document_version_id=document.id, locator={'paragraph': 1, **({'title': f'重复股票资料{index}', 'sec_code': 'UNKNOWN.SH'} if entity_metadata else {})}, verbatim_text=f'第{index}份独立资料的完整原文内容。')
        cmd_session.add(span)
        cmd_session.flush()
        cmd_session.add_all([CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now),
            SourceStatement(source_span_id=span.id, kind='fact', normalized_text='资料陈述', created_at=now)])
    cmd_session.commit()
    queries = []
    def count_query(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    bind = cmd_session.get_bind()
    event.listen(bind, 'before_cursor_execute', count_query)
    try:
        path = f'/api/v1/event-research/{case.id}/documents'
        small = cmd_client.get(path, params={'limit': 1})
        assert small.status_code == 200
        small_count = len(queries)
        queries.clear()
        large = cmd_client.get(path, params={'limit': 10})
        assert large.status_code == 200
        large_count = len(queries)
    finally:
        event.remove(bind, 'before_cursor_execute', count_query)
    assert len(large.json()['items']) == 10
    assert all(item['span_count'] == item['statement_count'] == 1 for item in large.json()['items'])
    if entity_metadata:
        assert all(item["entity"] == "UNKNOWN.SH" for item in large.json()["items"])
    record_testsuite_property(f"entity_{entity_metadata}_one_document_selects", small_count)
    record_testsuite_property(f"entity_{entity_metadata}_ten_document_selects", large_count)
    assert large_count <= small_count + 2, (small_count, large_count)


def test_entity_lookup_preserves_fallbacks_without_caching_across_requests(cmd_session):
    from app.models.ledger import Company, Stock
    from app.queries.documents import DocumentReadQueries
    query = DocumentReadQueries(cmd_session)
    cache = {}
    assert query._resolve_entity('AUDIT.SH', stock_cache=cache) == 'AUDIT.SH'
    now = datetime.now(timezone.utc)
    company = Company(code='AUDIT-C', name='验收公司', type='company', created_at=now)
    cmd_session.add(company)
    cmd_session.flush()
    cmd_session.add(Stock(company_id=company.id, code='AUDIT.SH', name='验收股票', market='SH', created_at=now))
    cmd_session.flush()
    fresh_cache = {}
    assert query._resolve_entity('AUDIT', stock_cache=fresh_cache) == '验收股票 (AUDIT.SH)'
    assert query._resolve_entity(None, sec_name='验收股票', stock_cache=fresh_cache) == '验收股票 (AUDIT.SH)'
    assert query._resolve_entity(None, title='验收股票：季度公告', stock_cache=fresh_cache) == '验收股票 (AUDIT.SH)'
    assert query._resolve_entity('AUDIT.SH', stock_cache={}) == '验收股票 (AUDIT.SH)'
