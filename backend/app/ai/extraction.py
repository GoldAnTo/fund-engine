"""StatementExtractor: LLM-driven extraction of SourceStatements from spans.

Reads the evidence ledger (SourceSpans for a DocumentVersion).  Table-like
spans first go through the deterministic ``FinancialTableExtractor`` (rule
based, auditable, free); only spans the rules could not handle are sent to
the LLM, which extracts atomic statements from the verbatim text.  All
statements are written through ``ResearchService.add_statement`` with full
kind validation.

Every extraction operation writes exactly one ``AIRun`` audit record
(``kind=extract``) capturing the model/prompt versions, span IDs processed,
statement count (split into rule-based and LLM), and success/failure status.
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
from app.services.table_extraction import FinancialTableExtractor


class StatementExtractor:
    """Extracts atomic SourceStatements from a DocumentVersion's spans."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._table_extractor = FinancialTableExtractor()

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

        try:
            # 1. Deterministic pass: table-like spans yield disclosed facts
            #    without involving the LLM.
            rule_based: list[SourceStatement] = []
            handled_span_ids: set[str] = set()
            for span in spans:
                facts = self._table_extractor.extract(span.verbatim_text)
                for fact in facts:
                    statement = research.add_statement(
                        span.id,
                        fact.statement_text,
                        kind="disclosed_fact",
                        observed_period=fact.observed_period,
                    )
                    rule_based.append(statement)
                if facts:
                    handled_span_ids.add(str(span.id))

            # 2. LLM pass: narrative spans only.
            created: list[SourceStatement] = list(rule_based)
            llm_spans = [s for s in spans if str(s.id) not in handled_span_ids]
            if llm_spans:
                user_data = {
                    "spans": [
                        {"span_id": str(span.id), "verbatim_text": span.verbatim_text}
                        for span in llm_spans
                    ]
                }
                messages = [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
                ]
                result = self._client.chat_json(messages, schema_hint="extract")
                statements_data = result.get("statements", [])

                span_map = {str(span.id): span for span in llm_spans}
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
                output_summary=(
                    f"extracted {len(created)} statements "
                    f"({len(rule_based)} rule-based) from {len(spans)} spans"
                ),
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
