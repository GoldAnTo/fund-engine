"""Command-side v1 API tests for theme-tag assignment (横切主题标签).

Uses the private-engine ``cmd_*`` fixtures: command endpoints COMMIT, so
they never share the session engine.
"""
from __future__ import annotations

from sqlalchemy import select


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _seed_case(cmd_session, title: str = "AI 算力链") -> object:
    from app.repositories.research import ResearchRepository

    return ResearchRepository(cmd_session).add_case(
        title=title, industry_topic="ai_compute", created_by="tester"
    )


def test_patch_theme_tags_appends_add_events(cmd_client, cmd_session):
    case = _seed_case(cmd_session)

    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化", "云厂商CapEx"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "v1"
    assert body["tags"] == ["云厂商CapEx", "算力国产化"]
    assert body["events_appended"] == 2


def test_patch_theme_tags_is_idempotent(cmd_client, cmd_session):
    from app.models.ledger import CaseThemeTagEvent

    case = _seed_case(cmd_session)
    first = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"]},
    )
    assert first.status_code == 200

    second = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"]},
    )
    assert second.status_code == 200
    assert second.json()["events_appended"] == 0

    events = cmd_session.scalars(select(CaseThemeTagEvent)).all()
    assert len(events) == 1


def test_patch_theme_tags_appends_remove_events(cmd_client, cmd_session):
    from app.models.ledger import CaseThemeTagEvent

    case = _seed_case(cmd_session)
    cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化", "云厂商CapEx"]},
    )
    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"]},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["算力国产化"]
    assert response.json()["events_appended"] == 1

    # History is append-only: both the add and the remove stay on the ledger.
    ops = [
        (event.tag, event.op)
        for event in cmd_session.scalars(select(CaseThemeTagEvent))
    ]
    assert ("云厂商CapEx", "add") in ops
    assert ("云厂商CapEx", "remove") in ops


def test_patch_theme_tags_unknown_tag_is_422(cmd_client, cmd_session):
    case = _seed_case(cmd_session)
    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["元宇宙"]},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"

    from app.models.ledger import CaseThemeTagEvent

    assert cmd_session.scalars(select(CaseThemeTagEvent)).all() == []


def test_patch_theme_tags_blank_tag_is_422(cmd_client, cmd_session):
    case = _seed_case(cmd_session)
    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["  "]},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_patch_theme_tags_missing_case_is_404(cmd_client):
    response = cmd_client.patch(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/theme-tags",
        json={"tags": ["算力国产化"]},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"
