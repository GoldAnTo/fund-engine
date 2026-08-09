"""StatementExtractor: produce review-gated atomic claims from frozen spans.

Reads the evidence ledger (SourceSpans for a DocumentVersion). Table-like
spans first go through the deterministic ``FinancialTableExtractor``; only
spans the rules could not handle are sent to the LLM. Both paths create only
``AtomicClaimCandidate`` records with continuous source quotes. A human review
is the sole path that may publish a formal ``SourceStatement``.

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
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import AtomicClaimCandidate, SourceSpan
from app.services.atomic_claims import AtomicClaimService
from app.services.table_extraction import FinancialTableExtractor


class StatementExtractor:
    """Extracts atomic candidates; formal statements require human review."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._table_extractor = FinancialTableExtractor()

    def extract(
        self, document_version_id: uuid.UUID, session: Session
    ) -> list[AtomicClaimCandidate]:
        started_at = datetime.now(timezone.utc)
        claims = AtomicClaimService(session)
        run_ref = f"extract:{uuid.uuid4()}"

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
                output_summary="skipped: no source spans attached to this version",
                status="success",
                started_at=started_at,
            )
            return []

        try:
            # 1. Deterministic pass: table-like spans yield disclosed facts
            #    without involving the LLM.
            rule_based: list[AtomicClaimCandidate] = []
            handled_span_ids: set[str] = set()
            for span in spans:
                facts = self._table_extractor.extract(span.verbatim_text)
                for fact in facts:
                    candidate = claims.admit(
                        AtomicClaimDraft(
                            source_span_id=span.id,
                            quote=fact.quote,
                            quote_start=fact.quote_start,
                            quote_end=fact.quote_end,
                            normalized_text=fact.statement_text,
                            # A table's shape alone cannot establish that its
                            # document is a primary disclosure. Until source
                            # authority is explicitly recorded, retain the
                            # fact as a reviewable reported claim.
                            claim_type="reported_claim",
                            assertion_actor=None,
                            subject=None,
                            predicate=fact.metric_name,
                            object_text=fact.statement_text,
                            numeric_value=None,
                            unit=None,
                            observed_period=fact.observed_period,
                            scope={},
                        ),
                        authority_level="unknown",
                        run_ref=run_ref,
                    )
                    rule_based.append(candidate)
                if facts:
                    handled_span_ids.add(str(span.id))

            # 2. LLM pass: narrative spans only.
            created: list[AtomicClaimCandidate] = list(rule_based)
            llm_spans = [s for s in spans if str(s.id) not in handled_span_ids]
            if llm_spans:
                span_ids_by_text_id = {
                    str(span.id): span.id for span in llm_spans
                }
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
                # The initial span query and the deterministic table pass may
                # have opened a read/write transaction.  Publish that small
                # unit before the external provider call so a slow LLM does
                # not retain a database connection or block scope updates.
                session.commit()
                result = self._client.chat_json(messages, schema_hint="extract")
                statements_data = result.get("statements", [])

                for stmt_data in statements_data:
                    span_id = stmt_data.get("span_id", "")
                    source_span_id = span_ids_by_text_id.get(span_id)
                    if source_span_id is None:
                        continue
                    quote = stmt_data.get("quote")
                    quote_start = stmt_data.get("quote_start")
                    quote_end = stmt_data.get("quote_end")
                    if not isinstance(quote, str) or not isinstance(quote_start, int) or not isinstance(quote_end, int):
                        continue
                    try:
                        candidate = claims.admit(
                            AtomicClaimDraft(
                                source_span_id=source_span_id,
                                quote=quote,
                                quote_start=quote_start,
                                quote_end=quote_end,
                                normalized_text=stmt_data["normalized_text"],
                                claim_type=(
                                    "reported_claim"
                                    if stmt_data["kind"] == "disclosed_fact"
                                    else stmt_data["kind"]
                                ),
                                assertion_actor=stmt_data.get("assertion_actor"),
                                subject=stmt_data.get("subject"),
                                predicate=stmt_data.get("predicate"),
                                object_text=stmt_data.get("object_text"),
                                numeric_value=stmt_data.get("numeric_value"),
                                unit=stmt_data.get("unit"),
                                observed_period=_parse_period(stmt_data.get("observed_period")),
                                scope=dict(stmt_data.get("scope") or {}),
                            ),
                            authority_level="unknown",
                            run_ref=run_ref,
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    created.append(candidate)

            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=(
                    f"extracted {len(created)} atomic candidates awaiting review "
                    f"({len(rule_based)} rule-based, "
                    f"{len(created) - len(rule_based)} llm) from {len(spans)} spans"
                    + (
                        "; llm returned 0 statements"
                        if len(created) - len(rule_based) == 0
                        and len(rule_based) == 0
                        and llm_spans
                        else ""
                    )
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
