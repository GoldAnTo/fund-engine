from __future__ import annotations

import pytest
from app.models.operational import Job
from app.repositories.operational import JobRepository
from sqlalchemy import select

from tests.test_operational_access_api import operations as _operations
from tests.test_research_gateway_service import _gateway, _send

operations = _operations


@pytest.mark.parametrize(
    "path", ["/api/v1/activity", "/api/v1/tasks", "/api/v1/evidence-changes"]
)
def test_operational_routes_require_authentication(operations, path):
    client, own, _ = operations
    client.headers.pop("Authorization", None)
    assert client.get(path, params={"case_id": str(own.id)}).status_code == 401


def test_jobs_and_legacy_workbench_require_authentication(operations, cmd_session):
    client, own, _ = operations
    job = JobRepository(cmd_session).add_job(kind="propose", research_case_id=own.id)
    cmd_session.commit()
    client.headers.pop("Authorization", None)
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 401
    assert client.post(f"/api/v1/jobs/{job.id}/cancel").status_code == 401
    assert client.get(f"/api/research-cases/{own.id}/workbench").status_code == 401


@pytest.mark.parametrize("token", ["bob", "foreign"])
def test_gateway_private_case_cannot_be_read_or_controlled_through_operational_backdoors(
    operations, cmd_session, token
):
    client, _, _ = operations
    receipt = _send(_gateway(cmd_session))
    job = cmd_session.scalar(
        select(Job).where(Job.research_case_id == receipt.native_case_id)
    )
    client.headers["Authorization"] = f"Bearer {token}"
    for suffix in ["", "/events"]:
        assert client.get(f"/api/v1/jobs/{job.id}{suffix}").status_code == 404
    assert client.post(f"/api/v1/jobs/{job.id}/cancel").status_code == 404
    for path in ["/activity", "/evidence-changes", "/tasks"]:
        assert (
            client.get(
                "/api/v1" + path, params={"case_id": str(receipt.native_case_id)}
            ).status_code
            == 404
        )
    assert (
        client.get(
            f"/api/research-cases/{receipt.native_case_id}/workbench"
        ).status_code
        == 404
    )


def test_even_gateway_owner_cannot_use_raw_operational_feeds_or_native_job_controls(
    operations, cmd_session
):
    client, _, _ = operations
    receipt = _send(_gateway(cmd_session))
    job = cmd_session.scalar(
        select(Job).where(Job.research_case_id == receipt.native_case_id)
    )
    job.error = "secret provider token and raw source body"
    job.step = "secret provider prompt"
    cmd_session.commit()
    summary = client.get(f"/api/v1/jobs/{job.id}")
    assert summary.status_code == 200
    assert "secret provider" not in summary.text
    for suffix in ["/events", "/cancel", "/retries"]:
        request = client.get if suffix == "/events" else client.post
        assert request(f"/api/v1/jobs/{job.id}{suffix}").status_code == 404
    for path in ["/activity", "/evidence-changes", "/tasks"]:
        assert (
            client.get(
                "/api/v1" + path, params={"case_id": str(receipt.native_case_id)}
            ).status_code
            == 404
        )
    assert (
        client.get(
            f"/api/research-cases/{receipt.native_case_id}/workbench"
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/tasks",
            json={"title": "bypass", "research_case_id": str(receipt.native_case_id)},
        ).status_code
        == 404
    )
    assert cmd_session.get(Job, job.id).cancel_requested is False
