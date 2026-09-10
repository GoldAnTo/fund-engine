"""End-to-end HTTP flow for the Cambricon profitability case.

Seeds the frozen case through the Python seed function, then drives the real
v1 HTTP contract via FastAPI's TestClient (full ASGI stack): case discovery,
theme tag, conclusion read model, evidence-link reviews, assessment review,
and the post-review conclusion state.

Uses the isolated ``cmd_session`` / ``cmd_client`` fixtures so the command
endpoints (which commit) never pollute the session-scoped shared engine.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.ledger import AIAssessment, ResearchCase
from app.scripts.seed_cambricon_profitability_case import CASE_TITLE, seed


def test_cambricon_case_runs_through_existing_http_flow(cmd_client, cmd_session):
    seeded = seed(cmd_session)
    cmd_session.commit()

    # --- P1: the case is discoverable through the real read API. ---
    cases = cmd_client.get("/api/v1/research-cases", params={"limit": 50}).json()
    assert any(row["id"] == str(seeded.case_id) for row in cases["items"]), cases

    # --- P2: the controlled theme tag is visible. ---
    themes = cmd_client.get("/api/v1/themes").json()
    assert any(row.get("tag") == "算力国产化" for row in themes["items"]), themes

    conclusion_url = f"/api/v1/research-cases/{seeded.case_id}/conclusion"

    # --- P3 (before review): AI provisional draft, no human reviewer. ---
    before = cmd_client.get(conclusion_url).json()
    assert before["header"]["conclusion_status"] == "supported"
    assert before["header"]["ai_provisional"] is True
    assert before["header"]["review_state"] == "provisional"
    assert before["header"]["reviewer"] is None
    # One thesis -> one key factor (the inflection-point thesis).
    assert len(before["key_factors"]) == 1
    # The frozen rationale carries the explicit non-causal boundary and the
    # negative-cash-flow scope warning.
    rendered = str(before)
    assert "经营现金流" in rendered
    assert "可持续" in rendered
    # The reproduction manifest must point back at the frozen snapshot.
    assert before["reproduction_manifest"] is not None

    # --- P4: every machine-generated link enters the review queue. ---
    queue = cmd_client.get(
        "/api/v1/review-queue", params={"limit": 200}
    ).json()
    queue_items = queue["items"]
    assert queue_items, "review queue must list the seeded machine-generated links"
    assert all(item["ai_role"] in {"supports", "contextualizes"} for item in queue_items)

    for item in queue_items:
        response = cmd_client.post(
            f"/api/v1/evidence-links/{item['link_id']}/reviews",
            json={
                "outcome": "confirmed",
                "relation": item["ai_role"],
                "factor_role": "盈利拐点事实或范围限制",
                "scope_boundary": "寒武纪会计利润口径，2024Q4至2025Q4",
                "reason": "逐项核对聚源冻结结果与年报第10页后确认",
                "reviewer": "e2e-human-reviewer",
            },
        )
        assert response.status_code == 201, response.text

    # --- P5: human review of the AI assessment turns it into a formal decision. ---
    assessment = cmd_session.scalar(select(AIAssessment))
    assert assessment is not None
    reviewed = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": "supported",
            "reason": "证据关系已逐项确认，维持狭义盈利拐点判断",
            "reviewer": "e2e-human-reviewer",
        },
    )
    assert reviewed.status_code == 201, reviewed.text

    # --- P6 (after review): formal confirmed state with a real reviewer;
    # the original AI draft remains visible alongside it. ---
    after = cmd_client.get(conclusion_url).json()
    assert after["header"]["conclusion_status"] == "supported"
    assert after["header"]["review_state"] == "confirmed"
    assert after["header"]["reviewer"] == "e2e-human-reviewer"
    assert after["header"]["ai_provisional"] is True  # AI draft still flagged

    # The review queue is drained once every link has been reviewed.
    remaining = cmd_client.get(
        "/api/v1/review-queue", params={"limit": 200}
    ).json()["items"]
    assert remaining == []


def test_cambricon_case_conclusion_404_for_unknown_case(cmd_client):
    response = cmd_client.get(
        f"/api/v1/research-cases/00000000-0000-0000-0000-000000000000/conclusion"
    )
    assert response.status_code == 404
