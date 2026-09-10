"""Case authorization proofs for run_id and job_id operational routes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import uuid

import pytest

from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.models.operational import ResearchRun
from app.repositories.operational import TaskRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.security.principal import ResearchPrincipal
from app.services.case_authorization import CaseAuthorizationService
from app.services.event_research import EventResearchService
from app.services.jobs import JobService
from tests.research_identity import persist_research_principal


def _create_case(session, principal: ResearchPrincipal) -> uuid.UUID:
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Frozen source for indirect authorization.",
            event_title="Operational authorization proof",
            research_question="Can this principal use the operational resource?",
            candidate_factors=["Demand", "Capacity", "Margin"],
        ),
        principal=principal,
    )
    return uuid.UUID(created.case_id)


def _request_as(cmd_client, principal: ResearchPrincipal, method: str, path: str, json=None):
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: principal
    try:
        return cmd_client.request(method, path, json=json)
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous


@pytest.fixture
def operational_authorization_scope(cmd_session):
    owner = persist_research_principal(cmd_session, tenant_id="team-a", label="owner")
    viewer = persist_research_principal(cmd_session, tenant_id="team-a", label="viewer")
    editor = persist_research_principal(cmd_session, tenant_id="team-a", label="editor")
    ungranted = persist_research_principal(
        cmd_session, tenant_id="team-a", label="ungranted"
    )
    service = replace(
        persist_research_principal(cmd_session, tenant_id="team-a", label="service"),
        roles=frozenset({"service_principal"}),
    )
    tenant_admin = replace(
        persist_research_principal(cmd_session, tenant_id="team-a", label="admin"),
        roles=frozenset({"tenant_administrator"}),
    )
    foreign_admin = replace(
        persist_research_principal(cmd_session, tenant_id="team-b", label="foreign"),
        roles=frozenset({"tenant_administrator"}),
    )
    case_id = _create_case(cmd_session, owner)
    authorization = CaseAuthorizationService(cmd_session)
    authorization.grant(case_id, owner, viewer.user_id, "viewer")
    authorization.grant(case_id, owner, editor.user_id, "editor")

    now = datetime.now(UTC)
    run = ResearchRun(
        research_case_id=case_id,
        status="succeeded",
        stage="completed",
        round=0,
        max_rounds=1,
        budget=1,
        budget_used=0,
        scope_thesis_ids=[],
        monitor_version_id=None,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(run)
    job = JobService(cmd_session).create(
        kind="research_run",
        target_type="research_run",
        target_id=run.id,
        research_case_id=case_id,
    )
    cmd_session.commit()
    return {
        "run_id": run.id,
        "job_id": job.id,
        "case_id": case_id,
        "viewer": viewer,
        "editor": editor,
        "ungranted": ungranted,
        "service": service,
        "tenant_admin": tenant_admin,
        "foreign_admin": foreign_admin,
    }


@pytest.mark.parametrize(
    ("path_template", "success_status"),
    [
        ("/api/v1/research-runs/{run_id}", 200),
        ("/api/v1/research-runs/{run_id}/events", 200),
        ("/api/v1/jobs/{job_id}", 200),
        ("/api/v1/jobs/{job_id}/events", 200),
    ],
)
def test_run_and_job_reads_require_case_view_permission(
    cmd_client,
    operational_authorization_scope,
    path_template,
    success_status,
) -> None:
    scope = operational_authorization_scope
    path = path_template.format(**scope)

    assert _request_as(cmd_client, scope["viewer"], "GET", path).status_code == success_status
    assert _request_as(cmd_client, scope["tenant_admin"], "GET", path).status_code == 404
    assert _request_as(cmd_client, scope["ungranted"], "GET", path).status_code == 404
    assert _request_as(cmd_client, scope["service"], "GET", path).status_code == 404
    assert _request_as(cmd_client, scope["foreign_admin"], "GET", path).status_code == 404


@pytest.mark.parametrize(
    ("path_template", "json"),
    [
        (
            "/api/v1/research-runs/{run_id}/cancel",
            {"change_reason": "authorization proof"},
        ),
        ("/api/v1/jobs/{job_id}/cancel", None),
        ("/api/v1/jobs/{job_id}/retries", None),
    ],
)
def test_run_and_job_mutations_require_case_edit_permission(
    cmd_client,
    operational_authorization_scope,
    path_template,
    json,
) -> None:
    scope = operational_authorization_scope
    path = path_template.format(**scope)

    assert _request_as(cmd_client, scope["viewer"], "POST", path, json).status_code == 403
    assert _request_as(cmd_client, scope["ungranted"], "POST", path, json).status_code == 404
    assert _request_as(cmd_client, scope["service"], "POST", path, json).status_code == 404
    assert _request_as(cmd_client, scope["foreign_admin"], "POST", path, json).status_code == 404
    # Only the explicitly granted editor reaches the existing terminal/run
    # conflict validation. Tenant administration is not a private-Case grant.
    assert _request_as(cmd_client, scope["editor"], "POST", path, json).status_code == 409
    assert _request_as(cmd_client, scope["tenant_admin"], "POST", path, json).status_code == 404


def test_unscoped_tasks_cannot_be_created_read_or_mutated(
    cmd_client,
    cmd_session,
    operational_authorization_scope,
) -> None:
    scope = operational_authorization_scope
    legacy = TaskRepository(cmd_session).add_task(
        title="legacy unscoped task",
        task_type="legacy",
    )
    cmd_session.commit()

    listed = _request_as(cmd_client, scope["viewer"], "GET", "/api/v1/tasks")
    assert listed.status_code == 200
    assert all(item["id"] != str(legacy.id) for item in listed.json()["items"])
    assert _request_as(
        cmd_client,
        scope["viewer"],
        "PATCH",
        f"/api/v1/tasks/{legacy.id}",
        {"status": "done"},
    ).status_code == 404
    assert _request_as(
        cmd_client,
        scope["editor"],
        "POST",
        "/api/v1/tasks",
        {"title": "missing Case", "task_type": "manual"},
    ).status_code == 422


def test_task_assignment_requires_a_granted_user_id(
    cmd_client,
    operational_authorization_scope,
) -> None:
    scope = operational_authorization_scope
    path = "/api/v1/tasks"
    base = {
        "title": "复核补证结果",
        "task_type": "manual",
        "research_case_id": str(scope["case_id"]),
    }

    display_name = _request_as(
        cmd_client,
        scope["editor"],
        "POST",
        path,
        {**base, "assignee": "陈子仪"},
    )
    assert display_name.status_code == 422

    ungranted = _request_as(
        cmd_client,
        scope["editor"],
        "POST",
        path,
        {**base, "assignee": str(scope["ungranted"].user_id)},
    )
    assert ungranted.status_code == 404

    foreign = _request_as(
        cmd_client,
        scope["editor"],
        "POST",
        path,
        {**base, "assignee": str(scope["foreign_admin"].user_id)},
    )
    assert foreign.status_code == 404

    created = _request_as(
        cmd_client,
        scope["editor"],
        "POST",
        path,
        {**base, "assignee": str(scope["viewer"].user_id)},
    )
    assert created.status_code == 201, created.text
    assert created.json()["assignee"] == str(scope["viewer"].user_id)

    reassigned = _request_as(
        cmd_client,
        scope["editor"],
        "PATCH",
        f"/api/v1/tasks/{created.json()['id']}",
        {"status": "in_progress", "assignee": str(scope["editor"].user_id)},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["assignee"] == str(scope["editor"].user_id)


def test_hidden_task_cannot_be_used_as_a_collection_cursor(
    cmd_client,
    cmd_session,
    operational_authorization_scope,
) -> None:
    scope = operational_authorization_scope
    hidden_owner = persist_research_principal(
        cmd_session,
        tenant_id="team-a",
        label="hidden-task-owner",
    )
    hidden_case_id = _create_case(cmd_session, hidden_owner)
    visible = TaskRepository(cmd_session).add_task(
        title="visible task",
        task_type="manual",
        research_case_id=scope["case_id"],
    )
    hidden = TaskRepository(cmd_session).add_task(
        title="hidden cursor task",
        task_type="manual",
        research_case_id=hidden_case_id,
    )
    cmd_session.commit()

    visible_response = _request_as(
        cmd_client,
        scope["viewer"],
        "GET",
        "/api/v1/tasks",
    )
    assert visible_response.status_code == 200
    assert str(visible.id) in {
        item["id"] for item in visible_response.json()["items"]
    }
    hidden_cursor_response = _request_as(
        cmd_client,
        scope["viewer"],
        "GET",
        f"/api/v1/tasks?after={hidden.id}",
    )
    assert hidden_cursor_response.status_code == 404


def test_unscoped_proposals_cannot_be_read_claimed_or_decided(
    cmd_client,
    cmd_session,
    operational_authorization_scope,
) -> None:
    scope = operational_authorization_scope
    proposal = ProposalRepository(cmd_session).add_proposal(
        kind="evidence_link",
        payload={"reason": "legacy unscoped payload"},
        target_context={"entity_type": "evidence_link"},
        proposed_by_type="legacy",
        proposed_by_ref="migration",
        research_case_id=None,
    )
    cmd_session.commit()

    listed = _request_as(
        cmd_client,
        scope["viewer"],
        "GET",
        "/api/v1/review-proposals",
    )
    assert listed.status_code == 200
    assert all(item["id"] != str(proposal.id) for item in listed.json()["items"])
    assert _request_as(
        cmd_client,
        scope["viewer"],
        "POST",
        f"/api/v1/review-proposals/{proposal.id}/claim",
    ).status_code == 404
    assert _request_as(
        cmd_client,
        scope["viewer"],
        "POST",
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        {
            "outcome": "rejected",
            "reason": "must stay hidden",
            "expected_version": 1,
        },
    ).status_code == 404
