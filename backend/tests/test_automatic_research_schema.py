"""Schema contract for one-click automatic event research."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register every model with Base.metadata
from app.models.event_research import EventResearchBrief
from app.models.ledger import Base, ResearchCase
from app.models.operational import EventResearchLifecycle


def _case(now: datetime) -> ResearchCase:
    return ResearchCase(
        title="Automatic research case",
        industry_topic="event_research",
        created_by="tester",
        created_at=now,
    )


def _brief(case: ResearchCase, now: datetime, *, workflow_mode: str) -> EventResearchBrief:
    return EventResearchBrief(
        research_case_id=case.id,
        raw_input="Company guidance changed after earnings.",
        source_url="https://example.test/event",
        event_title="Guidance changed",
        company_name="Example Co",
        ticker="EXM",
        event_at=now,
        market_reaction="Shares fell.",
        research_question="Did the guidance change drive the reaction?",
        workflow_mode=workflow_mode,
        extraction_state="system_generated",
        created_at=now,
    )


def test_event_research_brief_workflow_mode_defaults_to_reviewed() -> None:
    column = EventResearchBrief.__table__.c.workflow_mode

    assert column.default is not None
    assert column.default.arg == "reviewed"
    assert column.server_default is not None
    assert str(column.server_default.arg) == "reviewed"


def test_automatic_brief_and_completed_lifecycle_persist_for_case() -> None:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    with Session(engine) as session:
        case = _case(now)
        session.add(case)
        session.flush()
        brief = _brief(case, now, workflow_mode="automatic")
        lifecycle = EventResearchLifecycle(
            research_case_id=case.id,
            status="completed",
            active_run_id=None,
            current_round=0,
            status_summary="Automatic research completed.",
            current_gap=None,
            next_human_action=None,
            updated_at=now,
        )
        session.add_all([brief, lifecycle])
        session.commit()

        assert session.get(EventResearchBrief, brief.id).workflow_mode == "automatic"
        assert session.get(EventResearchLifecycle, case.id).status == "completed"


def test_event_research_brief_rejects_unknown_workflow_mode() -> None:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)

    with Session(engine) as session:
        case = _case(now)
        session.add(case)
        session.flush()
        session.add(_brief(case, now, workflow_mode="unknown"))

        with pytest.raises(IntegrityError):
            session.flush()
