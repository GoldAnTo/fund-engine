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


def test_scope_api_stays_empty_until_explicit_candidate_extraction(cmd_client) -> None:
    created = _create_report(cmd_client)
    case_id = created["case"]["id"]

    before = cmd_client.get(f"/api/v1/report-research/{case_id}/scopes")

    assert before.status_code == 200
    initial = before.json()
    assert initial == {"items": [], "current_scope_version": None}
    current = cmd_client.get(f"/api/v1/report-research/{case_id}/scopes/current")
    assert current.status_code == 404

    appended = cmd_client.post(
        f"/api/v1/report-research/{case_id}/scopes",
        json={
            "document_id": created["document"]["id"],
            "research_question": "供应商关系是否足以解释目标公司的发布后波动？",
            "factor_selection": ["供应链传导"],
            "evidence_plan": ["核对公告", "核对 1D / 5D 市场窗口"],
            "selected_claim_ids": [str(uuid.uuid4())],
            "selected_relation_ids": [],
            "changed_by": "alice",
            "change_summary": "改为只验证供应链路径",
        },
    )

    assert appended.status_code == 422
    assert "selected claims" in appended.json()["error"]["message"]


def test_scope_api_rejects_unextracted_claim_selection(cmd_client) -> None:
    owner = _create_report(cmd_client, title="归属研报")
    owner_case_id = owner["case"]["id"]
    response = cmd_client.post(
        f"/api/v1/report-research/{owner_case_id}/scopes",
        json={
            "document_id": owner["document"]["id"],
            "research_question": "不应允许外部路径进入当前案例",
            "selected_claim_ids": [str(uuid.uuid4())],
            "selected_relation_ids": [],
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
