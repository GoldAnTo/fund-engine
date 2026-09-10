from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.api.v1.tenant_context import require_research_actor
from app.main import app
from app.security.principal import ResearchPrincipal


def test_research_session_projects_only_trusted_principal(cmd_client) -> None:
    principal = ResearchPrincipal(
        user_id=uuid.UUID("d16b39dd-5ee9-52ab-8165-93569a03f760"),
        issuer="https://identity.example.test/realms/research",
        subject="subject-never-returned",
        tenant_id="team-a",
        display_name="Alice Researcher",
        roles=frozenset({"reviewer", "editor"}),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    app.dependency_overrides[require_research_actor] = lambda: principal
    try:
        response = cmd_client.get(
            "/api/v1/research-session",
            headers={
                "Authorization": "Bearer raw-token-never-returned",
                "X-Actor": "human:forged",
            },
        )
    finally:
        app.dependency_overrides.pop(require_research_actor, None)

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(principal.user_id),
        "display_name": "Alice Researcher",
        "tenant_id": "team-a",
        "roles": ["editor", "reviewer"],
        "issuer": principal.issuer,
        "expires_at": principal.expires_at.isoformat().replace("+00:00", "Z"),
    }
    serialized = response.text
    assert "raw-token-never-returned" not in serialized
    assert "subject-never-returned" not in serialized
    assert "human:forged" not in serialized
