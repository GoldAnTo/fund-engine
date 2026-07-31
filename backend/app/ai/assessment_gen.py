"""AssessmentGenerator: LLM-driven AIAssessment from a frozen evidence snapshot.

Freezes an EvidenceSnapshot at the given cutoff, gathers the visible
EvidenceLinks with their statement texts, asks the LLM to produce a
three-valued conclusion (supported / contradicted / insufficient_evidence),
and writes the assessment through ``AssessmentService.create_ai_assessment``
with ``displayed_as_provisional=True``.

Every assessment operation writes exactly one ``AIRun`` audit record
(``kind=assess``).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.prompts import ASSESS_PROMPT_VERSION, ASSESS_SYSTEM
from app.ai.runs import record_run
from app.models.ledger import (
    AIAssessment,
    EvidenceLink,
    EvidenceSnapshot,
    SourceStatement,
    Thesis,
)
from app.repositories.research import ResearchRepository
from app.services.assessment import AssessmentService


class AssessmentGenerator:
    """Generates a provisional AIAssessment for a Thesis at a cutoff."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def generate(
        self,
        thesis_id: uuid.UUID,
        cutoff: datetime,
        session: Session,
    ) -> AIAssessment:
        started_at = datetime.now(timezone.utc)
        assessment_service = AssessmentService(ResearchRepository(session))

        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ValueError(f"thesis {thesis_id} not found")

        input_ref = {
            "thesis_id": str(thesis_id),
            "cutoff": cutoff.isoformat(),
        }

        try:
            snapshot = assessment_service.freeze_snapshot(
                thesis_id, cutoff=cutoff
            )

            link_ids = snapshot.evidence_link_ids
            links_data: list[dict] = []
            for link_id_str in link_ids:
                link = session.get(EvidenceLink, uuid.UUID(link_id_str))
                if link is None:
                    continue
                statement = session.get(SourceStatement, link.source_statement_id)
                links_data.append(
                    {
                        "role": link.role,
                        "reason": link.reason,
                        "statement_text": statement.normalized_text
                        if statement
                        else "",
                    }
                )

            user_data = {
                "thesis": thesis.statement,
                "links": links_data,
            }
            messages = [
                {"role": "system", "content": ASSESS_SYSTEM},
                {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
            ]

            result = self._client.chat_json(messages, schema_hint="assess")
            conclusion = result["conclusion"]
            rationale = result["rationale"]
            gaps = result.get("gaps", [])

            assessment = assessment_service.create_ai_assessment(
                snapshot.id,
                conclusion=conclusion,
                rationale=rationale,
                gaps=gaps,
            )

            input_ref["snapshot_id"] = str(snapshot.id)
            input_ref["link_count"] = len(link_ids)
            record_run(
                session,
                kind="assess",
                model_version=self._client.model_version,
                prompt_version=ASSESS_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=f"conclusion={conclusion}, links={len(link_ids)}",
                status="success",
                started_at=started_at,
            )
            return assessment

        except Exception as exc:
            record_run(
                session,
                kind="assess",
                model_version=self._client.model_version,
                prompt_version=ASSESS_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="",
                status="failed",
                error=str(exc),
                started_at=started_at,
            )
            raise
