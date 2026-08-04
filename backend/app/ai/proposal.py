"""EvidenceProposer: LLM-driven proposal of EvidenceLinks.

Candidate SourceStatements are recalled per Thesis through ``RecallService`` —
cutoff-visible, relevance-ranked, and excluding already-linked statements — then
the LLM judges the evidence relationship (supports / contradicts /
contextualizes) for each recalled statement.

DESIGN CONTRACT (§5.3 / §9.2): the proposer NO LONGER writes a reviewed
``EvidenceLink`` directly.  It appends a ``Proposal(kind=evidence_link)`` and a
``DomainEvent``; the link only becomes a formal, reviewed relation after a human
``ProposalReviewDecision`` publishes an ``EvidenceLinkVersion`` via the
ProposalReview service.  This guarantees an AI proposal can never cross the
human-review boundary on its own.

Every proposal operation writes exactly one ``AIRun`` audit record
(``kind=propose``).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.prompts import PROPOSE_PROMPT_VERSION, PROPOSE_SYSTEM
from app.ai.runs import record_run
from app.models.ledger import ResearchCase, Thesis
from app.repositories.outbox import emit_event
from app.repositories.research import ResearchRepository
from app.services.compliance import evaluate_compliance
from app.services.proposals import ProposalService
from app.services.research import ResearchService


class EvidenceProposer:
    """Proposes machine-generated evidence_link Proposals for a Thesis."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def propose(
        self, thesis_id: uuid.UUID, session: Session
    ) -> list[uuid.UUID]:
        started_at = datetime.now(timezone.utc)
        research = ResearchService(ResearchRepository(session))
        proposals = ProposalService(session)

        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ValueError(f"thesis {thesis_id} not found")

        # Retrieval-scoped recall: only statements visible at the proposal
        # cutoff and relevant to this thesis reach the LLM.
        cutoff = started_at
        statements = RecallService(session).for_thesis(thesis, cutoff=cutoff)

        input_ref = {
            "thesis_id": str(thesis_id),
            "cutoff": cutoff.isoformat(),
            "statement_ids": [str(s.id) for s in statements],
        }

        if not statements:
            record_run(
                session,
                kind="propose",
                model_version=self._client.model_version,
                prompt_version=PROPOSE_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="no relevant statements recalled",
                status="success",
                started_at=started_at,
            )
            return []

        user_data = {
            "thesis": thesis.statement,
            "statements": [
                {"id": str(s.id), "kind": s.kind, "text": s.normalized_text}
                for s in statements
            ],
        }
        messages = [
            {"role": "system", "content": PROPOSE_SYSTEM},
            {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
        ]

        created_ids: list[uuid.UUID] = []
        try:
            result = self._client.chat_json(messages, schema_hint="propose")
            links_data = result.get("links", [])

            stmt_map = {str(s.id): s for s in statements}
            case = session.get(ResearchCase, thesis.research_case_id)
            derived_scope = (
                {"industry_topic": case.industry_topic}
                if case is not None and case.industry_topic
                else {}
            )
            seen_statement_ids: set[str] = set()
            refused = 0
            for link_data in links_data:
                stmt_id = link_data.get("source_statement_id", "")
                statement = stmt_map.get(stmt_id)
                if statement is None or stmt_id in seen_statement_ids:
                    continue
                # Non-investment-advice gate: a link whose rationale crosses
                # the boundary is skipped, never proposed.
                if evaluate_compliance(str(link_data.get("reason", ""))).is_hit:
                    refused += 1
                    continue
                scope = link_data.get("scope") or derived_scope
                if not scope:
                    continue
                seen_statement_ids.add(stmt_id)
                proposal = proposals.create_proposal(
                    kind="evidence_link",
                    payload={
                        "source_statement_id": str(statement.id),
                        "role": link_data["role"],
                        "reason": link_data["reason"],
                        "scope": scope,
                    },
                    target_context={
                        "thesis_id": str(thesis.id),
                        "entity_type": "evidence_link",
                    },
                    proposed_by_type="ai",
                    proposed_by_ref=self._client.model_version,
                    basis_cutoff=cutoff,
                    input_entity_ids=[str(statement.id)],
                    research_case_id=thesis.research_case_id,
                    actor=f"ai:{self._client.model_version}",
                )
                created_ids.append(proposal.id)
                emit_event(
                    session,
                    type="evidence_link_proposed",
                    aggregate_type="evidence_link",
                    aggregate_id=str(proposal.id),
                    ref_type="proposal",
                    ref_id=proposal.id,
                    payload={"thesis_id": str(thesis.id), "role": link_data["role"]},
                    origin="ledger",
                    actor=f"ai:{self._client.model_version}",
                )

            summary = f"proposed {len(created_ids)} evidence links"
            if refused:
                summary += f" ({refused} refused by compliance)"
            record_run(
                session,
                kind="propose",
                model_version=self._client.model_version,
                prompt_version=PROPOSE_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=summary,
                status="success",
                started_at=started_at,
            )
            return created_ids

        except Exception as exc:
            record_run(
                session,
                kind="propose",
                model_version=self._client.model_version,
                prompt_version=PROPOSE_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="",
                status="failed",
                error=str(exc),
                started_at=started_at,
            )
            raise


def RecallService(session: Session):  # local import helper to avoid cycle
    from app.services.recall import RecallService as _RS

    return _RS(session)
