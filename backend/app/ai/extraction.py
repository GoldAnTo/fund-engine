"""StatementExtractor: produce review-gated atomic claims from frozen spans.

Reads the evidence ledger (SourceSpans for a DocumentVersion). Table-like
spans first go through the deterministic ``FinancialTableExtractor``; only
spans the rules could not handle are sent to the LLM. Both paths create only
``AtomicClaimCandidate`` records with continuous source quotes. Formal
publication requires either a human review or an immutable automatic-admission
decision.

Every extraction operation writes exactly one ``AIRun`` audit record
(``kind=extract``) capturing the model/prompt versions, span IDs processed,
unique returned candidates, rule-based accepted items, LLM
returned/accepted/rejected items, and success/failure status.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import (
    LLM_MALFORMED_RESPONSE_MESSAGE,
    LLMClient,
    LLMFailureCategory,
    LLMJSONResponse,
    LLMMalformedResponseError,
    LLMProviderError,
)
from app.ai.error_safety import AI_OPERATION_ERROR_MESSAGE
from app.ai.prompts import (
    EXTRACT_JSON_RETRY_SYSTEM,
    EXTRACT_PROMPT_VERSION,
    EXTRACT_SYSTEM,
    MAX_EXTRACT_NORMALIZED_TEXT_CHARACTERS,
    MAX_EXTRACT_QUOTE_CHARACTERS,
    MAX_EXTRACT_RETRY_STATEMENTS,
    MAX_EXTRACT_STATEMENTS_PER_RESPONSE,
    MAX_EXTRACT_STATEMENTS_PER_SPAN,
)
from app.ai.runs import record_run
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    AtomicClaimCandidate,
    DocumentVersion,
    SourceSpan,
    ValidationError,
)
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
        before_persist: Callable[[], bool] | None = None,
        pre_commit_guard: Callable[[Session], None] | None = None,
    ) -> list[AtomicClaimCandidate] | None:
        started_at = datetime.now(UTC)
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
            if before_persist is not None and not before_persist():
                return None
            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=(
                    "skipped: no source spans attached to this version; "
                    "llm returned 0, accepted 0, rejected 0"
                ),
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
            rule_drafts: list[AtomicClaimDraft] = []
            handled_span_ids: set[str] = set()
            for span in spans:
                facts = self._table_extractor.extract(span.verbatim_text)
                for fact in facts:
                    rule_drafts.append(
                        AtomicClaimDraft(
                            source_span_id=span.id,
                            quote=fact.quote,
                            quote_start=fact.quote_start,
                            quote_end=fact.quote_end,
                            normalized_text=fact.statement_text,
                            # A table's shape alone cannot establish that its
                            # document is a primary disclosure. Only an
                            # immutable intake authority declaration may keep
                            # the candidate as a disclosed fact.
                            claim_type=("disclosed_fact" if authority_level == "primary_disclosure" else "reported_claim"),
                            assertion_actor=None,
                            subject=None,
                            predicate=fact.metric_name,
                            object_text=fact.statement_text,
                            numeric_value=None,
                            unit=None,
                            observed_period=fact.observed_period,
                            scope={},
                        )
                    )
                if facts:
                    handled_span_ids.add(str(span.id))

            # 2. LLM pass: narrative spans only.
            statements_data: list[object] = []
            llm_returned_count = 0
            llm_accepted_count = 0
            llm_rejected_count = 0
            llm_offsets_repaired_count = 0
            llm_attempt_count = 0
            llm_malformed_retry_count = 0
            effective_statement_limit = MAX_EXTRACT_STATEMENTS_PER_RESPONSE
            spans_by_text_id: dict[str, SourceSpan] = {}
            llm_spans = [s for s in spans if str(s.id) not in handled_span_ids]
            if llm_spans:
                requested_statement_limit = min(
                    MAX_EXTRACT_STATEMENTS_PER_RESPONSE,
                    len(llm_spans) * MAX_EXTRACT_STATEMENTS_PER_SPAN,
                )
                effective_statement_limit = requested_statement_limit
                spans_by_text_id = {
                    str(span.id): span for span in llm_spans
                }
                user_data = {
                    "max_statements": requested_statement_limit,
                    "spans": [
                        {"span_id": str(span.id), "verbatim_text": span.verbatim_text}
                        for span in llm_spans
                    ]
                }
                messages = [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
                ]
                # Only reads and in-memory deterministic drafts exist here.
                # End the read transaction before waiting on the provider;
                # no candidate may become durable before the whole extraction
                # operation succeeds.
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                session.commit()
                result = self._client.chat_json(
                    messages,
                    schema_hint="extract",
                    malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
                )
                if (
                    not isinstance(result, dict)
                    or "statements" not in result
                    or not isinstance(result["statements"], list)
                ):
                    raise LLMMalformedResponseError(
                        LLM_MALFORMED_RESPONSE_MESSAGE
                    )
                if isinstance(result, LLMJSONResponse):
                    llm_attempt_count = result.attempt_count
                    llm_malformed_retry_count = result.malformed_retry_count
                    if llm_malformed_retry_count:
                        effective_statement_limit = min(
                            requested_statement_limit,
                            MAX_EXTRACT_RETRY_STATEMENTS,
                        )
                else:
                    # Test doubles and legacy in-process clients return a plain
                    # dict and represent one successful logical LLM attempt.
                    llm_attempt_count = 1
                statements_data = result["statements"]
                llm_returned_count = len(statements_data)

            # Every output path, including deterministic table-only
            # extraction, must claim the caller's current output slot before
            # creating candidates or a successful audit row.
            if before_persist is not None and not before_persist():
                return None

            # ``admit`` may return an existing row and does not expose a
            # race-safe created/reused flag. Count each successful model item
            # as accepted, but return each persisted candidate identity once.
            returned_candidates: list[AtomicClaimCandidate] = []
            returned_candidate_ids: set[uuid.UUID] = set()
            for draft in rule_drafts:
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                candidate = claims.admit(
                    draft,
                    authority_level=authority_level,
                    run_ref=run_ref,
                )
                if candidate.id not in returned_candidate_ids:
                    returned_candidates.append(candidate)
                    returned_candidate_ids.add(candidate.id)

            accepted_statement_counts_by_span: dict[str, int] = {}
            for item_index, stmt_data in enumerate(statements_data):
                if item_index >= effective_statement_limit:
                    llm_rejected_count += 1
                    continue
                if not isinstance(stmt_data, dict):
                    llm_rejected_count += 1
                    continue
                span_id = stmt_data.get("span_id", "")
                if not isinstance(span_id, str):
                    llm_rejected_count += 1
                    continue
                source_span = spans_by_text_id.get(span_id)
                if source_span is None:
                    llm_rejected_count += 1
                    continue
                if (
                    accepted_statement_counts_by_span.get(span_id, 0)
                    >= MAX_EXTRACT_STATEMENTS_PER_SPAN
                ):
                    llm_rejected_count += 1
                    continue
                quote = stmt_data.get("quote")
                quote_start = stmt_data.get("quote_start")
                quote_end = stmt_data.get("quote_end")
                normalized_text = stmt_data.get("normalized_text")
                kind = stmt_data.get("kind")
                optional_text = {
                    field: stmt_data.get(field)
                    for field in (
                        "assertion_actor",
                        "subject",
                        "predicate",
                        "object_text",
                        "numeric_value",
                        "unit",
                    )
                }
                scope = stmt_data.get("scope")
                if scope is None:
                    scope = {}
                if (
                    not isinstance(quote, str)
                    or isinstance(quote_start, bool)
                    or not isinstance(quote_start, int)
                    or isinstance(quote_end, bool)
                    or not isinstance(quote_end, int)
                    or not isinstance(normalized_text, str)
                    or not normalized_text.strip()
                    or len(quote) > MAX_EXTRACT_QUOTE_CHARACTERS
                    or len(normalized_text) > MAX_EXTRACT_NORMALIZED_TEXT_CHARACTERS
                    or not isinstance(kind, str)
                    or any(
                        value is not None and not isinstance(value, str)
                        for value in optional_text.values()
                    )
                    or not isinstance(scope, dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, str)
                        for key, value in scope.items()
                    )
                ):
                    llm_rejected_count += 1
                    continue
                resolved_offsets = _resolve_verbatim_quote_offsets(
                    source_text=source_span.verbatim_text,
                    quote=quote,
                    quote_start=quote_start,
                    quote_end=quote_end,
                )
                if resolved_offsets is None:
                    llm_rejected_count += 1
                    continue
                offsets_repaired = resolved_offsets != (quote_start, quote_end)
                quote_start, quote_end = resolved_offsets
                try:
                    draft = AtomicClaimDraft(
                        source_span_id=source_span.id,
                        quote=quote,
                        quote_start=quote_start,
                        quote_end=quote_end,
                        normalized_text=normalized_text,
                        claim_type=(
                            "disclosed_fact"
                            if kind == "disclosed_fact"
                            and authority_level == "primary_disclosure"
                            else "reported_claim"
                            if kind == "disclosed_fact"
                            else kind
                        ),
                        assertion_actor=optional_text["assertion_actor"],
                        subject=optional_text["subject"],
                        predicate=optional_text["predicate"],
                        object_text=optional_text["object_text"],
                        numeric_value=optional_text["numeric_value"],
                        unit=optional_text["unit"],
                        observed_period=_parse_period(stmt_data.get("observed_period")),
                        scope=dict(scope),
                    )
                except (KeyError, TypeError, ValueError):
                    llm_rejected_count += 1
                    continue
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                try:
                    candidate = claims.admit(
                        draft,
                        authority_level=authority_level,
                        run_ref=run_ref,
                    )
                except ValidationError:
                    llm_rejected_count += 1
                    continue
                llm_accepted_count += 1
                accepted_statement_counts_by_span[span_id] = (
                    accepted_statement_counts_by_span.get(span_id, 0) + 1
                )
                if offsets_repaired:
                    llm_offsets_repaired_count += 1
                if candidate.id not in returned_candidate_ids:
                    returned_candidates.append(candidate)
                    returned_candidate_ids.add(candidate.id)

            if pre_commit_guard is not None:
                pre_commit_guard(session)
            record_run(
                session,
                kind="extract",
                model_version=self._client.model_version,
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=(
                    "candidate extraction completed; "
                    f"unique candidates returned {len(returned_candidates)}; "
                    f"rule-based accepted items {len(rule_drafts)}; "
                    f"llm returned {llm_returned_count}, "
                    f"accepted {llm_accepted_count}, "
                    f"rejected {llm_rejected_count}, "
                    f"offsets repaired {llm_offsets_repaired_count}, "
                    f"llm attempts {llm_attempt_count}, "
                    f"malformed retries {llm_malformed_retry_count}; "
                    f"source spans {len(spans)}"
                ),
                status="success",
                started_at=started_at,
                run_id=run_id,
            )
            if pre_commit_guard is not None:
                pre_commit_guard(session)
                session.commit()
            return returned_candidates

        except Exception as exc:
            # Discard every candidate admitted before a malformed later item,
            # then create the failed audit in a clean transaction for the
            # caller to commit with its own terminal state.
            failure_summary = _safe_failure_summary(exc)
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
                    output_summary=failure_summary,
                    status="failed",
                    error=AI_OPERATION_ERROR_MESSAGE,
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


def _resolve_verbatim_quote_offsets(
    *,
    source_text: str,
    quote: str,
    quote_start: int,
    quote_end: int,
) -> tuple[int, int] | None:
    """Keep exact offsets or repair only one unambiguous verbatim match."""
    if not quote.strip():
        return None
    if (
        0 <= quote_start < quote_end <= len(source_text)
        and source_text[quote_start:quote_end] == quote
    ):
        return quote_start, quote_end
    first_match = source_text.find(quote)
    if first_match < 0 or source_text.find(quote, first_match + 1) >= 0:
        return None
    return first_match, first_match + len(quote)


def _safe_failure_summary(exc: Exception) -> str:
    if isinstance(exc, LLMProviderError):
        attempt_count = exc.attempt_count
        failure_category = exc.failure_category
    else:
        attempt_count = 0
        failure_category = LLMFailureCategory.UNKNOWN
    return (
        f"llm attempts {attempt_count}; "
        f"failure category {failure_category.value}"
    )


def _parse_period(value: object) -> date | None:
    """Parse an ISO date string or pass through ``date`` / ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, date):
        return value
    raise TypeError("observed_period must be an ISO date string, date, or null")
