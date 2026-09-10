"""Repository for the unified Proposal + ProposalReviewDecision tables."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.proposals import (
    Proposal,
    ProposalReviewDecision,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProposalRepository:
    """Append/read access for proposals and their review decisions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ writes
    def add_proposal(
        self,
        *,
        kind: str,
        payload: dict,
        target_context: dict,
        proposed_by_type: str,
        proposed_by_ref: str,
        basis_cutoff: datetime | None = None,
        input_entity_ids: list[str] | None = None,
        content_hash: str | None = None,
        ai_run_id: uuid.UUID | None = None,
        research_case_id: uuid.UUID | None = None,
    ) -> Proposal:
        proposal = Proposal(
            kind=kind,
            payload=payload,
            target_context=target_context,
            proposed_by_type=proposed_by_type,
            proposed_by_ref=proposed_by_ref,
            proposed_at=_utcnow(),
            basis_cutoff=basis_cutoff,
            input_entity_ids=input_entity_ids,
            content_hash=content_hash,
            ai_run_id=ai_run_id,
            research_case_id=research_case_id,
        )
        self._session.add(proposal)
        self._session.flush()
        return proposal

    def get_proposal(self, proposal_id: uuid.UUID) -> Proposal | None:
        return self._session.get(Proposal, proposal_id)

    def add_decision(
        self,
        *,
        proposal_id: uuid.UUID,
        outcome: str,
        reason: str,
        reviewer_id: str,
        expected_proposal_version: int,
        replacement_payload: dict | None = None,
    ) -> ProposalReviewDecision:
        decision = ProposalReviewDecision(
            proposal_id=proposal_id,
            outcome=outcome,
            reason=reason,
            reviewer_id=reviewer_id,
            decided_at=_utcnow(),
            expected_proposal_version=expected_proposal_version,
            replacement_payload=replacement_payload,
        )
        self._session.add(decision)
        self._session.flush()
        return decision

    def latest_decision(self, proposal_id: uuid.UUID) -> ProposalReviewDecision | None:
        return self._session.scalar(
            select(ProposalReviewDecision)
            .where(ProposalReviewDecision.proposal_id == proposal_id)
            .order_by(ProposalReviewDecision.decided_at.desc())
            .limit(1)
        )

    def pending_for_case(
        self, *, case_id: uuid.UUID | None = None, kind: str | None = None, limit: int = 50
    ) -> list[Proposal]:
        query = select(Proposal).where(Proposal.status == "pending")
        if case_id is not None:
            query = query.where(Proposal.research_case_id == case_id)
        if kind is not None:
            query = query.where(Proposal.kind == kind)
        query = query.order_by(Proposal.proposed_at).limit(limit)
        return list(self._session.scalars(query))

    def pending_page(
        self,
        *,
        after_proposed_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
        case_id: uuid.UUID | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Proposal]:
        """Cursor-paginated pending proposals (opaque cursor = proposed_at+id)."""
        query = select(Proposal).where(Proposal.status == "pending")
        if case_id is not None:
            query = query.where(Proposal.research_case_id == case_id)
        if kind is not None:
            query = query.where(Proposal.kind == kind)
        if after_proposed_at is not None and after_id is not None:
            from sqlalchemy import tuple_

            query = query.where(
                tuple_(Proposal.proposed_at, Proposal.id)
                < tuple_(after_proposed_at, after_id)
            )
        query = query.order_by(Proposal.proposed_at.desc(), Proposal.id.desc())
        return list(self._session.scalars(query.limit(limit + 1)))
