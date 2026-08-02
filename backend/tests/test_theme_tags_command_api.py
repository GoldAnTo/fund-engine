"""Command-side v1 API tests for theme-tag assignment (横切主题标签).

Uses the private-engine ``cmd_*`` fixtures: command endpoints COMMIT, so
they never share the session engine.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _seed_case(cmd_session, title: str = "AI 算力链") -> object:
    from app.repositories.research import ResearchRepository

    return ResearchRepository(cmd_session).add_case(
        title=title, industry_topic="ai_compute", created_by="tester"
    )


def test_patch_theme_tags_human_appends_confirmed_events(cmd_client, cmd_session):
    """Default ``proposed_by='human'`` lands events as confirmed, immediately effective."""
    from app.models.ledger import CaseThemeTagEvent

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
    assert body["proposed_by"] == "human"
    assert body["proposal_id"] is None

    events = cmd_session.scalars(select(CaseThemeTagEvent)).all()
    assert all(event.status == "confirmed" for event in events)
    assert all(event.proposed_by == "human" for event in events)
    assert all(event.proposal_id is None for event in events)


def test_patch_theme_tags_ai_appends_pending_events(cmd_client, cmd_session):
    """AI PATCHes create a proposal whose events are pending — effective set unchanged."""
    from app.models.ledger import CaseThemeTagEvent

    case = _seed_case(cmd_session)

    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化", "云厂商CapEx"], "proposed_by": "ai"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Effective set is empty (the proposal does not change it yet).
    assert body["tags"] == []
    assert body["proposed_by"] == "ai"
    assert body["events_appended"] == 2
    assert body["proposal_id"] is not None
    assert body["promoted_proposal_id"] is None

    events = cmd_session.scalars(select(CaseThemeTagEvent)).all()
    assert len(events) == 2
    assert all(event.status == "pending" for event in events)
    assert all(event.proposed_by == "ai" for event in events)
    proposal_id = body["proposal_id"]
    assert all(str(event.proposal_id) == proposal_id for event in events)


def test_patch_theme_tags_human_promotes_matching_ai_proposal(cmd_client, cmd_session):
    """Human PATCH with the AI's desired set promotes the proposal to confirmed.

    The two-stage flow is exercised end-to-end: AI proposes, human
    confirms by re-sending the same set, effective tags are now visible.
    """
    from app.models.ledger import CaseThemeTagEvent

    case = _seed_case(cmd_session)

    # 1) AI proposes
    proposed = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化", "云厂商CapEx"], "proposed_by": "ai"},
    )
    proposal_id = proposed.json()["proposal_id"]
    assert proposed.json()["tags"] == []

    # 2) Human confirms with the same desired set
    confirmed = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化", "云厂商CapEx"], "proposed_by": "human"},
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["tags"] == ["云厂商CapEx", "算力国产化"]
    assert body["proposed_by"] == "human"
    assert body["proposal_id"] is None
    assert body["promoted_proposal_id"] == proposal_id  # both UUID-as-string

    # The ledger is append-only: the two original AI events stay
    # status='pending' (they are part of the audit trail but never
    # count toward the effective set), and two new confirmed human
    # events carry the same proposal_id. The effective fold now sees
    # the confirmed add+remove pair and produces the intended set.
    events = cmd_session.scalars(
        select(CaseThemeTagEvent).order_by(CaseThemeTagEvent.created_at)
    ).all()
    assert len(events) == 4
    statuses = [event.status for event in events]
    assert statuses.count("pending") == 2
    assert statuses.count("confirmed") == 2
    proposed_by = [event.proposed_by for event in events]
    assert proposed_by.count("ai") == 2
    assert proposed_by.count("human") == 2
    assert all(event.proposal_id == uuid.UUID(proposal_id) for event in events)


def test_patch_theme_tags_human_with_different_set_does_not_promote(
    cmd_client, cmd_session
):
    """A human PATCH whose set differs from the AI's proposal does NOT promote.

    Instead it appends fresh confirmed human events. The AI's proposal
    stays pending (it is the operator's choice to ignore, confirm, or
    come back later).
    """
    from app.models.ledger import CaseThemeTagEvent

    case = _seed_case(cmd_session)

    cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"], "proposed_by": "ai"},
    )

    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["光模块"], "proposed_by": "human"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == ["光模块"]
    assert body["proposed_by"] == "human"
    assert body["proposal_id"] is None
    assert body["promoted_proposal_id"] is None
    assert body["events_appended"] == 1

    pending = [
        e
        for e in cmd_session.scalars(select(CaseThemeTagEvent)).all()
        if e.status == "pending"
    ]
    assert len(pending) == 1  # the AI's proposal is still pending


def test_patch_theme_tags_human_direct_write_when_no_proposal(
    cmd_client, cmd_session
):
    """Without an AI proposal, human PATCH behaves like a direct write."""
    case = _seed_case(cmd_session)

    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"], "proposed_by": "human"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == ["算力国产化"]
    assert body["proposal_id"] is None
    assert body["promoted_proposal_id"] is None
    assert body["events_appended"] == 1


def test_patch_theme_tags_is_idempotent_for_human(cmd_client, cmd_session):
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

    from app.models.ledger import CaseThemeTagEvent

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


def test_patch_theme_tags_unknown_proposed_by_is_422(cmd_client, cmd_session):
    """Pydantic rejects any value other than 'human' / 'ai' for proposed_by."""
    case = _seed_case(cmd_session)
    response = cmd_client.patch(
        f"/api/v1/research-cases/{case.id}/theme-tags",
        json={"tags": ["算力国产化"], "proposed_by": "robot"},
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
