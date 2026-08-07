"""Creation service for independent, automatically-starting event research."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.event_research import EventResearchBrief, EventResearchFactorDraft
from app.models.operational import EventResearchLifecycle
from app.repositories.research import ResearchRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.auto_research import AutoResearchService
from app.services.research import ResearchService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CreatedEventResearch:
    case_id: str
    brief_id: str
    lifecycle: EventResearchLifecycle


class EventResearchService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, payload: CreateEventResearchRequest) -> CreatedEventResearch:
        research = ResearchService(ResearchRepository(self._session))
        case = research.add_case(
            title=payload.event_title,
            industry_topic="事件研究",
            created_by=payload.created_by,
            research_object=payload.company_name or payload.event_title,
            phenomenon=payload.market_reaction,
            core_question=payload.research_question,
            evidence_cutoff=payload.event_at.date() if payload.event_at else None,
        )
        now = _utcnow()
        brief = EventResearchBrief(
            research_case_id=case.id,
            raw_input=payload.raw_input,
            source_url=payload.source_url,
            event_title=payload.event_title,
            company_name=payload.company_name,
            ticker=payload.ticker,
            event_at=payload.event_at,
            market_reaction=payload.market_reaction,
            research_question=payload.research_question,
            extraction_state="human_confirmed",
            created_at=now,
        )
        self._session.add(brief)
        for position, factor in enumerate(payload.candidate_factors, start=1):
            statement = factor.strip()
            self._session.add(
                EventResearchFactorDraft(
                    research_case_id=case.id,
                    statement=statement,
                    position=position,
                    created_by="human",
                    created_at=now,
                )
            )
            research.add_thesis(
                case.id,
                statement=statement,
                created_by=payload.created_by,
                creator_type="human",
                review_state="confirmed",
            )
        self._session.flush()

        # This queues one durable worker job.  The request never waits for
        # collection or model calls, but the workbench immediately tells the
        # user that the system has started research.
        run = AutoResearchService(self._session).start(
            case.id, max_rounds=3, budget=100, commit=False
        )
        lifecycle = EventResearchLifecycle(
            research_case_id=case.id,
            status="researching",
            active_run_id=run.id,
            current_round=1,
            status_summary="正在建立第一轮证据检索",
            current_gap=None,
            next_human_action=None,
            updated_at=_utcnow(),
        )
        self._session.add(lifecycle)
        self._session.commit()
        return CreatedEventResearch(
            case_id=str(case.id), brief_id=str(brief.id), lifecycle=lifecycle
        )
