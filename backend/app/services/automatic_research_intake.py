"""One-input entry point for automatic event research."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from unicodedata import category

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


def _contains_control_characters(value: str) -> bool:
    return any(category(character) == "Cc" for character in value)


class AutomaticResearchIntakeService:
    def __init__(
        self, session: Session, extractor: _EventExtractor | None = None
    ) -> None:
        self._session = session
        self._extractor = extractor or EventExtractionService()

    def start(
        self,
        raw_input: str,
        *,
        tenant_id: str,
        actor_subject_id: str | None,
        commit: bool = True,
    ) -> AutomaticResearchStart:
        text = raw_input.strip()
        if not text:
            raise ValueError("automatic research input must not be blank")
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("automatic research tenant_id must not be blank")
        if _contains_control_characters(normalized_tenant_id):
            raise ValueError(
                "automatic research tenant_id must not contain control characters"
            )
        if len(normalized_tenant_id) > 121:
            raise ValueError(
                "automatic research tenant_id must not exceed 121 characters"
            )
        if actor_subject_id is None:
            audit_actor = f"tenant:{normalized_tenant_id}"
        else:
            if not actor_subject_id.strip():
                raise ValueError(
                    "automatic research actor_subject_id must not be blank"
                )
            if _contains_control_characters(actor_subject_id):
                raise ValueError(
                    "automatic research actor_subject_id must not contain "
                    "control characters"
                )
            if len(actor_subject_id) > 128:
                raise ValueError(
                    "automatic research actor_subject_id must not exceed 128 characters"
                )
            audit_actor = f"human:{actor_subject_id}"

        extracted = self._extractor.extract(raw_input=text, source_url=None)
        input_kind = (
            extracted.input_kind
            if extracted.input_kind in {"topic", "material"}
            else "topic"
        )
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
                    "intake_role": (
                        "provided_material"
                        if input_kind == "material"
                        else "research_prompt"
                    ),
                    "input_kind": input_kind,
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
                created_by=audit_actor,
            ),
            tenant_id=normalized_tenant_id,
            workflow_mode="automatic",
            commit=commit,
        )
        if created.run_id is None:
            raise RuntimeError("automatic research intake did not create a run")
        return AutomaticResearchStart(
            case_id=created.case_id,
            run_id=created.run_id,
        )
