"""Command-side v1 API tests (prototype 新建研究 / 审核工作区).

The command endpoints COMMIT, so these tests run on a private in-memory
engine instead of the shared session-scoped one — committed rows must never
leak into other tests (e.g. the release gate's manifest-hash check counts
seeded DocumentVersions exactly).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def cmd_session():
    from app.models.ledger import Base

    eng = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(eng)


@pytest.fixture
def cmd_client(cmd_session):
    from app.db import get_db
    from app.main import app

    def _override_get_db():
        yield cmd_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def cmd_seeded(cmd_session):
    from app.scripts.seed_ai_compute_case import seed

    seed(cmd_session)
    cmd_session.commit()
    return cmd_session


def _error_code(response) -> str:
    return response.json()["error"]["code"]


# ---------------------------------------------------------------------------
# 新建研究: POST /api/v1/research-cases
# ---------------------------------------------------------------------------


def test_create_case_with_framing_and_initial_theses(cmd_client, cmd_session):
    from app.models.ledger import ResearchCase, Thesis

    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "AI 算力产业链",
            "industry_topic": "ai_compute",
            "created_by": "analyst-test",
            "research_object": "从云厂商资本开支到芯片收入的传导",
            "phenomenon": "AI 资本开支持续扩张但订单收入确认节奏分化",
            "core_question": "截至 2026-06-30 算力资本开支能否通过已披露订单验证？",
            "period_start": "2026-01-01",
            "period_end": "2027-12-31",
            "evidence_cutoff": "2026-06-30",
            "initial_theses": [
                {
                    "statement": "云厂商资本开支形成持续算力需求",
                    "title": "命题 1",
                    "observation_start": "2026-01-01",
                    "observation_end": "2027-12-31",
                    "support_condition": "至少两家主要云厂商给出资本开支扩张指引",
                    "falsification_condition": "主要云厂商下调资本开支",
                    "next_verification_event": "核对 2026Q2 云厂商财报",
                },
                {
                    "statement": "AI 草案命题·未经人工复核",
                    "creator_type": "ai",
                },
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["theses"][0]["review_state"] == "confirmed"
    assert body["theses"][1]["review_state"] == "draft"

    case = cmd_session.scalar(select(ResearchCase))
    assert case.core_question.startswith("截至 2026-06-30")
    assert str(case.evidence_cutoff) == "2026-06-30"

    theses = cmd_session.scalars(select(Thesis)).all()
    assert len(theses) == 2
    assert theses[0].falsification_condition == "主要云厂商下调资本开支"
    assert theses[1].creator_type == "ai"


def test_create_case_rejects_inverted_period(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases",
        json={
            "title": "x",
            "industry_topic": "t",
            "created_by": "u",
            "period_start": "2027-01-01",
            "period_end": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_add_thesis_to_missing_case_is_422(cmd_client):
    response = cmd_client.post(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/theses",
        json={"statement": "x", "created_by": "u"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_add_thesis_rejects_inverted_observation_window(cmd_client, cmd_session):
    from app.models.ledger import ResearchCase

    created = cmd_client.post(
        "/api/v1/research-cases",
        json={"title": "c", "industry_topic": "t", "created_by": "u"},
    )
    case_id = created.json()["case_id"]

    response = cmd_client.post(
        f"/api/v1/research-cases/{case_id}/theses",
        json={
            "statement": "s",
            "created_by": "u",
            "observation_start": "2027-01-01",
            "observation_end": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


# ---------------------------------------------------------------------------
# 审核队列: GET /api/v1/review-queue
# ---------------------------------------------------------------------------


def test_review_queue_lists_pending_machine_links(cmd_client, cmd_seeded):
    response = cmd_client.get("/api/v1/review-queue")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 15  # all seeded links are machine-generated

    item = items[0]
    assert item["ai_role"] in {"supports", "contradicts", "contextualizes"}
    assert item["verbatim_text"], "queue item must carry the frozen span text"
    assert item["thesis_statement"]
    assert item["document_source_url"]


def test_review_queue_empty_when_nothing_seeded(cmd_client, cmd_session):
    response = cmd_client.get("/api/v1/review-queue")
    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------------------
# 关系级审核: POST /api/v1/evidence-links/{id}/reviews
# ---------------------------------------------------------------------------


def _first_queue_item_id(cmd_client) -> str:
    return cmd_client.get("/api/v1/review-queue").json()["items"][0]["link_id"]


def test_confirmed_link_review_leaves_queue(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)

    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "supports",
            "factor_role": "需求驱动因素",
            "scope_boundary": "仅适用于当前截止日与该分部口径",
            "reason": "原文披露与 AI 提议一致",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text
    review = response.json()["review"]
    assert review["outcome"] == "confirmed"
    assert review["relation"] == "supports"

    remaining = cmd_client.get("/api/v1/review-queue").json()["items"]
    assert len(remaining) == 14
    assert all(i["link_id"] != link_id for i in remaining)


def test_confirmed_review_requires_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_confirmed_review_rejects_evidence_gap_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "evidence_gap",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 422


def test_rejected_review_needs_no_relation(cmd_client, cmd_seeded):
    link_id = _first_queue_item_id(cmd_client)
    response = cmd_client.post(
        f"/api/v1/evidence-links/{link_id}/reviews",
        json={
            "outcome": "rejected",
            "factor_role": "不适用",
            "scope_boundary": "不适用",
            "reason": "AI 误把公司整体口径当作分部证据",
            "reviewer": "r",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["review"]["relation"] is None


def test_review_missing_link_is_404(cmd_client, cmd_seeded):
    response = cmd_client.post(
        "/api/v1/evidence-links/00000000-0000-0000-0000-000000000000/reviews",
        json={
            "outcome": "rejected",
            "factor_role": "x",
            "scope_boundary": "y",
            "reason": "z",
            "reviewer": "r",
        },
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


# ---------------------------------------------------------------------------
# 评估级审核: POST /api/v1/assessments/{id}/reviews
# ---------------------------------------------------------------------------


def test_assessment_review_roundtrip(cmd_client, cmd_seeded):
    from app.models.ledger import AIAssessment

    assessment = cmd_seeded.scalar(select(AIAssessment))
    response = cmd_client.post(
        f"/api/v1/assessments/{assessment.id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": assessment.conclusion,
            "reason": "人工确认",
            "reviewer": "reviewer-test",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "confirmed"


def test_assessment_review_missing_is_404(cmd_client, cmd_seeded):
    response = cmd_client.post(
        "/api/v1/assessments/00000000-0000-0000-0000-000000000000/reviews",
        json={"outcome": "confirmed", "reason": "x", "reviewer": "r"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"
