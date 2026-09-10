from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.models.event_research import EventResearchScopeVersion
from app.models.identity import ResearchUser
from app.security.principal import ResearchPrincipal
from app.services.research_worker_heartbeat import WorkerHeartbeatService


def _create_confirmed_case(cmd_client, cmd_session) -> uuid.UUID:
    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "公司上调资本开支指引，市场重新评估需求。",
            "event_title": "资本开支指引更新",
            "research_question": "资本开支上调能否被后续经营数据验证？",
            "candidate_factors": ["订单增长", "收入兑现", "替代解释"],
        },
    )
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    confirmed = cmd_client.post(
        f"/api/v1/event-research/{case_id}/workflow/confirm",
        json={
            "scope_version_id": str(scope.id),
            "scope_version": scope.version,
            "idempotency_key": f"runtime-status:{scope.id}",
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    return case_id


def _principal(cmd_session, *, roles: frozenset[str]) -> ResearchPrincipal:
    now = datetime.now(UTC)
    user = ResearchUser(
        issuer="https://test-identity.invalid/realms/research",
        subject=f"runtime-status:{uuid.uuid4()}",
        tenant_id="test-team",
        display_name="Runtime status reader",
        normalized_email=None,
        active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(user)
    cmd_session.commit()
    return ResearchPrincipal(
        user_id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        roles=roles,
        expires_at=now + timedelta(hours=1),
    )


def test_case_runtime_status_combines_service_health_with_durable_checkpoint(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    seen_at = datetime.now(UTC)
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="acquisition-worker:host-name-must-stay-private",
        mode="loop",
        state="polling",
        configuration_status="configured",
        seen_at=seen_at,
    )
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="scheduler:private-scheduler",
        mode="loop",
        state="polling",
        configuration_status="configured",
        seen_at=seen_at,
    )
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_status"] == "healthy"
    assert body["required_services"] == [
        {
            "name": "scheduler",
            "status": "healthy",
            "state": "polling",
            "last_seen_at": seen_at.isoformat().replace("+00:00", "Z"),
        },
        {
            "name": "acquisition-worker",
            "status": "healthy",
            "state": "polling",
            "last_seen_at": seen_at.isoformat().replace("+00:00", "Z"),
        },
    ]
    assert body["durable_checkpoint"]["workflow_state"] == "planning_acquisition"
    assert body["durable_checkpoint"]["user_stage"] == "acquisition"
    assert body["durable_checkpoint"]["version"] >= 1
    assert body["durable_checkpoint"]["last_transition"] == "scope_confirmed"
    assert body["recovery"]["automatic"] is True
    assert "host-name-must-stay-private" not in response.text


def test_missing_runtime_heartbeat_does_not_replace_the_recorded_case_state(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)

    response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_status"] == "unavailable"
    assert all(
        item["status"] == "unavailable" for item in body["required_services"]
    )
    assert body["durable_checkpoint"]["workflow_state"] == "planning_acquisition"
    assert body["recovery"]["automatic"] is True
    assert "不会用运行健康推断 Case 阶段" in body["message"]


def test_case_runtime_status_redacts_unrecognized_worker_state(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    private_state = "failed at private-worker.internal"
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="acquisition-worker:private-instance",
        mode="loop",
        state=private_state,
        configuration_status="configured",
    )
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="scheduler:private-instance",
        mode="loop",
        state="polling",
        configuration_status="configured",
    )
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )

    assert response.status_code == 200, response.text
    services = {item["name"]: item for item in response.json()["required_services"]}
    assert services["acquisition-worker"]["state"] == "unknown"
    assert private_state not in response.text


def test_scheduler_outage_makes_an_acquisition_stage_unavailable(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="acquisition-worker:worker-1",
        mode="loop",
        state="polling",
        configuration_status="configured",
    )
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_status"] == "unavailable"
    assert {item["name"]: item["status"] for item in body["required_services"]} == {
        "scheduler": "unavailable",
        "acquisition-worker": "healthy",
    }


def test_service_owned_misconfiguration_makes_the_case_runtime_unavailable(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="scheduler:scheduler-1",
        mode="loop",
        state="polling",
        configuration_status="configured",
    )
    WorkerHeartbeatService(cmd_session).touch(
        worker_id="acquisition-worker:worker-1",
        mode="loop",
        state="polling",
        configuration_status="misconfigured",
        configuration_issues={
            "missing": ["LLM_API_KEY"],
            "invalid": [],
        },
    )
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_status"] == "unavailable"
    assert {item["name"]: item["status"] for item in body["required_services"]}[
        "acquisition-worker"
    ] == "unavailable"


def test_mixed_worker_fleet_degrades_case_and_unions_redacted_admin_issues(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    heartbeats = WorkerHeartbeatService(cmd_session)
    heartbeats.touch(
        worker_id="scheduler:scheduler-1",
        mode="loop",
        state="polling",
        configuration_status="configured",
    )
    heartbeats.touch(
        worker_id="acquisition-worker:configured",
        mode="loop",
        state="polling",
        configuration_status="configured",
    )
    heartbeats.touch(
        worker_id="acquisition-worker:misconfigured",
        mode="loop",
        state="polling",
        configuration_status="misconfigured",
        configuration_issues={
            "missing": ["GILDATA_TOKEN", "private-secret-name"],
            "invalid": ["LLM_CONFIGURATION"],
        },
    )
    cmd_session.commit()

    case_response = cmd_client.get(
        f"/api/v1/event-research/{case_id}/runtime-status"
    )
    admin_response = cmd_client.get("/api/v1/runtime-status")

    assert case_response.status_code == 200, case_response.text
    case_body = case_response.json()
    assert case_body["runtime_status"] == "degraded"
    assert {item["name"]: item["status"] for item in case_body["required_services"]}[
        "acquisition-worker"
    ] == "degraded"
    assert admin_response.status_code == 200, admin_response.text
    provider = next(
        item
        for item in admin_response.json()["providers"]
        if item["service"] == "acquisition-worker"
    )
    assert provider["status"] == "unconfirmed"
    assert provider["missing"] == ["GILDATA_TOKEN"]
    assert provider["invalid"] == ["LLM_CONFIGURATION"]
    assert "private-secret-name" not in admin_response.text


def test_case_runtime_status_requires_case_view_permission(
    cmd_client,
    cmd_session,
) -> None:
    case_id = _create_confirmed_case(cmd_client, cmd_session)
    outsider = _principal(cmd_session, roles=frozenset())
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: outsider
    try:
        response = cmd_client.get(
            f"/api/v1/event-research/{case_id}/runtime-status"
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous

    # Case authorization deliberately hides inaccessible Case existence.
    assert response.status_code == 404, response.text


def test_admin_runtime_status_is_tenant_admin_only_and_redacts_configuration(
    cmd_client,
    cmd_session,
    monkeypatch,
) -> None:
    secret = "provider-secret-must-never-be-returned"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("GILDATA_TOKEN", secret)
    monkeypatch.setenv("ACQUISITION_ENABLED_ADAPTERS", "sse,szse,gildata")
    for service in ("research-worker", "acquisition-worker", "scheduler"):
        WorkerHeartbeatService(cmd_session).touch(
            worker_id=f"{service}:private-instance",
            mode="loop",
            state="polling",
            configuration_status="configured",
        )
    cmd_session.commit()

    response = cmd_client.get("/api/v1/runtime-status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["database"]["status"] == "healthy"
    assert body["migration"]["status"] in {"healthy", "unavailable"}
    assert [item["name"] for item in body["services"]] == [
        "research-worker",
        "acquisition-worker",
        "scheduler",
    ]
    assert all(item["status"] == "healthy" for item in body["services"])
    assert {item["service"] for item in body["providers"]} == {
        "api",
        "research-worker",
        "acquisition-worker",
        "scheduler",
    }
    assert {item["service"]: item["basis"] for item in body["providers"]} == {
        "api": "local_process",
        "research-worker": "service_heartbeat",
        "acquisition-worker": "service_heartbeat",
        "scheduler": "service_heartbeat",
    }
    assert all(item["status"] == "configured" for item in body["providers"][1:])
    assert secret not in response.text
    assert "private-instance" not in response.text
    assert "DATABASE_URL" not in response.text

    ordinary = _principal(cmd_session, roles=frozenset())
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: ordinary
    try:
        denied = cmd_client.get("/api/v1/runtime-status")
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous
    assert denied.status_code == 403, denied.text
