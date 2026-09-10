"""Exercise the actual private team endpoints and explicit command receipts."""
from tests import test_research_gateway_api as gateway_api
from tests.test_research_team_read import complete_team

gateway_client = gateway_api.gateway_client


def start(client):
    receipt = gateway_api._start(client).json()
    return f"{gateway_api.BASE}/{receipt['conversation_id']}/runs/{receipt['run_spec_id']}/team"


def test_team_endpoints_expose_real_tasks_and_accept_directed_work(gateway_client):
    client, _ = gateway_client
    path = start(client)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers['Cache-Control'] == 'no-store'
    body = response.json()
    assert len(body['tasks']) == 4
    assert all(task['output'] is None for task in body['tasks'])
    changed = client.post(path + '/messages', headers={'Idempotency-Key': 'role-message'}, json={'text': '核查财务期间', 'recipient': 'finance', 'expected_revision': 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()['revision'] == 2
    assert len(changed.json()['task_ids']) == 3


def test_team_commands_validate_revision_identity_and_request_shape(gateway_client):
    client, _ = gateway_client
    path = start(client)
    paused = client.post(path + '/commands', headers={'Idempotency-Key': 'pause'}, json={'kind': 'pause', 'expected_revision': 1})
    assert paused.status_code == 200
    assert client.get(path).json()['status'] == 'paused'
    invalid = client.post(path + '/messages', headers={'Idempotency-Key': 'invalid'}, json={'text': 'x', 'recipient': 'system', 'expected_revision': 1})
    assert invalid.status_code == 422
    client.headers['Authorization'] = 'Bearer bob-token'
    assert client.get(path).status_code == 404
    client.headers.pop('Authorization')
    assert client.get(path).status_code == 401


def test_review_records_real_principal_and_rejects_client_identity(gateway_client, cmd_session):
    client, _ = gateway_client
    spec, _, outputs = complete_team(cmd_session)
    path = f'{gateway_api.BASE}/{spec.conversation_id}/runs/{spec.id}/team'
    body = {'expected_revision': 1, 'output_ids': [str(output.id) for output in outputs.values()], 'decision': 'approved', 'comment': '人工逐项核验'}
    assert client.post(path + '/reviews', headers={'Idempotency-Key': 'fake'}, json={**body, 'reviewed_by': 'ai'}).status_code == 422
    response = client.post(path + '/reviews', headers={'Idempotency-Key': 'human'}, json=body)
    assert response.status_code == 200, response.text
    assert client.get(path).json()['reviews'][0]['reviewed_by'] == 'alice'
