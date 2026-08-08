from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.event_impact import CompanyImpactRelation, EventImpactHypothesis
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Company


def test_impact_trace_reads_only_the_current_scope(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "event fixture",
            "event_title": "Impact trace fixture",
            "research_question": "what transmits?",
            "candidate_factors": ["factor one", "factor two", "factor three"],
            "created_by": "tester",
        },
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace")

    assert response.status_code == 200
    body = response.json()
    assert body["scope_version"] == 1
    assert body["factors"] == []
    assert body["alternatives"] == []


def test_trace_excludes_predecessor_and_keeps_unlisted_boundary(cmd_client, cmd_session) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json={"raw_input": "fixture", "event_title": "Trace scopes", "research_question": "why?", "candidate_factors": ["one", "two", "three"], "created_by": "tester"},
    )
    case_id = uuid.UUID(created.json()["case_id"])
    first = cmd_session.scalar(select(EventResearchScopeVersion).where(EventResearchScopeVersion.research_case_id == case_id))
    assert first is not None
    now = datetime.now(timezone.utc)
    old = EventImpactHypothesis(research_case_id=case_id, scope_version_id=first.id, statement="old only", classification="candidate", rank=1, score_components={}, explanation="old", created_at=now)
    second = EventResearchScopeVersion(research_case_id=case_id, version=2, changed_by="tester", change_summary="new", created_at=now)
    old_company = Company(code="OLD-TRACE", name="Old trace", type="unlisted_supplier", created_at=now)
    cmd_session.add_all([old, second, old_company]); cmd_session.flush()
    old_relation = CompanyImpactRelation(hypothesis_id=old.id, scope_version_id=first.id, affected_company_id=old_company.id, relation_kind="supplier", direction="benefits", mechanism="old", status="candidate", source_statement_id=None, created_at=now)
    cmd_session.add(old_relation)
    current = EventImpactHypothesis(research_case_id=case_id, scope_version_id=second.id, statement="current", classification="candidate", rank=1, score_components={}, explanation="current", created_at=now)
    company = Company(code="PRIVATE-TRACE", name="Private trace", type="unlisted_supplier", created_at=now)
    cmd_session.add_all([current, company]); cmd_session.flush()
    cmd_session.add(CompanyImpactRelation(hypothesis_id=current.id, scope_version_id=second.id, affected_company_id=company.id, relation_kind="supplier", direction="benefits", mechanism="fixture", status="candidate", source_statement_id=None, created_at=now))
    cmd_session.commit()

    body = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace").json()

    assert body["scope_version"] == 2
    assert [factor["statement"] for factor in body["factors"]] == ["current"]
    relation = body["factors"][0]["relations"][0]
    assert relation["company_type"] == "unlisted_supplier"
    assert relation["stocks"] == [] and relation["fund_exposure"] == []
    stale = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{old_relation.id}/review",
        json={"outcome": "accepted", "reason": "stale", "reviewer": "tester"},
    )
    assert stale.status_code == 422
