import uuid

from app.repositories.auto_research import AutoResearchRepository
from app.services.case_monitor import ResearchRunEventRepository
from tests.event_case_factory import create_event_case


def test_run_event_pages_cover_history_without_duplicates(api_client, session, monkeypatch):
    monkeypatch.setenv('RESEARCH_TENANT_TOKENS', '{"test-tenant-token":"test-team","foreign":"other-team"}')
    case_id = uuid.UUID(create_event_case(api_client))
    run = AutoResearchRepository(session).create_run(research_case_id=case_id)
    events = ResearchRunEventRepository(session)
    for index in range(7):
        events.append(run.id, stage='planning', status='queued', message=f'step {index}', payload_json={})
    session.commit()
    def full_detail_forbidden(*args, **kwargs):
        raise AssertionError('event pages must not hydrate full task/evidence detail')
    monkeypatch.setattr('app.services.auto_research.AutoResearchService.detail', full_detail_forbidden)
    endpoint = f'/api/v1/research-runs/{run.id}/events'
    collected = []
    after = 0
    for expected_more in (True, True, False):
        response = api_client.get(endpoint, params={'limit':3, 'after_seq':after})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['has_more'] == expected_more
        assert page['next_cursor'] == (str(page['items'][-1]['seq']) if expected_more else None)
        collected.extend(item['message'] for item in page['items'])
        after = page['items'][-1]['seq']
    assert collected == [f'step {index}' for index in range(7)]
    assert api_client.get(endpoint, params={'after_seq':after}).json()['items'] == []
    assert api_client.get(endpoint, params={'after_seq':-1}).status_code == 422
    assert api_client.get(endpoint, headers={'Authorization':''}).status_code == 401
    assert api_client.get(endpoint, params={'after_seq':3}, headers={'Authorization':'Bearer foreign'}).status_code == 404
