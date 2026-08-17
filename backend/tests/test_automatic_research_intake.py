from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.event_research import EventResearchBrief
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_monitor import ResearchRunEvent
from app.models.research_preparation import ResearchPreparation
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction


class FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def extract(self, raw_input: str, source_url: str | None) -> EventExtraction:
        self.calls.append((raw_input, source_url))
        return EventExtraction(
            event_title=raw_input[:80],
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


def _scope_event(session, run_id: uuid.UUID) -> ResearchRunEvent:
    event = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run_id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    return event


def test_topic_input_creates_one_automatic_case_and_queued_run(session) -> None:
    extractor = FakeExtractor()
    raw_input = "光模块行业需求会如何变化"

    started = AutomaticResearchIntakeService(session, extractor=extractor).start(
        raw_input, tenant_id="research-team"
    )

    case_id = uuid.UUID(started.case_id)
    run_id = uuid.UUID(started.run_id)
    brief = session.scalar(
        select(EventResearchBrief).where(EventResearchBrief.research_case_id == case_id)
    )
    lifecycle = session.get(EventResearchLifecycle, case_id)
    runs = list(
        session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id))
    )
    assert extractor.calls == [(raw_input, None)]
    assert brief is not None
    assert brief.workflow_mode == "automatic"
    assert brief.extraction_state == "system_generated"
    assert lifecycle is not None
    assert lifecycle.status == "researching"
    assert lifecycle.active_run_id == run_id
    assert lifecycle.next_human_action is None
    assert len(runs) == 1
    assert runs[0].id == run_id
    assert runs[0].status == "queued"
    scope = _scope_event(session, run_id).payload_json
    assert scope["automatic_protocol"] == {
        "generated_by": "system",
        "research_question": f"{raw_input} 的关键变化是什么？",
        "factors": ["需求变化", "供给约束", "替代解释"],
        "conclusion_rule": "report support, contradiction, and insufficiency separately",
    }
    assert len(scope["automatic_evidence_plan"]["items"]) == 3


def test_pasted_material_creates_queued_run_without_human_preparation(session) -> None:
    raw_input = (
        "公司披露最新产能建设进度低于原计划，同时下游客户的订单节奏发生变化。"
    )

    started = AutomaticResearchIntakeService(
        session, extractor=FakeExtractor()
    ).start(raw_input, tenant_id="material-team")

    case_id = uuid.UUID(started.case_id)
    run_id = uuid.UUID(started.run_id)
    run = session.get(ResearchRun, run_id)
    preparations = list(
        session.scalars(
            select(ResearchPreparation).where(
                ResearchPreparation.research_case_id == case_id
            )
        )
    )
    assert run is not None
    assert run.research_case_id == case_id
    assert run.status == "queued"
    assert started.preparation_id is None
    assert preparations == []


def test_blank_input_is_rejected_before_extraction(session) -> None:
    extractor = FakeExtractor()

    with pytest.raises(ValueError, match="automatic research input must not be blank"):
        AutomaticResearchIntakeService(session, extractor=extractor).start(
            "  \n\t ", tenant_id="research-team"
        )

    assert extractor.calls == []
