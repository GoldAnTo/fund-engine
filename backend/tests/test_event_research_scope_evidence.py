from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.event_research import (
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import ResearchCase, Thesis
from app.services import event_research_scope_evidence


def test_explicit_scope_canonical_theses_follow_factor_order(cmd_session):
    now = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    case = ResearchCase(
        title="Ordered scope",
        industry_topic="semiconductors",
        created_by="human:test",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="human:test",
        change_summary="ordered factors",
        created_at=now,
    )
    cmd_session.add(scope)
    cmd_session.flush()
    cmd_session.add_all(
        [
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement="第二个因素",
                description=None,
                position=2,
            ),
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement="第一个因素",
                description=None,
                position=1,
            ),
        ]
    )
    later_duplicate = Thesis(
        research_case_id=case.id,
        statement="第一个因素",
        created_by="human:later",
        created_at=now + timedelta(minutes=2),
    )
    earliest_first = Thesis(
        research_case_id=case.id,
        statement="第一个因素",
        created_by="human:earliest",
        created_at=now,
    )
    earliest_second = Thesis(
        research_case_id=case.id,
        statement="第二个因素",
        created_by="human:earliest",
        created_at=now + timedelta(minutes=1),
    )
    cmd_session.add_all(
        [later_duplicate, earliest_first, earliest_second]
    )
    cmd_session.flush()
    helper = getattr(
        event_research_scope_evidence,
        "canonical_scope_thesis_ids",
        None,
    )

    assert helper is not None
    ordered = helper(cmd_session, case.id, scope.id)

    assert ordered == [earliest_first.id, earliest_second.id]
    assert event_research_scope_evidence.current_scope_thesis_ids(
        cmd_session,
        case.id,
    ) == {earliest_first.id, earliest_second.id}
