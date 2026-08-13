"""StatementExtractor: produce review-gated atomic claims from frozen spans.

Reads the evidence ledger (SourceSpans for a DocumentVersion). Table-like
spans first go through the deterministic ``FinancialTableExtractor``; only
spans the rules could not handle are sent to the LLM. Both paths create only
``AtomicClaimCandidate`` records with continuous source quotes. Formal
publication requires either a human review or an immutable automatic-admission
decision.

Every extraction operation writes exactly one ``AIRun`` audit record
(``kind=extract``) capturing the model/prompt versions, span IDs processed,
statement count (split into rule-based and LLM), and success/failure status.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.prompts import EXTRACT_PROMPT_VERSION, EXTRACT_SYSTEM
from app.ai.runs import record_run
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import AtomicClaimCandidate, DocumentVersion, SourceSpan
from app.services.atomic_claims import AtomicClaimService
from app.services.table_extraction import FinancialTableExtractor


class StatementExtractor:
    """Extract candidates for human review or immutable automatic admission."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._table_extractor = FinancialTableExtractor()

    def extract(
        self,
        document_version_id: uuid.UUID,
        session: Session,
        *,
        pre_commit_guard: Callable[[Session], None] | None = None,
    ) -> list[AtomicClaimCandidate]:
        started_at = datetime.now(timezone.utc)
        claims = AtomicClaimService(session)
        run_id = uuid.uuid4()
        run_ref = f"extract:{run_id}"
        document = session.get(DocumentVersion, document_version_id)
        authority_level = document.source_authority if document is not None else "unknown"

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
            if pre_commit_guard is not None:
                pre_commit_guard(session)
            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="skipped: no source spans attached to this version",
                status="success",
                started_at=started_at,
                run_id=run_id,
            )
            if pre_commit_guard is not None:
                pre_commit_guard(session)
                session.commit()
            return []

        try:
            # 1. Deterministic pass: table-like spans yield disclosed facts
            #    without involving the LLM.
            rule_based: list[AtomicClaimCandidate] = []
            delayed_rule_drafts: list[AtomicClaimDraft] = []
            handled_span_ids: set[str] = set()
            for span in spans:
                facts = self._table_extractor.extract(span.verbatim_text)
                for fact in facts:
                    draft = AtomicClaimDraft(
                        source_span_id=span.id,
                        quote=fact.quote,
                        quote_start=fact.quote_start,
                        quote_end=fact.quote_end,
                        normalized_text=fact.statement_text,
                        # A table's shape alone cannot establish that its
                        # document is a primary disclosure. Only an
                        # immutable intake authority declaration may keep
                        # the candidate as a disclosed fact.
                        claim_type=(
                            "disclosed_fact"
                            if authority_level == "primary_disclosure"
                            else "reported_claim"
                        ),
                        assertion_actor=None,
                        subject=None,
                        predicate=fact.metric_name,
                        object_text=fact.statement_text,
                        numeric_value=None,
                        unit=None,
                        observed_period=fact.observed_period,
                        scope={},
                    )
                    if pre_commit_guard is None:
                        rule_based.append(
                            claims.admit(
                                draft,
                                authority_level=authority_level,
                                run_ref=run_ref,
                            )
                        )
                    else:
                        delayed_rule_drafts.append(draft)
                if facts:
                    handled_span_ids.add(str(span.id))

            # 2. LLM pass: narrative spans only.
            created: list[AtomicClaimCandidate] = list(rule_based)
            llm_drafts: list[AtomicClaimDraft] = []
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
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
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
                        llm_drafts.append(
                            AtomicClaimDraft(
                                source_span_id=source_span_id,
                                quote=quote,
                                quote_start=quote_start,
                                quote_end=quote_end,
                                normalized_text=stmt_data["normalized_text"],
                                claim_type=(
                                    "disclosed_fact"
                                    if stmt_data["kind"] == "disclosed_fact" and authority_level == "primary_disclosure"
                                    else "reported_claim"
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
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue

            for draft in (*delayed_rule_drafts, *llm_drafts):
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                created.append(
                    claims.admit(
                        draft,
                        authority_level=authority_level,
                        run_ref=run_ref,
                    )
                )

            if pre_commit_guard is not None:
                pre_commit_guard(session)
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
                run_id=run_id,
            )
            if pre_commit_guard is not None:
                pre_commit_guard(session)
                session.commit()
            return created

        except Exception as exc:
            if pre_commit_guard is not None:
                session.rollback()
            try:
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                record_run(
                    session,
                    kind="extract",
                    model_version=self._client.model_version,
                    prompt_version=EXTRACT_PROMPT_VERSION,
                    input_ref=input_ref,
                    output_summary="",
                    status="failed",
                    error=f"extraction_failed:{type(exc).__name__}",
                    started_at=started_at,
                    run_id=run_id,
                )
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                    session.commit()
            except Exception:
                if pre_commit_guard is not None:
                    session.rollback()
                raise
            raise


def _parse_period(value):
    """Parse an ISO date string or pass through ``date`` / ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        from datetime import date

        return date.fromisoformat(value)
    return value
