"""Gap-fill read API tests (对接清单 G1–G4)."""
from __future__ import annotations

from sqlalchemy import select


# ---------------------------------------------------------------------------
# G1: GET /api/v1/provider-runs
# ---------------------------------------------------------------------------


def test_provider_runs_lists_airun_audit(cmd_client, cmd_seeded):
    from app.models.ledger import ResearchCase, Thesis

    case = cmd_seeded.scalar(select(ResearchCase))
    thesis = cmd_seeded.scalar(
        select(Thesis).where(Thesis.research_case_id == case.id)
    )
    reran = cmd_client.post(f"/api/v1/theses/{thesis.id}/rerun")
    assert reran.status_code == 201

    response = cmd_client.get("/api/v1/provider-runs", params={"kind": "assess"})
    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "success"
    assert runs[0]["kind"] == "assess"
    assert runs[0]["input_ref"]["thesis_id"] == str(thesis.id)


def test_provider_runs_empty(cmd_client, cmd_session):
    response = cmd_client.get("/api/v1/provider-runs")
    assert response.status_code == 200
    assert response.json()["runs"] == []


# ---------------------------------------------------------------------------
# G2: GET /api/v1/research-cases/{id}/snapshots
# ---------------------------------------------------------------------------


def test_case_snapshots_lists_frozen_snapshots(api_client, seeded_session):
    from app.models.ledger import ResearchCase

    case = seeded_session.scalar(select(ResearchCase))
    response = api_client.get(f"/api/v1/research-cases/{case.id}/snapshots")
    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    assert len(snapshots) == 3
    assert all(s["link_count"] == 5 for s in snapshots)
    assert all(s["thesis_statement"] for s in snapshots)


def test_case_snapshots_404(api_client, seeded_session):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/snapshots"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# G3: GET /api/v1/knowledge
# ---------------------------------------------------------------------------


def test_knowledge_layer_lists_statements_with_links(api_client, seeded_session):
    from app.models.ledger import ResearchCase

    case = seeded_session.scalar(select(ResearchCase))
    response = api_client.get("/api/v1/knowledge", params={"case_id": str(case.id)})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 15  # one per seeded statement
    item = items[0]
    assert item["verbatim_text"]
    assert item["links"][0]["review_state"] == "machine_generated"


def test_knowledge_layer_surfaces_link_review(cmd_client, cmd_seeded):
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase))
    queue = cmd_client.get("/api/v1/review-queue").json()["items"]
    link_id = queue[0]["link_id"]
    reviewed = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "supports",
            "factor_role": "需求驱动因素",
            "scope_boundary": "当前截止日口径",
            "reason": "原文一致",
            "reviewer": "gap-tester",
        },
    )
    assert reviewed.status_code == 201

    response = cmd_client.get("/api/v1/knowledge", params={"case_id": str(case.id)})
    items = response.json()["items"]
    link = next(
        link
        for item in items
        for link in item["links"]
        if link["link_id"] == link_id
    )
    assert link["latest_review_outcome"] == "confirmed"
    assert link["latest_reviewer"] == "gap-tester"


# ---------------------------------------------------------------------------
# G4: dossier 透出 Thesis 增强字段
# ---------------------------------------------------------------------------


def test_dossier_exposes_falsifiable_thesis_fields(cmd_client, cmd_session):
    created = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "G4 case",
            "industry_topic": "ai_compute",
            "created_by": "g4",
            "initial_theses": [
                {
                    "statement": "可反证命题",
                    "title": "命题 1",
                    "observation_start": "2026-01-01",
                    "observation_end": "2027-12-31",
                    "support_condition": "支持条件",
                    "falsification_condition": "反证条件",
                    "next_verification_event": "下一验证事件",
                    "creator_type": "ai",
                }
            ],
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]

    dossier = cmd_client.get(f"/api/v1/research-cases/{case_id}/dossier")
    assert dossier.status_code == 200, dossier.text
    theses = dossier.json()["theses"]
    assert len(theses) == 1
    thesis = theses[0]
    assert thesis["title"] == "命题 1"
    assert thesis["observation_start"] == "2026-01-01"
    assert thesis["observation_end"] == "2027-12-31"
    assert thesis["support_condition"] == "支持条件"
    assert thesis["falsification_condition"] == "反证条件"
    assert thesis["next_verification_event"] == "下一验证事件"
    assert thesis["creator_type"] == "ai"
    assert thesis["review_state"] == "draft"
