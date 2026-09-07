"""Event-scoped evidence-review queue and invalid-source reconciliation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import EventResearchFactorDraft
from app.models.events import DomainEvent
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    SourceSpan,
    SourceStatement,
    Thesis,
)
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
from app.services.source_admission import SourceStatus, classify_source
from app.services.event_research_scope_evidence import current_scope_thesis_ids


@dataclass(frozen=True)
class EventReviewQueueReconciliation:
    invalid_source_proposal_ids: list[uuid.UUID]


class EventReviewQueueService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def review_queue(self, case_id: uuid.UUID) -> EventReviewQueueResponse:
        active_thesis_ids = current_scope_thesis_ids(self._session, case_id)
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
            context
            for context in contexts
            if context.proposal.status == "pending"
            and (
                context.admission.status == SourceStatus.INVALID
                or self._is_current_scope_proposal(
                    context.proposal, active_thesis_ids
                )
            )
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

    def summary(self, case_id: uuid.UUID) -> EventReviewQueueSummaryDTO:
        """Return workbench counts without materializing full review-queue rows.

        Source provenance is resolved in bounded batches, keeping this path at
        a fixed number of queries regardless of pending-proposal volume.
        """
        proposals = list(
            self._session.scalars(
                select(Proposal)
                .where(Proposal.research_case_id == case_id)
                .where(Proposal.kind == "evidence_link")
            )
        )
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        active_thesis_ids = current_scope_thesis_ids(self._session, case_id)
        pending = [
            proposal
            for proposal in proposals
            if proposal.status == "pending"
        ]
        statement_ids = {
            statement_id
            for proposal in pending
            if (statement_id := _payload_uuid(proposal.payload, "source_statement_id"))
            is not None
        }
        thesis_ids = {
            thesis_id
            for proposal in pending
            if (thesis_id := _payload_uuid(proposal.target_context, "thesis_id"))
            is not None
        }
        statements = self._records_by_id(SourceStatement, statement_ids)
        theses = self._records_by_id(Thesis, thesis_ids)
        spans = self._records_by_id(
            SourceSpan,
            {statement.source_span_id for statement in statements.values()},
        )
        documents = self._records_by_id(
            DocumentVersion,
            {span.document_version_id for span in spans.values()},
        )
        document_ids = set(documents)
        linked_document_ids = set(
            self._session.scalars(
                select(CaseDocumentVersion.document_version_id)
                .where(CaseDocumentVersion.research_case_id == case_id)
                .where(CaseDocumentVersion.document_version_id.in_(document_ids))
            )
        ) if document_ids else set()

        admissible_pending = 0
        invalid_pending = 0
        for proposal in pending:
            statement = statements.get(
                _payload_uuid(proposal.payload, "source_statement_id")
            )
            thesis = theses.get(_payload_uuid(proposal.target_context, "thesis_id"))
            span = spans.get(statement.source_span_id) if statement else None
            document = documents.get(span.document_version_id) if span else None
            is_current_scope = self._is_current_scope_proposal(
                proposal, active_thesis_ids
            )
            if thesis is None or thesis.research_case_id != case_id:
                invalid_pending += 1
                continue
            if document is not None and document.id not in linked_document_ids:
                invalid_pending += 1
                continue
            admission = classify_source(
                document.source_url if document else None,
                document.parser_version if document else "",
                bool(document and document.parse_state in {"success", "parsed"}),
            )
            # Invalid/cross-case proposals must remain visible to reviewers
            # for provenance audit.  A valid proposal for a removed factor is
            # historical, so it no longer contributes any actionable count.
            if not is_current_scope and admission.status != SourceStatus.INVALID:
                continue
            if admission.can_accept:
                admissible_pending += 1
            if admission.status == SourceStatus.INVALID:
                invalid_pending += 1
        return EventReviewQueueSummaryDTO(
            total=len(proposals),
            reviewed=sum(proposal.status == "decided" for proposal in proposals),
            pending=admissible_pending,
            invalid_source=invalid_pending,
            current_round=lifecycle.current_round if lifecycle else 0,
            next_action=lifecycle.next_human_action if lifecycle else None,
        )

    def _records_by_id(self, model, ids: set[uuid.UUID]) -> dict:
        if not ids:
            return {}
        return {
            record.id: record
            for record in self._session.scalars(select(model).where(model.id.in_(ids)))
        }

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
        active_thesis_ids = current_scope_thesis_ids(self._session, case_id)
        task_repo = TaskRepository(self._session)
        for proposal in proposals:
            context = proposal_evidence_context(self._session, proposal)
            if context.admission.status == SourceStatus.INVALID:
                invalid_ids.append(proposal.id)
                task_repo.close_review_task(
                    "review_proposal", "proposal", proposal.id, research_case_id=case_id
                )
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
                continue
            if not self._is_current_scope_proposal(proposal, active_thesis_ids):
                task_repo.close_review_task(
                    "review_proposal", "proposal", proposal.id, research_case_id=case_id
                )
                if not self._has_out_of_scope_audit(proposal.id):
                    emit_event(
                        self._session,
                        type="event_evidence_out_of_scope",
                        aggregate_type="proposal",
                        aggregate_id=proposal.id,
                        ref_type="research_case",
                        ref_id=case_id,
                        origin="operational",
                        payload={
                            "research_case_id": str(case_id),
                            "reason": "proposal thesis is not active in the latest scope",
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
        payload = proposal.payload if isinstance(proposal.payload, dict) and not context.display_withheld else {}
        return EventReviewQueueItemDTO(
            proposal_id=str(proposal.id),
            proposal_version=proposal.version,
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
            display_withheld=context.display_withheld,
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

    def _has_out_of_scope_audit(self, proposal_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(DomainEvent.id)
            .where(DomainEvent.type == "event_evidence_out_of_scope")
            .where(DomainEvent.aggregate_type == "proposal")
            .where(DomainEvent.aggregate_id == str(proposal_id))
            .limit(1)
        ) is not None

    @staticmethod
    def _is_current_scope_proposal(
        proposal: Proposal, active_thesis_ids: set[uuid.UUID] | None
    ) -> bool:
        if active_thesis_ids is None:
            return True
        return _payload_uuid(proposal.target_context, "thesis_id") in active_thesis_ids


def _payload_uuid(payload: object, key: str) -> uuid.UUID | None:
    value = payload.get(key) if isinstance(payload, dict) else None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
