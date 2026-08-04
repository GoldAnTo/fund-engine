"""Proposal + unified review-decision service (design §5.3 / §6.2).

Owns the human-resolution flow for AI (or human) proposals of four kinds:
``statement``, ``evidence_link``, ``causal_edge``, ``entity_alignment``.

INVARIANT: an AI proposal never becomes a reviewed relation on its own.  The
publisher appends the formal, versioned entity only after a ``confirmed`` /
``modified`` decision, carrying both ids for provenance.  ``rejected`` keeps
the proposal + reason and emits no formal entity.

Concurrency: a decision must carry the ``expected_proposal_version`` the
reviewer loaded.  A mismatch → ``ReviewConflictError`` (409).  ``rejected``
also requires the expected version (you cannot reject a proposal someone else
already decided).  Double-deciding the same version is blocked by bumping
``Proposal.version`` on each decision and checking it atomically.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.models.ledger import ValidationError
from app.models.proposals import Proposal, ProposalReviewDecision
from app.repositories.outbox import emit_event
from app.repositories.proposals import ProposalRepository


class ReviewConflictError(Exception):
    """Raised when a decision collides with current proposal state (409)."""


def _content_hash(payload: dict, target_context: dict) -> str:
    blob = json.dumps(
        {"payload": payload, "target_context": target_context},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ProposalService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ProposalRepository(session)

    # ------------------------------------------------------------- create
    def create_proposal(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        target_context: dict[str, Any],
        proposed_by_type: str = "ai",
        proposed_by_ref: str = "ai:unknown",
        basis_cutoff: datetime | None = None,
        input_entity_ids: list[str] | None = None,
        ai_run_id: uuid.UUID | None = None,
        research_case_id: uuid.UUID | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> Proposal:
        if kind not in {"statement", "evidence_link", "causal_edge", "entity_alignment"}:
            raise ValidationError(f"invalid proposal kind: {kind}")
        content_hash = _content_hash(payload, target_context)
        proposal = self._repo.add_proposal(
            kind=kind,
            payload=payload,
            target_context=target_context,
            proposed_by_type=proposed_by_type,
            proposed_by_ref=proposed_by_ref,
            basis_cutoff=basis_cutoff,
            input_entity_ids=input_entity_ids,
            content_hash=content_hash,
            ai_run_id=ai_run_id,
            research_case_id=research_case_id,
        )
        emit_event(
            self._session,
            type="proposal_created",
            aggregate_type="proposal",
            aggregate_id=proposal.id,
            payload={
                "kind": kind,
                "research_case_id": (
                    str(research_case_id) if research_case_id else None
                ),
            },
            origin="operational",
            actor=actor,
            correlation_id=correlation_id,
        )
        return proposal

    # ------------------------------------------------------------- decide
    def decide(
        self,
        *,
        proposal_id: uuid.UUID,
        outcome: str,
        reason: str,
        reviewer_id: str,
        expected_proposal_version: int,
        replacement_payload: dict[str, Any] | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> ProposalReviewDecision:
        proposal = self._repo.get_proposal(proposal_id)
        if proposal is None:
            raise ConflictError(f"proposal {proposal_id} not found")
        if outcome not in {"confirmed", "modified", "rejected"}:
            raise ValidationError(f"invalid outcome: {outcome}")
        if not reason or not reason.strip():
            raise ValidationError("reason must not be empty")
        if outcome == "modified" and replacement_payload is None:
            raise ValidationError("modified outcome requires replacement_payload")
        if proposal.status != "pending":
            raise ReviewConflictError(
                f"proposal already decided (status={proposal.status})"
            )
        # Optimistic concurrency: the reviewer must have loaded the same version.
        if proposal.version != expected_proposal_version:
            raise ReviewConflictError(
                f"proposal version conflict: expected "
                f"{expected_proposal_version}, current {proposal.version}"
            )

        decision = self._repo.add_decision(
            proposal_id=proposal.id,
            outcome=outcome,
            reason=reason.strip(),
            reviewer_id=reviewer_id,
            expected_proposal_version=expected_proposal_version,
            replacement_payload=replacement_payload,
        )
        # Bump version + mark decided atomically with the decision insert
        # (same transaction).  A concurrent decider will then see a mismatched
        # expected_version and get a 409.
        proposal.version = expected_proposal_version + 1
        proposal.status = "decided"
        proposal.decided_at = datetime.now(timezone.utc)

        emit_event(
            self._session,
            type="proposal_decided",
            aggregate_type="proposal",
            aggregate_id=proposal.id,
            payload={
                "outcome": outcome,
                "decision_id": str(decision.id),
                "reviewed_entity": (
                    proposal.target_context.get("entity_type")
                    if isinstance(proposal.target_context, dict)
                    else None
                ),
            },
            origin="operational",
            ref_type="proposal_review_decision",
            ref_id=decision.id,
            actor=actor or reviewer_id,
            correlation_id=correlation_id,
        )
        return decision
