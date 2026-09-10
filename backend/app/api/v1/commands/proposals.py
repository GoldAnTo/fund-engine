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

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import (
    commit_or_rollback,
    translate_validation,
)
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.errors import ConflictError, NotFoundError
from app.models.ledger import ValidationError
from app.queries.review_queue import proposal_evidence_context
from app.repositories.operational import ReviewAssignmentRepository, TaskRepository
from app.repositories.proposals import ProposalRepository
from app.services.auto_research import AutoResearchService
from app.schemas.v1.operational import (
    ClaimResponse,
    ProposalItemDTO,
    ReviewDecisionDTO,
    ReviewDecisionRequest,
    ReviewQueueResponse,
)
from app.services.proposal_publisher import ProposalPublisher, final_evidence_payload
from app.services.proposals import ProposalService, ReviewConflictError
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(tags=["proposal-review-commands-v1"])


@router.get("/review-proposals", response_model=ReviewQueueResponse)
def list_proposals(
    case_policy: RequireCaseRoute,
    kind: str | None = Query(default=None),
    case_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        case_policy.require(case_id)
    proposals = ProposalRepository(db).pending_for_case(
        case_id=case_id,
        kind=kind,
        limit=limit,
        authorized_case_ids=case_policy.authorized_case_ids(),
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
    case_policy: RequireCaseRoute,
    proposal_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    proposal = ProposalRepository(db).get_proposal(proposal_id)
    if proposal is None or proposal.research_case_id is None:
        raise NotFoundError(f"pending proposal {proposal_id} not found")
    case_policy.require(proposal.research_case_id)
    assignment = translate_validation(
        _claim,
        proposal_id,
        db,
        actor.server_actor,
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


def _claim(proposal_id: uuid.UUID, db: Session, actor: str):
    from app.models.proposals import Proposal

    proposal = db.get(Proposal, proposal_id)
    if proposal is None or proposal.status != "pending":
        raise NotFoundError(f"pending proposal {proposal_id} not found")
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
    case_policy: RequireCaseRoute,
    proposal_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    proposal = ProposalRepository(db).get_proposal(proposal_id)
    if proposal is None or proposal.research_case_id is None:
        raise NotFoundError(f"pending proposal {proposal_id} not found")
    CaseTenantAccess(db).require_case(proposal.research_case_id, actor.tenant_id)
    case_policy.require(proposal.research_case_id)
    decision, published_id = translate_validation(
        _decide,
        proposal_id,
        payload,
        db,
        actor.server_actor,
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
    proposal_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    db: Session,
    reviewer_id: str,
):
    proposal = ProposalRepository(db).get_proposal(proposal_id)
    final_payload = (
        final_evidence_payload(
            proposal, payload.outcome, payload.replacement_payload
        )
        if proposal is not None
        else None
    )
    if (
        proposal is not None
        and proposal.research_case_id is not None
        and proposal.kind == "evidence_link"
        and payload.outcome in {"confirmed", "modified"}
        and not proposal_evidence_context(
            db,
            proposal,
            evidence_payload=final_payload,
        ).admission.can_accept
    ):
        raise ValidationError(
            "event evidence source cannot be accepted for formal publication"
        )
    try:
        decision = ProposalService(db).decide(
            proposal_id=proposal_id,
            outcome=payload.outcome,
            reason=payload.reason,
            reviewer_id=reviewer_id,
            expected_proposal_version=payload.expected_version,
            replacement_payload=payload.replacement_payload,
            actor=reviewer_id,
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

    TaskRepository(db).close_review_task(
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=proposal_id,
    )
    AutoResearchService(db).reconcile_runs_for_output(
        key="proposed_proposal_ids",
        value=proposal_id,
        trigger_ref=f"proposal:{proposal_id}",
    )
    if proposal is not None:
        raw_thesis_id = (proposal.target_context or {}).get("thesis_id")
        try:
            thesis_id = uuid.UUID(str(raw_thesis_id))
        except (TypeError, ValueError):
            thesis_id = None
        if thesis_id is not None:
            from app.models.ledger import Thesis

            thesis = db.get(Thesis, thesis_id)
            if thesis is not None:
                AutoResearchService(db).continue_after_key_review(thesis.research_case_id)
    return decision, published_id
