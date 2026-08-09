"""Proposal publisher (design §9.2).

On a ``confirmed`` / ``modified`` ReviewDecision, appends the formal, reviewed
domain entity carrying the proposal_id + decision_id for provenance.  This is
the ONLY path that turns an AI proposal into a reviewed relation — the AI
worker never writes the formal row itself.

First version handles ``evidence_link`` (the prototype's primary reviewed
relation).  ``statement`` / ``causal_edge`` / ``entity_alignment`` publishers
follow the same shape as their extract/propose steps land.

For the transition window we ALSO write the legacy ``evidence_links`` row so
existing read paths (graph / dossier / workbench) keep working; the
``evidence_link_versions`` table is the durable, versioned source and read
paths will flip to it in a later step.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import EvidenceLink, Thesis
from app.models.proposals import Proposal, ProposalReviewDecision
from app.models.versions import EvidenceLinkVersion
from app.repositories.outbox import emit_event
from app.repositories.proposals import ProposalRepository
from app.repositories.research import ResearchRepository
from app.services.event_research_scope_evidence import (
    append_current_scope_evidence_assignment,
)


def final_evidence_payload(
    proposal: Proposal,
    outcome: str,
    replacement_payload: dict | None,
) -> dict:
    """Return the payload that will be published for an evidence proposal."""
    if outcome == "modified" and replacement_payload:
        return replacement_payload
    return proposal.payload


class ProposalPublisher:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ProposalRepository(session)
        self._research = ResearchRepository(session)

    def publish(self, decision: ProposalReviewDecision) -> object | None:
        """Publish the formal entity for a decided proposal, if applicable.

        Returns the created formal entity (e.g. EvidenceLinkVersion) or None
        for ``rejected`` / non-publishing kinds.
        """
        proposal = self._repo.get_proposal(decision.proposal_id)
        if proposal is None:
            return None
        if decision.outcome == "rejected":
            # No formal entity; the rejection event is already emitted by the
            # decision service.  Emit the link-rejected event for projections.
            emit_event(
                self._session,
                type="evidence_link_rejected",
                aggregate_type="evidence_link",
                aggregate_id=str(proposal.id),
                ref_type="proposal",
                ref_id=proposal.id,
                payload={"proposal_id": str(proposal.id)},
                origin="ledger",
                actor=decision.reviewer_id,
            )
            return None

        if decision.outcome == "needs_more_evidence":
            # The reviewer explicitly requested a further collection round;
            # the proposal remains auditable but cannot become formal evidence.
            return None

        if proposal.kind == "evidence_link":
            return self._publish_evidence_link(proposal, decision)
        # Other kinds: recorded but not yet auto-published (returns None until
        # their publishers land).  The decision still stands.
        return None

    def _publish_evidence_link(
        self, proposal: Proposal, decision: ProposalReviewDecision
    ) -> EvidenceLinkVersion:
        payload = final_evidence_payload(
            proposal, decision.outcome, decision.replacement_payload
        )

        thesis_id = uuid.UUID(proposal.target_context["thesis_id"])
        statement_id = uuid.UUID(payload["source_statement_id"])
        now = datetime.now(timezone.utc)
        thesis = self._session.get(Thesis, thesis_id)
        model_version = (
            proposal.proposed_by_ref if proposal.proposed_by_type == "ai" else None
        )

        # Transition-window compatibility: also write the legacy row so the
        # existing graph/dossier read paths resolve the reviewed link.  The
        # row is also the parent identity required by EvidenceLinkVersion.
        legacy = EvidenceLink(
            thesis_id=thesis_id,
            source_statement_id=statement_id,
            role=payload["role"],
            reason=payload["reason"],
            scope=payload.get("scope", {}),
            available_at=now,
            creator_type="human",
            review_state="reviewed",
            model_version=model_version,
            created_at=now,
        )
        self._session.add(legacy)
        self._session.flush()
        if thesis is not None:
            # The public assignment helper takes the global case -> lifecycle
            # lock once, which also supports direct non-proposal callers.
            append_current_scope_evidence_assignment(
                self._session,
                case_id=thesis.research_case_id,
                evidence_link_id=legacy.id,
                factor_statement=thesis.statement,
                created_at=now,
            )

        # Durable versioned edge.  Persist the parent row first: PostgreSQL
        # enforces this FK (unlike SQLite's default test configuration).
        version = EvidenceLinkVersion(
            evidence_link_id=legacy.id,
            version=1,
            thesis_id=thesis_id,
            source_statement_id=statement_id,
            role=payload["role"],
            reason=payload["reason"],
            scope=payload.get("scope", {}),
            available_at=now,
            proposal_id=proposal.id,
            review_decision_id=decision.id,
            model_version=model_version,
            created_at=now,
        )
        self._session.add(version)
        self._session.flush()

        emit_event(
            self._session,
            type="evidence_link_published",
            aggregate_type="evidence_link",
            aggregate_id=str(version.evidence_link_id),
            ref_type="proposal",
            ref_id=proposal.id,
            payload={
                "thesis_id": str(thesis_id),
                "statement_id": str(statement_id),
                "role": payload["role"],
            },
            origin="ledger",
            actor=decision.reviewer_id,
        )
        return version
