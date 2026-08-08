"""Public case-scoped commands for immutable report research scopes."""
from __future__ import annotations

import uuid


def _create_report(cmd_client, *, title: str = "范围 API 研报") -> dict:
    response = cmd_client.post(
        "/api/v1/report-research",
        json={
            "input_kind": "pasted_text",
            "title": title,
            "content": "研报观点：供应商甲是目标公司的供应商。",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_scope_api_lists_current_scope_and_appends_a_successor(cmd_client) -> None:
    created = _create_report(cmd_client)
    case_id = created["case"]["id"]

    before = cmd_client.get(f"/api/v1/report-research/{case_id}/scopes")

    assert before.status_code == 200
    initial = before.json()
    assert initial["current_scope_version"] == 1
    assert [scope["version"] for scope in initial["items"]] == [1]
    source_scope = initial["items"][0]
    assert source_scope["selected_claim_ids"]
    assert source_scope["selected_relation_ids"]

    appended = cmd_client.post(
        f"/api/v1/report-research/{case_id}/scopes",
        json={
            "document_id": source_scope["document_id"],
            "research_question": "供应商关系是否足以解释目标公司的发布后波动？",
            "factor_selection": ["供应链传导"],
            "evidence_plan": ["核对公告", "核对 1D / 5D 市场窗口"],
            "selected_claim_ids": source_scope["selected_claim_ids"],
            "selected_relation_ids": source_scope["selected_relation_ids"],
        },
    )

    assert appended.status_code == 201
    successor = appended.json()
    assert successor["version"] == 2
    assert successor["research_question"] == "供应商关系是否足以解释目标公司的发布后波动？"
    assert successor["factor_selection"] == ["供应链传导"]
    assert successor["selected_claim_ids"] == source_scope["selected_claim_ids"]

    after = cmd_client.get(f"/api/v1/report-research/{case_id}/scopes")
    assert after.status_code == 200
    assert after.json()["current_scope_version"] == 2
    assert [scope["version"] for scope in after.json()["items"]] == [1, 2]
    # The successor must not mutate the historical question or selections.
    assert after.json()["items"][0] == source_scope

    current = cmd_client.get(f"/api/v1/report-research/{case_id}/scopes/current")
    assert current.status_code == 200
    assert current.json()["version"] == 2


def test_scope_api_rejects_foreign_claim_or_relation_selection(cmd_client) -> None:
    owner = _create_report(cmd_client, title="归属研报")
    foreign = _create_report(cmd_client, title="外部研报")
    owner_case_id = owner["case"]["id"]
    owner_scope = cmd_client.get(
        f"/api/v1/report-research/{owner_case_id}/scopes"
    ).json()["items"][0]
    foreign_scope = cmd_client.get(
        f"/api/v1/report-research/{foreign['case']['id']}/scopes"
    ).json()["items"][0]

    response = cmd_client.post(
        f"/api/v1/report-research/{owner_case_id}/scopes",
        json={
            "document_id": owner_scope["document_id"],
            "research_question": "不应允许外部路径进入当前案例",
            "selected_claim_ids": foreign_scope["selected_claim_ids"],
            "selected_relation_ids": foreign_scope["selected_relation_ids"],
        },
    )

    assert response.status_code == 422
    assert "selected claims" in response.json()["error"]["message"]


def test_scope_api_returns_not_found_for_unknown_case(cmd_client) -> None:
    response = cmd_client.get(f"/api/v1/report-research/{uuid.uuid4()}/scopes")

    assert response.status_code == 404


def test_scope_append_returns_not_found_for_unknown_case(cmd_client) -> None:
    response = cmd_client.post(
        f"/api/v1/report-research/{uuid.uuid4()}/scopes",
        json={
            "document_id": str(uuid.uuid4()),
            "research_question": "不存在的案例不能追加范围",
            "selected_claim_ids": [str(uuid.uuid4())],
        },
    )

    assert response.status_code == 404
