from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.api.v1.case_access as case_access_api
from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.models.identity import CaseAccessGrant, ResearchUser
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.security.principal import ResearchPrincipal
from app.services.event_research import EventResearchService


def _principal(cmd_session, *, tenant_id: str, label: str) -> ResearchPrincipal:
    now = datetime.now(UTC)
    user = ResearchUser(
        issuer="https://test-identity.invalid/realms/research",
        subject=f"case-access:{tenant_id}:{label}:{uuid.uuid4()}",
        tenant_id=tenant_id,
        display_name=label,
        normalized_email=f"{label}@{tenant_id}.invalid",
        active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(user)
    cmd_session.flush()
    return ResearchPrincipal(
        user_id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        roles=frozenset(),
        expires_at=now + timedelta(hours=1),
    )


def _request_as(cmd_client, principal, method: str, url: str, *, body=None):
    previous = app.dependency_overrides.get(require_research_actor)
    app.dependency_overrides[require_research_actor] = lambda: principal
    try:
        return cmd_client.request(method, url, json=body)
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_research_actor, None)
        else:
            app.dependency_overrides[require_research_actor] = previous


def _case(cmd_session, owner: ResearchPrincipal) -> uuid.UUID:
    created = EventResearchService(cmd_session).create(
        CreateEventResearchRequest(
            raw_input="A company changed its capacity outlook.",
            event_title="Capacity outlook changed",
            research_question="Will the change alter supplier demand?",
            candidate_factors=["Capacity", "Orders", "Revenue"],
        ),
        principal=owner,
    )
    cmd_session.commit()
    return uuid.UUID(created.case_id)


def test_owner_manages_case_access_through_authenticated_http(
    cmd_client,
    cmd_session,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="owner")
    viewer = _principal(cmd_session, tenant_id="team-a", label="viewer")
    case_id = _case(cmd_session, owner)

    granted = _request_as(
        cmd_client,
        owner,
        "POST",
        f"/api/v1/event-research/{case_id}/access-grants",
        body={
            "target_user_id": str(viewer.user_id),
            "role": "viewer",
            "reason": "Share the frozen Case for independent review.",
        },
    )

    assert granted.status_code == 201, granted.text
    assert granted.json() == {
        "user_id": str(viewer.user_id),
        "display_name": "viewer",
        "normalized_email": "viewer@team-a.invalid",
        "role": "viewer",
        "reason": "Share the frozen Case for independent review.",
    }
    listing = _request_as(
        cmd_client,
        owner,
        "GET",
        f"/api/v1/event-research/{case_id}/access-grants",
    )
    assert listing.status_code == 200, listing.text
    assert [(item["display_name"], item["role"]) for item in listing.json()["items"]] == [
        ("owner", "owner"),
        ("viewer", "viewer"),
    ]
    assert _request_as(
        cmd_client,
        viewer,
        "GET",
        f"/api/v1/event-research/{case_id}/workbench",
    ).status_code == 200

    changed = _request_as(
        cmd_client,
        owner,
        "PATCH",
        f"/api/v1/event-research/{case_id}/access-grants/{viewer.user_id}",
        body={"role": "editor", "reason": "Viewer will now maintain the scope."},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role"] == "editor"

    revoked = _request_as(
        cmd_client,
        owner,
        "DELETE",
        f"/api/v1/event-research/{case_id}/access-grants/{viewer.user_id}",
        body={"reason": "Access window closed."},
    )
    assert revoked.status_code == 204, revoked.text
    assert cmd_session.scalar(
        select(CaseAccessGrant).where(
            CaseAccessGrant.research_case_id == case_id,
            CaseAccessGrant.user_id == viewer.user_id,
        )
    ) is None


def test_access_management_hides_ungranted_case_and_rejects_cross_tenant_target(
    cmd_client,
    cmd_session,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="owner")
    same_tenant = _principal(cmd_session, tenant_id="team-a", label="ungranted")
    outsider = _principal(cmd_session, tenant_id="team-b", label="outsider")
    case_id = _case(cmd_session, owner)

    hidden = _request_as(
        cmd_client,
        same_tenant,
        "GET",
        f"/api/v1/event-research/{case_id}/access-grants",
    )
    assert hidden.status_code == 404, hidden.text

    cross_tenant = _request_as(
        cmd_client,
        owner,
        "POST",
        f"/api/v1/event-research/{case_id}/access-grants",
        body={
            "target_user_id": str(outsider.user_id),
            "role": "viewer",
            "reason": "This must not cross the tenant boundary.",
        },
    )
    assert cross_tenant.status_code == 404, cross_tenant.text
    assert str(outsider.user_id) not in cross_tenant.text


def test_public_activity_redacts_access_target_and_reason(
    cmd_client,
    cmd_session,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="owner")
    viewer = _principal(cmd_session, tenant_id="team-a", label="viewer")
    case_id = _case(cmd_session, owner)
    secret_reason = "Confidential staffing rationale 91f6d4"

    granted = _request_as(
        cmd_client,
        owner,
        "POST",
        f"/api/v1/event-research/{case_id}/access-grants",
        body={
            "target_user_id": str(viewer.user_id),
            "role": "viewer",
            "reason": secret_reason,
        },
    )
    assert granted.status_code == 201, granted.text

    activity = _request_as(
        cmd_client,
        viewer,
        "GET",
        f"/api/v1/activity?case_id={case_id}&event_type=case_access_granted",
    )
    assert activity.status_code == 200, activity.text
    public_events = activity.json()["items"]
    assert public_events
    assert all("user_id" not in item["payload"] for item in public_events)
    assert all("reason" not in item["payload"] for item in public_events)
    assert str(viewer.user_id) not in activity.text
    assert secret_reason not in activity.text


def test_grant_response_is_the_committed_command_snapshot(
    cmd_client,
    cmd_session,
    monkeypatch,
) -> None:
    owner = _principal(cmd_session, tenant_id="team-a", label="owner")
    viewer = _principal(cmd_session, tenant_id="team-a", label="viewer")
    case_id = _case(cmd_session, owner)
    real_commit = case_access_api.commit_or_rollback

    def commit_then_simulate_a_later_change(db) -> None:
        real_commit(db)
        grant = db.scalar(
            select(CaseAccessGrant).where(
                CaseAccessGrant.research_case_id == case_id,
                CaseAccessGrant.user_id == viewer.user_id,
            )
        )
        assert grant is not None
        grant.role = "reviewer"
        db.flush()

    monkeypatch.setattr(
        case_access_api,
        "commit_or_rollback",
        commit_then_simulate_a_later_change,
    )
    response = _request_as(
        cmd_client,
        owner,
        "POST",
        f"/api/v1/event-research/{case_id}/access-grants",
        body={
            "target_user_id": str(viewer.user_id),
            "role": "viewer",
            "reason": "Return the command result, not a later database state.",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["role"] == "viewer"
