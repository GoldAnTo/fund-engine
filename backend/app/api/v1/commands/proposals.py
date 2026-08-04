"""Unified proposal review commands (design §5.3 / §8.5).

Exposes the human-resolution workflow for AI proposals: list the queue,
optionally claim a lease, and record a decision.  On ``confirmed`` /
``modified`` the decision service publishes the formal reviewed entity through
``ProposalPublisher`` (design §9.2) — the AI proposal never becomes a reviewed
relation on its own.

Decisions are guarded by ``expected_version`` for safe concurrent review, and
the whole endpoint is idempotency-key protected at the route layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import (
    commit_or_rollback,
    resolve_actor,
    translate_validation,
)
from app.db import get_db
from app.errors import ConflictError, NotFoundError
from app.models.proposals import ProposalReviewDecision
from app.queries.review_queue import ReviewQueueQueries
from app.repositories.operational import ReviewAssignmentRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.v1.operational import (
    ActivityItemDTO,
    ClaimResponse,
    JobDTO,
    ProposalItemDTO,
    ReviewDecisionDTO,
    ReviewDecisionRequest,
    ReviewQueueResponse,
)
from app.services.proposal_publisher import ProposalPublisher
from app.services.proposals import ProposalService, ReviewConflictError

router = APIRouter(tags=["proposal-review-commands-v1"])


@router.get("/review-proposals", response_model=ReviewQueueResponse)
def list_proposals(
    kind: str | None = Query(default=None),
    case_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    proposals = ProposalRepository(db).pending_for_case(
        case_id=case_id, kind=kind, limit=limit
    )
    items = [
        ProposalItemDTO(
            id=str(p.id),
            kind=p.kind,
            payload=p.payload,
            target_context=p.target_context,
            proposed_by_type=p.proposed_by_type,
            proposed_by_ref=p.proposed_by_ref,
            proposed_at=p.proposed_at.isoformat(),
            basis_cutoff=p.basis_cutoff.isoformat() if p.basis_cutoff else None,
            status=p.status,
            version=p.version,
        )
        for p in proposals
    ]
    return ReviewQueueResponse(items=items)


@router.post(
    "/review-proposals/{proposal_id}/claim",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
def claim_proposal(
    proposal_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    assignment = translate_validation(
        _claim,
        proposal_id,
        db,
    )
    commit_or_rollback(db)
    return ClaimResponse(
        proposal_id=str(assignment.proposal_id),
        assignee=assignment.assignee,
        claimed_at=assignment.claimed_at.isoformat(),
        lease_expires_at=(
            assignment.lease_expires_at.isoformat()
            if assignment.lease_expires_at
            else None
        ),
    )


def _claim(proposal_id: uuid.UUID, db: Session):
    from app.models.proposals import Proposal

    proposal = db.get(Proposal, proposal_id)
    if proposal is None or proposal.status != "pending":
        raise NotFoundError(f"pending proposal {proposal_id} not found")
    actor = resolve_actor_plain(proposal_id)
    return ReviewAssignmentRepository(db).claim(
        proposal_id=proposal_id,
        assignee=actor,
        lease_expires_at=None,
    )


@router.post(
    "/review-proposals/{proposal_id}/decisions",
    response_model=ReviewDecisionDTO,
    status_code=status.HTTP_201_CREATED,
)
def decide_proposal(
    proposal_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
):
    decision, published_id = translate_validation(
        _decide,
        proposal_id,
        payload,
        db,
    )
    commit_or_rollback(db)
    return ReviewDecisionDTO(
        id=str(decision.id),
        proposal_id=str(decision.proposal_id),
        outcome=decision.outcome,
        reason=decision.reason,
        reviewer_id=decision.reviewer_id,
        expected_proposal_version=decision.expected_proposal_version,
        decided_at=decision.decided_at.isoformat(),
        published_entity_id=published_id,
    )


def _decide(
    proposal_id: uuid.UUID, payload: ReviewDecisionRequest, db: Session
):
    actor = resolve_actor_plain(proposal_id)
    try:
        decision = ProposalService(db).decide(
            proposal_id=proposal_id,
            outcome=payload.outcome,
            reason=payload.reason,
            reviewer_id=payload.reviewer_id,
            expected_proposal_version=payload.expected_version,
            replacement_payload=payload.replacement_payload,
            actor=actor,
        )
    except ReviewConflictError as exc:
        raise ConflictError(str(exc)) from exc

    publisher = ProposalPublisher(db)
    published = publisher.publish(decision)
    published_id = (
        str(published.evidence_link_id)
        if published is not None and hasattr(published, "evidence_link_id")
        else None
    )
    return decision, published_id


def resolve_actor_plain(proposal_id: uuid.UUID) -> str:
    """Best-effort actor; review endpoints currently run without auth (the
    reviewer id is supplied by the client).  Once auth lands, this resolves
    from the principal instead."""
    return "human:anonymous"
