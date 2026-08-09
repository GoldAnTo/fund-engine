from __future__ import annotations


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _event_payload() -> dict[str, object]:
    return {
        "raw_input": "某公司披露新的订单节奏，后续收入兑现仍需要核验。",
        "source_type": "pasted_snapshot",
        "source_metadata": {"original_url": "https://example.test/tenant-event"},
        "event_title": "订单节奏更新",
        "company_name": "示例公司",
        "ticker": "000001",
        "research_question": "订单更新是否会改变收入兑现预期？",
        "candidate_factors": ["订单确认节奏", "产能交付能力", "客户需求持续性"],
        "created_by": "human:researcher",
    }


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


def test_foreign_tenant_cannot_read_or_attach_to_an_event_case(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}'
    )
    created = cmd_client.post(
        "/api/v1/event-research", json=_event_payload(), headers=_auth("token-a")
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    owner_list = cmd_client.get("/api/v1/event-research", headers=_auth("token-a"))
    foreign_list = cmd_client.get("/api/v1/event-research", headers=_auth("token-b"))
    assert [item["case_id"] for item in owner_list.json()["items"]] == [case_id]
    assert foreign_list.json()["items"] == []

    foreign_read = cmd_client.get(
        f"/api/v1/event-research/{case_id}/workbench", headers=_auth("token-b")
    )
    foreign_write = cmd_client.post(
        f"/api/v1/event-research/{case_id}/materials",
        headers=_auth("token-b"),
        json={
            "raw_input": "外部团队不应写入的材料。",
            "source_type": "pasted_snapshot",
            "source_metadata": {},
            "actor": "human:foreign",
        },
    )

    assert foreign_read.status_code == 404
    assert foreign_write.status_code == 404
