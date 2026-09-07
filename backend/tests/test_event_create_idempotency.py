"""Creation retries must replay the same owned Case without duplicate side effects."""
import pytest
from sqlalchemy import func, select
from app.models.ledger import ResearchCase
from app.models.operational import IdempotencyKey
from app.services.research_preparation import ResearchPreparationService
from tests.test_event_research_api import _confirmed_event


def test_same_submission_replays_without_another_case(api_client, session):
    headers = {"Idempotency-Key": "creation-retry"}
    first = api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers)
    second = api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert session.scalar(select(func.count()).select_from(ResearchCase)) == 1


def test_changed_payload_conflicts(api_client, session):
    headers = {"Idempotency-Key": "creation-retry"}
    assert api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers).status_code == 201
    changed = {**_confirmed_event(), "event_title": "另一个事件"}
    assert api_client.post("/api/v1/event-research", json=changed, headers=headers).status_code == 409
    assert session.scalar(select(func.count()).select_from(ResearchCase)) == 1


def test_failed_creation_releases_key_and_rolls_back(api_client, session, monkeypatch):
    original = ResearchPreparationService.create_for_case
    def fail(*args, **kwargs):
        raise ValueError("test queue failure")
    monkeypatch.setattr(ResearchPreparationService, "create_for_case", fail)
    headers = {"Idempotency-Key": "failed-creation-retry"}
    assert api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers).status_code == 422
    assert session.scalar(select(func.count()).select_from(ResearchCase)) == 0
    assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
    monkeypatch.setattr(ResearchPreparationService, "create_for_case", original)
    assert api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers).status_code == 201


def test_key_is_scoped_to_authenticated_tenant(api_client, session, monkeypatch):
    import json
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"test-token": "test-team", "other-token": "other-team"}))
    headers = {"Idempotency-Key": "same-key", "Authorization": "Bearer test-token"}
    first = api_client.post("/api/v1/event-research", json=_confirmed_event(), headers=headers)
    second = api_client.post("/api/v1/event-research", json=_confirmed_event(), headers={**headers, "Authorization": "Bearer other-token"})
    assert first.status_code == second.status_code == 201
    assert first.json()["case_id"] != second.json()["case_id"]


@pytest.mark.parametrize("key", ["", "x" * 257])
def test_invalid_key_rejected_before_write(api_client, session, key):
    assert api_client.post("/api/v1/event-research", json=_confirmed_event(), headers={"Idempotency-Key": key}).status_code == 422
    assert session.scalar(select(func.count()).select_from(ResearchCase)) == 0
