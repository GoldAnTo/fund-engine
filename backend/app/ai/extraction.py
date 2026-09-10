"""StatementExtractor: LLM-driven extraction of SourceStatements from spans.

Reads the evidence ledger (SourceSpans for a DocumentVersion), asks the LLM
to extract atomic statements from the verbatim text, and writes them through
``ResearchService.add_statement`` with full kind validation.

Every extraction operation writes exactly one ``AIRun`` audit record
(``kind=extract``) capturing the model/prompt versions, span IDs processed,
statement count, and success/failure status.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.prompts import EXTRACT_PROMPT_VERSION, EXTRACT_SYSTEM
from app.ai.runs import record_run
from app.models.ledger import SourceSpan, SourceStatement
from app.repositories.research import ResearchRepository
from app.services.research import ResearchService


class StatementExtractor:
    """Extracts atomic SourceStatements from a DocumentVersion's spans."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def extract(
        self, document_version_id: uuid.UUID, session: Session
    ) -> list[SourceStatement]:
        started_at = datetime.now(timezone.utc)
        research = ResearchService(ResearchRepository(session))

        spans = list(
            session.scalars(
                select(SourceSpan).where(
                    SourceSpan.document_version_id == document_version_id
                )
            )
        )

        span_ids = [str(span.id) for span in spans]
        input_ref = {
            "document_version_id": str(document_version_id),
            "span_ids": span_ids,
        }

        if not spans:
            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="no spans found",
                status="success",
                started_at=started_at,
            )
            return []

        user_data = {
            "spans": [
                {"span_id": str(span.id), "verbatim_text": span.verbatim_text}
                for span in spans
            ]
        }
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
        ]

        try:
            result = self._client.chat_json(messages, schema_hint="extract")
            statements_data = result.get("statements", [])

            span_map = {str(span.id): span for span in spans}
            created: list[SourceStatement] = []
            for stmt_data in statements_data:
                span_id = stmt_data.get("span_id", "")
                span = span_map.get(span_id)
                if span is None:
                    continue
                observed_period = stmt_data.get("observed_period")
                statement = research.add_statement(
                    span.id,
                    stmt_data["normalized_text"],
                    kind=stmt_data["kind"],
                    observed_period=_parse_period(observed_period),
                )
                created.append(statement)

            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=f"extracted {len(created)} statements from {len(spans)} spans",
                status="success",
                started_at=started_at,
            )
            return created

        except Exception as exc:
            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="",
                status="failed",
                error=str(exc),
                started_at=started_at,
            )
            raise


def _parse_period(value):
    """Parse an ISO date string or pass through ``date`` / ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        from datetime import date

        return date.fromisoformat(value)
    return value
