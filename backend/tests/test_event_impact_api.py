from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import event, select

from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyImpactRelationReview,
    EventImpactHypothesis,
    EventImpactHypothesisAssessment,
)
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Company, Fund, HoldingDisclosure, Stock
from app.models.operational import TaskItem


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
    assert relation["is_high_impact"] is False
    assert relation["is_reviewable"] is False
    assert relation["stocks"] == [] and relation["fund_exposure"] == []
    stale = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{old_relation.id}/review",
        json={"outcome": "accepted", "reason": "stale", "reviewer": "tester"},
    )
    assert stale.status_code == 422


def test_trace_review_is_idempotent_and_keeps_source_gap_nonkey_with_pit_funds(
    cmd_client, cmd_session
) -> None:
    """The HTTP trace keeps a source-gap review auditable without overclaiming it."""
    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "fixture",
            "event_title": "PIT trace",
            "research_question": "what transmits?",
            "candidate_factors": ["one", "two", "three"],
            "created_by": "tester",
        },
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    now = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
    hypothesis = EventImpactHypothesis(
        research_case_id=case_id,
        scope_version_id=scope.id,
        statement="source-gap candidate",
        classification="candidate",
        rank=1,
        score_components={},
        explanation="awaiting an admissible relation source",
        created_at=now,
    )
    company = Company(
        code="PIT-TRACE",
        name="PIT trace issuer",
        type="listed",
        created_at=now,
    )
    fund = Fund(
        code="000001",
        name="China public fund",
        fund_type="equity",
        scale=None,
        establish_date=None,
        management_company_id=None,
        created_at=now,
    )
    cmd_session.add_all([hypothesis, company, fund])
    cmd_session.flush()
    first_stock = Stock(
        company_id=company.id,
        code="600001",
        name="PIT one",
        market="SSE",
        created_at=now,
    )
    second_stock = Stock(
        company_id=company.id,
        code="600002",
        name="PIT two",
        market="SSE",
        created_at=now,
    )
    cmd_session.add_all([first_stock, second_stock])
    cmd_session.flush()
    relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=scope.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="candidate has no admitted relation source",
        status="candidate",
        source_statement_id=None,
        created_at=now,
    )
    cmd_session.add(relation)
    cmd_session.flush()
    cmd_session.add_all([
        # This is an event datum, not an admitted relation source: accepting
        # the review must therefore not make the relation evidence verified.
        CompanyImpactObservation(
            relation_id=relation.id,
            kind="event",
            status="verified",
            source_statement_id=None,
            valuation_snapshot_id=None,
            summary="event happened",
            as_of_date=date(2026, 8, 8),
            created_at=now,
        ),
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=first_stock.id,
            weight=Decimal("0.10"),
            report_period=date(2026, 6, 30),
            published_at=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
            acquired_at=datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
            source="visible filing",
            created_at=now,
        ),
        # It is a valid ledger record but was published after this relation's
        # event cutoff, so it must not turn partial coverage into a result.
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=second_stock.id,
            weight=Decimal("0.20"),
            report_period=date(2026, 6, 30),
            published_at=datetime(2026, 8, 9, 8, tzinfo=timezone.utc),
            acquired_at=datetime(2026, 8, 9, 8, tzinfo=timezone.utc),
            source="future filing",
            created_at=now,
        ),
    ])
    cmd_session.commit()

    holding_queries: list[tuple[str, object]] = []

    def capture_holding_query(
        _conn, _cursor, statement, parameters, _context, _executemany
    ) -> None:
        if "holding_disclosures" in statement.lower():
            holding_queries.append((statement, parameters))

    event.listen(cmd_session.bind, "before_cursor_execute", capture_holding_query)
    try:
        trace = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace")
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", capture_holding_query)
    assert trace.status_code == 200
    assert len(holding_queries) == 1
    assert "published_at <=" in holding_queries[0][0].lower()
    assert "2026-08-08" in str(holding_queries[0][1])
    relation_payload = trace.json()["factors"][0]["relations"][0]
    assert len(relation_payload["fund_exposure"]) == 1
    fund_exposure = relation_payload["fund_exposure"][0]
    assert fund_exposure["coverage_ratio"] == 0.5
    assert fund_exposure["coverage_status"] == "partial"
    assert fund_exposure["computable"] is False
    assert fund_exposure["exposure"] is None
    assert fund_exposure["source"] == "visible filing"
    assert relation_payload["is_high_impact"] is True
    assert relation_payload["is_reviewable"] is True
    # SQLite round-trips datetimes without tzinfo; the HTTP contract is still
    # an explicit UTC timestamp rather than a timezone-ambiguous string.
    assert fund_exposure["published_at"] == "2026-08-07T08:00:00+00:00"

    whitespace = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{relation.id}/review",
        json={"outcome": "accepted", "reason": "  ", "reviewer": "  "},
    )
    assert whitespace.status_code == 422
    accepted = {
        "outcome": "accepted",
        "reason": "reviewed but source remains absent",
        "reviewer": "tester",
    }
    first = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{relation.id}/review", json=accepted
    )
    rejected = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{relation.id}/review",
        json={
            "outcome": "rejected",
            "reason": "initial assessment rejected",
            "reviewer": "tester",
        },
    )
    second = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{relation.id}/review", json=accepted
    )
    replay = cmd_client.post(
        f"/api/v1/event-research/impact-relations/{relation.id}/review", json=accepted
    )
    assert (
        first.status_code
        == rejected.status_code
        == second.status_code
        == replay.status_code
        == 201
    )
    assert first.json()["review_id"] != second.json()["review_id"]
    assert second.json()["review_id"] == replay.json()["review_id"]
    reviews = cmd_session.scalars(
        select(CompanyImpactRelationReview).where(
            CompanyImpactRelationReview.relation_id == relation.id
        )
    ).all()
    assert len(reviews) == 3
    review_task = cmd_session.scalar(
        select(TaskItem).where(
            TaskItem.task_type == "review_company_impact",
            TaskItem.ref_id == relation.id,
            TaskItem.scope_version_id == scope.id,
        )
    )
    assert review_task is not None and review_task.status == "done"
    assessment = cmd_session.scalar(
        select(EventImpactHypothesisAssessment)
        .where(EventImpactHypothesisAssessment.hypothesis_id == hypothesis.id)
        .order_by(
            EventImpactHypothesisAssessment.created_at.desc(),
            EventImpactHypothesisAssessment.id.desc(),
        )
    )
    assert assessment is not None
    assert assessment.classification != "key"
    assert assessment.score_components["company"] == 0
    reviewed_trace = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace")
    review = reviewed_trace.json()["factors"][0]["relations"][0]["review"]
    assert review["outcome"] == "accepted"
    assert review["created_at"].endswith("+00:00")


def test_trace_without_relation_cutoff_does_not_read_fund_history(
    cmd_client, cmd_session
) -> None:
    """A relation with no dated evidence cannot read unbounded fund history."""
    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "fixture",
            "event_title": "No cutoff",
            "research_question": "why?",
            "candidate_factors": ["one", "two", "three"],
            "created_by": "tester",
        },
    )
    case_id = uuid.UUID(created.json()["case_id"])
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    now = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
    hypothesis = EventImpactHypothesis(
        research_case_id=case_id,
        scope_version_id=scope.id,
        statement="no dated evidence",
        classification="candidate",
        rank=1,
        score_components={},
        explanation="no cutoff",
        created_at=now,
    )
    company = Company(
        code="NO-CUTOFF", name="No cutoff issuer", type="listed", created_at=now
    )
    fund = Fund(
        code="000001",
        name="China public fund",
        fund_type="equity",
        scale=None,
        establish_date=None,
        management_company_id=None,
        created_at=now,
    )
    cmd_session.add_all([hypothesis, company, fund])
    cmd_session.flush()
    stock = Stock(
        company_id=company.id,
        code="600003",
        name="No cutoff",
        market="SSE",
        created_at=now,
    )
    cmd_session.add(stock)
    cmd_session.flush()
    relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=scope.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="no date",
        status="candidate",
        source_statement_id=None,
        created_at=now,
    )
    cmd_session.add(relation)
    cmd_session.flush()
    cmd_session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=Decimal("0.10"),
            report_period=date(2026, 6, 30),
            published_at=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
            acquired_at=now,
            source="would leak without cutoff",
            created_at=now,
        )
    )
    cmd_session.commit()

    holding_queries: list[str] = []

    def capture_holding_query(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if "holding_disclosures" in statement.lower():
            holding_queries.append(statement)

    event.listen(cmd_session.bind, "before_cursor_execute", capture_holding_query)
    try:
        trace = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace")
    finally:
        event.remove(cmd_session.bind, "before_cursor_execute", capture_holding_query)
    assert trace.status_code == 200
    assert trace.json()["factors"][0]["relations"][0]["fund_exposure"] == []
    assert holding_queries == []
