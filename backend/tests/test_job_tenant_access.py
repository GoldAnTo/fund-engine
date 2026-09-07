"""Public job controls must not bypass Case ownership."""
import uuid

import pytest

from app.models.operational import Job
from app.services.jobs import JobService
from tests.event_case_factory import create_event_case


@pytest.mark.parametrize("operation", ["read", "events", "cancel", "retry"])
@pytest.mark.parametrize("authorization, expected", [("", 401), ("Bearer foreign", 404), ("Bearer test-tenant-token", 200)])
def test_job_operations_require_case_owner(cmd_client, cmd_session, monkeypatch, operation, authorization, expected):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign":"other-team"}')
    case_id = uuid.UUID(create_event_case(cmd_client))
    job = JobService(cmd_session).create(kind="propose", research_case_id=case_id)
    if operation == "retry":
        JobService(cmd_session).finish(job, status="failed", error="test failure")
    cmd_session.commit()
    original = (job.status, job.attempt, job.cancel_requested)
    suffix = {"read":"", "events":"/events", "cancel":"/cancel", "retry":"/retries"}[operation]
    method = cmd_client.get if operation in {"read", "events"} else cmd_client.post
    response = method(f"/api/v1/jobs/{job.id}{suffix}", headers={"Authorization":authorization})
    assert response.status_code == expected, response.text
    if expected != 200:
        cmd_session.expire_all()
        current = cmd_session.get(Job, job.id)
        assert (current.status, current.attempt, current.cancel_requested) == original


def test_unscoped_job_is_not_exposed_by_legacy_job_api(cmd_client, cmd_session):
    job = JobService(cmd_session).create(kind="extract")
    cmd_session.commit()
    assert cmd_client.get(f"/api/v1/jobs/{job.id}").status_code == 404
