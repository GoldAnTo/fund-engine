"""One-input entry point for automatic event research."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.event_extraction import EventExtraction, EventExtractionService
from app.services.event_research import EventResearchService


class _EventExtractor(Protocol):
    def extract(
        self, *, raw_input: str, source_url: str | None
    ) -> EventExtraction: ...


@dataclass(frozen=True, slots=True)
class AutomaticResearchStart:
    case_id: str
    run_id: str
    preparation_id: None = None


class AutomaticResearchIntakeService:
    def __init__(
        self, session: Session, extractor: _EventExtractor | None = None
    ) -> None:
        self._session = session
        self._extractor = extractor or EventExtractionService()

    def start(self, raw_input: str, *, tenant_id: str) -> AutomaticResearchStart:
        text = raw_input.strip()
        if not text:
            raise ValueError("automatic research input must not be blank")
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("automatic research tenant_id must not be blank")
        if len(normalized_tenant_id) > 121:
            raise ValueError(
                "automatic research tenant_id must not exceed 121 characters"
            )

        extracted = self._extractor.extract(raw_input=text, source_url=None)
        event_title = (
            extracted.event_title.strip()
            if extracted.event_title and extracted.event_title.strip()
            else text[:80]
        )
        created = EventResearchService(self._session).create(
            CreateEventResearchRequest(
                raw_input=text,
                source_url=None,
                source_type="pasted_snapshot",
                source_metadata={
                    "authority_level": "user_supplied",
                    "intake_role": "research_prompt",
                    "permissions": {
                        "ai_processing": True,
                        "display": True,
                        "export": False,
                        "api": False,
                    },
                },
                event_title=event_title,
                company_name=extracted.company_name,
                ticker=extracted.ticker,
                event_at=extracted.event_at,
                market_reaction=extracted.market_reaction,
                research_question=extracted.research_question,
                candidate_factors=list(extracted.candidate_factors),
                research_protocol_required=False,
                created_by=f"tenant:{normalized_tenant_id}",
            ),
            tenant_id=normalized_tenant_id,
            workflow_mode="automatic",
        )
        if created.run_id is None:
            raise RuntimeError("automatic research intake did not create a run")
        return AutomaticResearchStart(
            case_id=created.case_id,
            run_id=created.run_id,
        )
