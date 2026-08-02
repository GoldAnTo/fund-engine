"""Command-side v1 API tests for causal-chain write paths.

Covers POST /api/v1/theses/{id}/causal-steps and
POST /api/v1/theses/{id}/causal-edges. Uses the private-engine ``cmd_*``
fixtures: command endpoints COMMIT, so they never share the session engine.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _create_thesis(cmd_client) -> str:
    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "因果链测试案例",
            "industry_topic": "ai_compute",
            "created_by": "analyst-test",
            "initial_theses": [{"statement": "需求爆发驱动收入增长"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["theses"][0]["id"]


def _create_step(cmd_client, thesis_id: str, sequence: int = 1) -> dict:
    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-steps",
        json={"description": f"第 {sequence} 步传导", "sequence": sequence},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# POST /api/v1/theses/{thesis_id}/causal-steps
# ---------------------------------------------------------------------------


def test_create_causal_step_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import CausalStep

    thesis_id = _create_thesis(cmd_client)
    body = _create_step(cmd_client, thesis_id)

    assert body["thesis_id"] == thesis_id
    assert body["sequence"] == 1
    assert body["description"] == "第 1 步传导"

    row = cmd_session.scalar(select(CausalStep))
    assert row is not None
    assert str(row.id) == body["id"]


def test_causal_step_missing_thesis_is_404(cmd_client):
    response = cmd_client.post(
        "/api/v1/theses/00000000-0000-0000-0000-000000000000/causal-steps",
        json={"description": "x", "sequence": 1},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_causal_step_blank_description_is_422(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-steps",
        json={"description": "   ", "sequence": 1},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


@pytest.mark.parametrize("sequence", [0, -3])
def test_causal_step_non_positive_sequence_is_422(cmd_client, sequence):
    thesis_id = _create_thesis(cmd_client)
    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-steps",
        json={"description": "x", "sequence": sequence},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_causal_step_duplicate_sequence_is_422(cmd_client, cmd_session):
    from app.models.ledger import CausalStep

    thesis_id = _create_thesis(cmd_client)
    _create_step(cmd_client, thesis_id, sequence=1)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-steps",
        json={"description": "另一个第一步", "sequence": 1},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"

    count = cmd_session.scalar(select(func.count()).select_from(CausalStep))
    assert count == 1


# ---------------------------------------------------------------------------
# POST /api/v1/theses/{thesis_id}/causal-edges
# ---------------------------------------------------------------------------


def _edge_payload(source_id: str, target_id: str, **overrides) -> dict:
    payload = {
        "source_step_id": source_id,
        "target_step_id": target_id,
        "rationale": "人工复核的传导关系",
    }
    payload.update(overrides)
    return payload


def test_create_causal_edge_human_confirmed(cmd_client, cmd_session):
    from app.models.ledger import CausalEdge

    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"]),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["creator_type"] == "human"
    assert body["review_state"] == "confirmed"

    row = cmd_session.scalar(select(CausalEdge))
    assert row is not None
    assert str(row.source_step_id) == s1["id"]
    assert str(row.target_step_id) == s2["id"]


def test_create_causal_edge_ai_starts_as_draft(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"], creator_type="ai"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["review_state"] == "draft"


def test_causal_edge_missing_thesis_is_404(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    response = cmd_client.post(
        "/api/v1/theses/00000000-0000-0000-0000-000000000000/causal-edges",
        json=_edge_payload(s1["id"], s2["id"]),
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_causal_edge_missing_step_is_404(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], str(uuid.uuid4())),
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_causal_edge_self_loop_is_422(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s1["id"]),
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_causal_edge_cross_thesis_steps_is_422(cmd_client):
    thesis_a = _create_thesis(cmd_client)
    thesis_b = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_a, 1)
    s2 = _create_step(cmd_client, thesis_b, 1)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_a}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"]),
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_causal_edge_duplicate_pair_is_422(cmd_client, cmd_session):
    from app.models.ledger import CausalEdge

    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    first = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"]),
    )
    assert first.status_code == 201, first.text

    second = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"]),
    )
    assert second.status_code == 422
    assert _error_code(second) == "validation_failed"

    count = cmd_session.scalar(select(func.count()).select_from(CausalEdge))
    assert count == 1


def test_causal_edge_invalid_creator_type_is_422(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"], creator_type="robot"),
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_causal_edge_blank_rationale_is_422(cmd_client):
    thesis_id = _create_thesis(cmd_client)
    s1 = _create_step(cmd_client, thesis_id, 1)
    s2 = _create_step(cmd_client, thesis_id, 2)

    response = cmd_client.post(
        f"/api/v1/theses/{thesis_id}/causal-edges",
        json=_edge_payload(s1["id"], s2["id"], rationale="  "),
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"
