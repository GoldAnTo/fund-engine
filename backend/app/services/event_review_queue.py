"""Event-scoped evidence-review queue and invalid-source reconciliation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import EventResearchFactorDraft
from app.models.events import DomainEvent
from app.models.operational import EventResearchLifecycle
from app.models.proposals import Proposal
from app.queries.review_queue import ProposalEvidenceContext, proposal_evidence_context
from app.repositories.operational import TaskRepository
from app.repositories.outbox import emit_event
from app.schemas.v1.event_research import (
    EventReviewQueueItemDTO,
    EventReviewQueueResponse,
    EventReviewQueueSummaryDTO,
)
from app.services.source_admission import SourceStatus


@dataclass(frozen=True)
class EventReviewQueueReconciliation:
    invalid_source_proposal_ids: list[uuid.UUID]


class EventReviewQueueService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def review_queue(self, case_id: uuid.UUID) -> EventReviewQueueResponse:
        proposals = list(
            self._session.scalars(
                select(Proposal)
                .where(Proposal.research_case_id == case_id)
                .where(Proposal.kind == "evidence_link")
                .order_by(Proposal.proposed_at, Proposal.id)
            )
        )
        contexts = [
            proposal_evidence_context(self._session, proposal) for proposal in proposals
        ]
        pending_contexts = [
            context for context in contexts if context.proposal.status == "pending"
        ]
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        return EventReviewQueueResponse(
            items=[self._item(case_id, context) for context in pending_contexts],
            summary=EventReviewQueueSummaryDTO(
                total=len(contexts),
                reviewed=sum(context.proposal.status == "decided" for context in contexts),
                pending=sum(
                    context.admission.can_accept for context in pending_contexts
                ),
                invalid_source=sum(
                    context.admission.status == SourceStatus.INVALID
                    for context in pending_contexts
                ),
                current_round=lifecycle.current_round if lifecycle else 0,
                next_action=lifecycle.next_human_action if lifecycle else None,
            ),
        )

    def reconcile_event_review_queue(
        self, case_id: uuid.UUID
    ) -> EventReviewQueueReconciliation:
        proposals = self._session.scalars(
            select(Proposal)
            .where(Proposal.research_case_id == case_id)
            .where(Proposal.kind == "evidence_link")
            .where(Proposal.status == "pending")
            .order_by(Proposal.proposed_at, Proposal.id)
        )
        invalid_ids: list[uuid.UUID] = []
        task_repo = TaskRepository(self._session)
        for proposal in proposals:
            context = proposal_evidence_context(self._session, proposal)
            if context.admission.status != SourceStatus.INVALID:
                continue
            invalid_ids.append(proposal.id)
            task_repo.close_review_task("review_proposal", "proposal", proposal.id)
            if not self._has_admission_audit(proposal.id):
                emit_event(
                    self._session,
                    type="event_evidence_source_invalid",
                    aggregate_type="proposal",
                    aggregate_id=proposal.id,
                    ref_type="research_case",
                    ref_id=case_id,
                    origin="operational",
                    payload={
                        "research_case_id": str(case_id),
                        "source_status": str(context.admission.status),
                        "admission_reason": context.admission.reason,
                        "can_accept": context.admission.can_accept,
                    },
                )
        return EventReviewQueueReconciliation(invalid_source_proposal_ids=invalid_ids)

    def _item(
        self, case_id: uuid.UUID, context: ProposalEvidenceContext
    ) -> EventReviewQueueItemDTO:
        proposal, thesis, statement, span, document = (
            context.proposal,
            context.thesis,
            context.statement,
            context.span,
            context.document,
        )
        payload = proposal.payload if isinstance(proposal.payload, dict) else {}
        return EventReviewQueueItemDTO(
            proposal_id=str(proposal.id),
            status=proposal.status,
            proposed_at=proposal.proposed_at,
            link_id=str(proposal.id),
            thesis_id=str(thesis.id) if thesis else None,
            case_id=str(case_id),
            thesis_statement=thesis.statement if thesis else None,
            ai_role=payload.get("role", ""),
            ai_reason=payload.get("reason", ""),
            ai_scope=payload.get("scope", {}),
            statement_id=str(statement.id) if statement else None,
            statement_text=statement.normalized_text if statement else None,
            statement_kind=statement.kind if statement else None,
            span_id=str(span.id) if span else None,
            verbatim_text=span.verbatim_text if span else None,
            locator=span.locator if span else {},
            document_version_id=str(document.id) if document else None,
            document_source_url=document.source_url if document else None,
            document_published_at=document.published_at if document else None,
            available_at=document.available_at if document else proposal.basis_cutoff,
            source_title=document.title if document else None,
            source_status=str(context.admission.status),
            source_status_reason=context.admission.reason,
            can_accept=context.admission.can_accept,
            proposal_reason=payload.get("reason", ""),
            position=self._factor_position(case_id, thesis.statement if thesis else None),
        )

    def _factor_position(
        self, case_id: uuid.UUID, statement: str | None
    ) -> int | None:
        if statement is None:
            return None
        return self._session.scalar(
            select(EventResearchFactorDraft.position)
            .where(EventResearchFactorDraft.research_case_id == case_id)
            .where(EventResearchFactorDraft.statement == statement)
            .limit(1)
        )

    def _has_admission_audit(self, proposal_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(DomainEvent.id)
            .where(DomainEvent.type == "event_evidence_source_invalid")
            .where(DomainEvent.aggregate_type == "proposal")
            .where(DomainEvent.aggregate_id == str(proposal_id))
            .limit(1)
        ) is not None
