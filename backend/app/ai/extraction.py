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
import re
import uuid
from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.error_safety import AI_OPERATION_ERROR_MESSAGE
from app.ai.prompts import EXTRACT_PROMPT_VERSION, EXTRACT_SYSTEM
from app.ai.runs import record_run
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import AtomicClaimCandidate, DocumentVersion, SourceSpan
from app.services.atomic_claims import AtomicClaimService
from app.services.table_extraction import FinancialTableExtractor


EXTRACT_SELECTION_VERSION = "grounded-span-selection-v1"
_MAX_LLM_SPANS = 8
_MAX_LLM_CHARACTERS = 24_000


def _context_values(context: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = context.get(key)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(value.strip() for value in raw if isinstance(value, str) and value.strip())


def _search_needles(values: tuple[str, ...]) -> tuple[str, ...]:
    needles: set[str] = set()
    for value in values:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._%+-]*|[\u3400-\u9fff]+", value):
            folded = token.casefold()
            if re.fullmatch(r"[\u3400-\u9fff]+", token):
                if len(token) <= 4:
                    if len(token) >= 2:
                        needles.add(token)
                else:
                    needles.update(
                        token[index : index + 4]
                        for index in range(len(token) - 3)
                    )
            elif len(folded) >= 3:
                needles.add(folded)
    return tuple(sorted(needles, key=lambda item: (-len(item), item)))


def _select_llm_spans(
    spans: list[SourceSpan], grounding_context: Mapping[str, object]
) -> list[SourceSpan]:
    """Bound provider input to the report pages grounded in the Case scope."""

    metric_needles = _search_needles(_context_values(grounding_context, "metric_terms"))
    entity_needles = _search_needles(_context_values(grounding_context, "entity_names"))
    period_needles = tuple(
        sorted(
            {
                match
                for key in ("period_start", "period_end")
                for match in re.findall(r"\b\d{4}\b", str(grounding_context.get(key) or ""))
            }
        )
    )
    scored: list[tuple[int, int, int, int, SourceSpan]] = []
    for index, span in enumerate(spans):
        text = span.verbatim_text.casefold()
        metric_score = sum(1 for needle in metric_needles if needle in text)
        entity_score = sum(1 for needle in entity_needles if needle in text)
        period_score = sum(1 for needle in period_needles if needle in text)
        scored.append((metric_score, entity_score, period_score, index, span))

    if metric_needles and any(item[0] > 0 for item in scored):
        candidates = [item for item in scored if item[0] > 0]
    elif entity_needles and any(item[1] > 0 for item in scored):
        candidates = [item for item in scored if item[1] > 0]
    else:
        candidates = scored
    candidates.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))

    selected: list[SourceSpan] = []
    character_count = 0
    for *_score, span in candidates:
        span_size = len(span.verbatim_text)
        if selected and character_count + span_size > _MAX_LLM_CHARACTERS:
            continue
        selected.append(span)
        character_count += span_size
        if len(selected) >= _MAX_LLM_SPANS or character_count >= _MAX_LLM_CHARACTERS:
            break
    return selected


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
        grounding_context: Mapping[str, object] | None = None,
        before_persist: Callable[[], bool] | None = None,
        pre_commit_guard: Callable[[Session], None] | None = None,
    ) -> list[AtomicClaimCandidate] | None:
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
        frozen_grounding_context = dict(grounding_context or {})
        input_ref = {
            "document_version_id": str(document_version_id),
            "span_ids": span_ids,
            "grounding_context": frozen_grounding_context,
            "selection_version": EXTRACT_SELECTION_VERSION,
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
            rule_drafts: list[AtomicClaimDraft] = []
            handled_span_ids: set[str] = set()
            for span in spans:
                facts = self._table_extractor.extract(span.verbatim_text)
                matched_entities = tuple(
                    entity
                    for entity in _context_values(
                        frozen_grounding_context, "entity_names"
                    )
                    if entity in span.verbatim_text
                )
                table_subject = (
                    matched_entities[0] if len(matched_entities) == 1 else None
                )
                for fact in facts:
                    period_text = (
                        f"{fact.observed_period.year}年"
                        f"{fact.observed_period.month}月{fact.observed_period.day}日"
                    )
                    value_text = fact.statement_text.split("为", 1)[-1]
                    normalized_text = (
                        f"{table_subject or ''}{period_text}"
                        f"{fact.predicate}为{value_text}"
                    )
                    rule_drafts.append(
                        AtomicClaimDraft(
                            source_span_id=span.id,
                            quote=fact.quote,
                            quote_start=fact.quote_start,
                            quote_end=fact.quote_end,
                            normalized_text=normalized_text,
                            # A table's shape alone cannot establish that its
                            # document is a primary disclosure. Only an
                            # immutable intake authority declaration may keep
                            # the candidate as a disclosed fact.
                            claim_type=("disclosed_fact" if authority_level == "primary_disclosure" else "reported_claim"),
                            assertion_actor=table_subject,
                            subject=table_subject,
                            predicate=fact.predicate,
                            object_text=fact.statement_text,
                            numeric_value=None,
                            unit=None,
                            observed_period=fact.observed_period,
                            scope={"extraction_method": "financial_table_v1"},
                        )
                    )
                if facts:
                    handled_span_ids.add(str(span.id))

            # 2. LLM pass: narrative spans only.
            statements_data: list[dict] = []
            llm_failed = False
            span_ids_by_text_id: dict[str, uuid.UUID] = {}
            llm_spans = _select_llm_spans(
                [s for s in spans if str(s.id) not in handled_span_ids],
                frozen_grounding_context,
            )
            input_ref["selected_span_ids"] = [str(span.id) for span in llm_spans]
            if llm_spans:
                span_ids_by_text_id = {
                    str(span.id): span.id for span in llm_spans
                }
                user_data = {
                    "spans": [
                        {"span_id": str(span.id), "verbatim_text": span.verbatim_text}
                        for span in llm_spans
                    ],
                    "grounding_context": frozen_grounding_context,
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
                try:
                    result = self._client.chat_json(messages, schema_hint="extract")
                    statements_data = result.get("statements", [])
                except Exception:
                    if not rule_drafts:
                        raise
                    llm_failed = True
                    input_ref["rule_fallback"] = True

            # Every output path, including deterministic table-only
            # extraction, must claim the caller's current output slot before
            # creating candidates or a successful audit row.
            if before_persist is not None and not before_persist():
                return None

            created: list[AtomicClaimCandidate] = []
            for draft in rule_drafts:
                if pre_commit_guard is not None:
                    pre_commit_guard(session)
                created.append(
                    claims.admit(
                        draft,
                        authority_level=authority_level,
                        run_ref=run_ref,
                    )
                )

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
                    if pre_commit_guard is not None:
                        pre_commit_guard(session)
                    candidate = claims.admit(
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
                        ),
                        authority_level=authority_level,
                        run_ref=run_ref,
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                created.append(candidate)

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
                    f"({len(rule_drafts)} rule-based, "
                    f"{len(created) - len(rule_drafts)} llm) from {len(spans)} spans"
                    + (
                        "; narrative provider failed, deterministic table facts retained"
                        if llm_failed
                        else
                        "; llm returned 0 statements"
                        if len(created) - len(rule_drafts) == 0
                        and len(rule_drafts) == 0
                        and llm_spans
                        else ""
                    )
                ),
                status="partial" if llm_failed else "success",
                error=AI_OPERATION_ERROR_MESSAGE if llm_failed else None,
                started_at=started_at,
                run_id=run_id,
            )
            if pre_commit_guard is not None:
                pre_commit_guard(session)
                session.commit()
            return created

        except Exception:
            # Discard every candidate admitted before a malformed later item,
            # then create the failed audit in a clean transaction for the
            # caller to commit with its own terminal state.
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


def _parse_period(value):
    """Parse an ISO date string or pass through ``date`` / ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        from datetime import date

        return date.fromisoformat(value)
    return value
