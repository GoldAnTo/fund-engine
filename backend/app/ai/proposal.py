"""EvidenceProposer: LLM-driven proposal of EvidenceLinks.

Candidate SourceStatements are recalled per Thesis through
``RecallService`` — cutoff-visible, relevance-ranked, and excluding already
linked statements — then the LLM judges the evidence relationship
(supports / contradicts / contextualizes) for each recalled statement.
Links are written through ``ResearchService.link_evidence`` with
``creator_type=ai`` and ``review_state=machine_generated``.

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
from app.models.ledger import EvidenceLink, ResearchCase, Thesis
from app.repositories.research import ResearchRepository
from app.services.recall import RecallService
from app.services.research import ResearchService


class EvidenceProposer:
    """Proposes machine-generated EvidenceLinks for a Thesis."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def propose(
        self, thesis_id: uuid.UUID, session: Session
    ) -> list[EvidenceLink]:
        started_at = datetime.now(timezone.utc)
        research = ResearchService(ResearchRepository(session))

        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ValueError(f"thesis {thesis_id} not found")

        # Retrieval-scoped recall: only statements visible at the proposal
        # cutoff and relevant to this thesis reach the LLM.  The previous
        # ``select(SourceStatement).limit(20)`` leaked unrelated cases and
        # future information into the prompt and re-proposed duplicates.
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

        try:
            result = self._client.chat_json(messages, schema_hint="propose")
            links_data = result.get("links", [])

            stmt_map = {str(s.id): s for s in statements}
            case = session.get(ResearchCase, thesis.research_case_id)
            # The LLM is required to return a scope; when it does not, derive
            # one from the case instead of fabricating a fixed segment.  A
            # link without any scope is skipped rather than admitted.
            derived_scope = (
                {"industry_topic": case.industry_topic}
                if case is not None and case.industry_topic
                else {}
            )
            created: list[EvidenceLink] = []
            seen_statement_ids: set[str] = set()
            for link_data in links_data:
                stmt_id = link_data.get("source_statement_id", "")
                statement = stmt_map.get(stmt_id)
                if statement is None or stmt_id in seen_statement_ids:
                    continue
                scope = link_data.get("scope") or derived_scope
                if not scope:
                    continue
                seen_statement_ids.add(stmt_id)
                link = research.link_evidence(
                    thesis_id,
                    statement.id,
                    role=link_data["role"],
                    reason=link_data["reason"],
                    scope=scope,
                )
                created.append(link)

            record_run(
                session,
                kind="propose",
                model_version=self._client.model_version,
                prompt_version=PROPOSE_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=f"proposed {len(created)} evidence links",
                status="success",
                started_at=started_at,
            )
            return created

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
