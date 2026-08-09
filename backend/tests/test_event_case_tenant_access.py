from __future__ import annotations


def test_event_routes_reject_missing_or_unknown_tenant_credentials(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"token-a":"team-a"}')

    missing = cmd_client.get("/api/v1/event-research", headers={"Authorization": ""})
    unknown = cmd_client.get(
        "/api/v1/event-research", headers={"Authorization": "Bearer unknown"}
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_required"
    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "permission_denied"
