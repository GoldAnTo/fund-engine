"""LLM drafts for the review-gated research-preparation workbench.

The module deliberately stops at draft artifacts: it may admit source-grounded
atomic *candidates*, but never publishes source statements, creates a research
run, starts collection, or materializes a research protocol.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypeVar

import httpx
from openai import OpenAIError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import LLMClient, LLMMalformedResponseError, LLMProviderError
from app.ai.prompts import (
    PREPARATION_EVIDENCE_PLAN_SYSTEM,
    PREPARATION_PARSE_CLAIMS_SYSTEM,
    PREPARATION_PROTOCOL_SYSTEM,
)
from app.domain.atomic_claims import AtomicClaimDraft
from app.errors import NotFoundError
from app.models.event_research import EventResearchScopeFactor, EventResearchScopeVersion
from app.models.ledger import AtomicClaimCandidate, CaseDocumentVersion, DocumentVersion, ResearchCase, SourceSpan
from app.models.research_preparation import ResearchPreparation
from app.services.atomic_claims import AtomicClaimService


PREPARATION_PROVIDER_ERROR_MESSAGE = (
    "preparation provider unavailable or returned an invalid response"
)


class ResearchPreparationProviderError(RuntimeError):
    """A fixed, safe boundary for preparation-provider failures."""


@dataclass(frozen=True, slots=True)
class PreparationSourceSpan:
    source_span_id: uuid.UUID
    verbatim_text: str


@dataclass(frozen=True, slots=True)
class PreparationCandidateSummary:
    candidate_id: uuid.UUID
    source_span_id: uuid.UUID
    quote: str
    normalized_text: str
    claim_type: str


@dataclass(frozen=True, slots=True)
class PreparationInput:
    case_id: uuid.UUID
    preparation_id: uuid.UUID
    preparation_version: int
    input_fingerprint: str
    document_version_id: uuid.UUID
    source_authority: str
    source_spans: tuple[PreparationSourceSpan, ...]
    current_factors: tuple[str, ...]
    candidate_claim_summaries: tuple[PreparationCandidateSummary, ...]


def load_preparation_input(session: Session, case_id: uuid.UUID) -> PreparationInput:
    """Load the case-scoped frozen input required for one preparation draft."""
    if session.get(ResearchCase, case_id) is None:
        raise NotFoundError("research case not found")
    preparation = session.scalar(
        select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id)
    )
    if preparation is None:
        raise NotFoundError("research preparation not found")
    case_document = session.scalar(
        select(CaseDocumentVersion)
        .where(CaseDocumentVersion.research_case_id == case_id)
        .order_by(CaseDocumentVersion.linked_at, CaseDocumentVersion.id)
        .limit(1)
    )
    if case_document is None:
        raise NotFoundError("case document version not found")
    document = session.get(DocumentVersion, case_document.document_version_id)
    if document is None:  # protects the input boundary if an invalid FK was imported
        raise NotFoundError("case document version not found")

    spans = tuple(
        PreparationSourceSpan(source_span_id=span.id, verbatim_text=span.verbatim_text)
        for span in session.scalars(
            select(SourceSpan)
            .where(SourceSpan.document_version_id == document.id)
            .order_by(SourceSpan.id)
        )
    )
    candidates = tuple(
        PreparationCandidateSummary(
            candidate_id=candidate.id,
            source_span_id=candidate.source_span_id,
            quote=candidate.quote,
            normalized_text=candidate.normalized_text,
            claim_type=candidate.claim_type,
        )
        for candidate in session.scalars(
            select(AtomicClaimCandidate)
            .join(SourceSpan, AtomicClaimCandidate.source_span_id == SourceSpan.id)
            .where(SourceSpan.document_version_id == document.id)
            .order_by(AtomicClaimCandidate.created_at, AtomicClaimCandidate.id)
        )
    )
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc(), EventResearchScopeVersion.id.desc())
        .limit(1)
    )
    factors = () if scope is None else tuple(
        session.scalars(
            select(EventResearchScopeFactor.statement)
            .where(EventResearchScopeFactor.scope_version_id == scope.id)
            .order_by(EventResearchScopeFactor.position, EventResearchScopeFactor.id)
        )
    )
    return PreparationInput(
        case_id=case_id,
        preparation_id=preparation.id,
        preparation_version=preparation.version,
        input_fingerprint=preparation.input_fingerprint,
        document_version_id=document.id,
        source_authority=document.source_authority,
        source_spans=spans,
        current_factors=factors,
        candidate_claim_summaries=candidates,
    )


_T = TypeVar("_T")
_KNOWN_PROVIDER_FAILURES = (
    OpenAIError,
    httpx.HTTPError,
    TimeoutError,
    ConnectionError,
    LLMProviderError,
    LLMMalformedResponseError,
    ValueError,
)
_KIND_TO_CLAIM_TYPE = {
    "disclosed_fact": "disclosed_fact",
    "reported_claim": "reported_claim",
    "management_claim": "management_attribution",
    "market_claim": "research_opinion",
}


class ResearchPreparationGenerator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client

    def parse_claims(self, input: PreparationInput, session: Session) -> dict[str, object]:
        if not input.source_spans:
            return {"candidates": []}
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_PARSE_CLAIMS_SYSTEM,
            {"spans": [
                {"source_span_id": str(span.source_span_id), "verbatim_text": span.verbatim_text}
                for span in input.source_spans
            ]},
            "preparation_parse_claims",
        ))
        statements = self._provider_call(lambda: _validate_parse_response(result, input))

        # Validation finishes before the first insert, ensuring malformed
        # provider output cannot leave a partial candidate set behind.
        claims = AtomicClaimService(session)
        run_ref = f"preparation:parse:{uuid.uuid4()}"
        candidates = []
        for statement in statements:
            candidate = claims.admit(
                AtomicClaimDraft(
                    source_span_id=statement["source_span_id"],
                    quote=statement["quote"],
                    quote_start=statement["quote_start"],
                    quote_end=statement["quote_end"],
                    normalized_text=statement["normalized_text"],
                    claim_type=statement["claim_type"],
                    assertion_actor=statement["actor"],
                    subject=None,
                    predicate=None,
                    object_text=None,
                    numeric_value=None,
                    unit=None,
                    observed_period=None,
                    scope={},
                ),
                authority_level=input.source_authority,
                run_ref=run_ref,
            )
            candidates.append({
                "candidate_id": str(candidate.id),
                "source_span_id": str(candidate.source_span_id),
                "quote": candidate.quote,
                "quote_start": candidate.quote_start,
                "quote_end": candidate.quote_end,
                "normalized_text": candidate.normalized_text,
                "claim_type": candidate.claim_type,
            })
        return {"candidates": candidates}

    def draft_protocol(self, input: PreparationInput) -> dict[str, object]:
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_PROTOCOL_SYSTEM,
            _draft_context(input),
            "preparation_draft_protocol",
        ))
        return self._provider_call(lambda: _validate_protocol_response(result))

    def draft_evidence_plan(self, input: PreparationInput) -> dict[str, object]:
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_EVIDENCE_PLAN_SYSTEM,
            _draft_context(input),
            "preparation_draft_evidence_plan",
        ))
        return self._provider_call(lambda: _validate_evidence_plan_response(result, input.current_factors))

    def _chat_json(self, system: str, payload: dict[str, object], schema_hint: str) -> dict:
        client = self._client
        if client is None:
            client = LLMClient.from_env()
            self._client = client
        return client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            schema_hint=schema_hint,
        )

    @staticmethod
    def _provider_call(operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except _KNOWN_PROVIDER_FAILURES as exc:
            raise ResearchPreparationProviderError(
                PREPARATION_PROVIDER_ERROR_MESSAGE
            ) from exc


def _draft_context(input: PreparationInput) -> dict[str, object]:
    return {
        "factors": list(input.current_factors),
        "candidate_claims": [
            {
                "candidate_id": str(candidate.candidate_id),
                "source_span_id": str(candidate.source_span_id),
                "quote": candidate.quote,
                "normalized_text": candidate.normalized_text,
                "claim_type": candidate.claim_type,
            }
            for candidate in input.candidate_claim_summaries
        ],
    }


def _validate_parse_response(result: object, input: PreparationInput) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or set(result) != {"statements"}:
        raise ValueError("parse response must contain exactly statements")
    raw_statements = result["statements"]
    if not isinstance(raw_statements, list):
        raise ValueError("parse statements must be a list")
    if input.source_spans and not raw_statements:
        raise ValueError("parse response must contain a statement for supplied spans")
    spans = {str(span.source_span_id): span for span in input.source_spans}
    statements: list[dict[str, Any]] = []
    required = {"source_span_id", "quote", "quote_start", "quote_end", "normalized_text", "kind"}
    for raw in raw_statements:
        if not isinstance(raw, dict) or not required.issubset(raw) or set(raw).difference(required | {"actor"}):
            raise ValueError("parse statement is malformed")
        span_id = raw["source_span_id"]
        quote = raw["quote"]
        start = raw["quote_start"]
        end = raw["quote_end"]
        normalized = raw["normalized_text"]
        kind = raw["kind"]
        actor = raw.get("actor")
        if (
            not isinstance(span_id, str) or span_id not in spans
            or not isinstance(quote, str) or not quote
            or type(start) is not int or type(end) is not int
            or not isinstance(normalized, str) or not normalized.strip()
            or kind not in _KIND_TO_CLAIM_TYPE
            or (actor is not None and (not isinstance(actor, str) or not actor.strip()))
        ):
            raise ValueError("parse statement has invalid values")
        source_span = spans[span_id]
        if start < 0 or end <= start or end > len(source_span.verbatim_text) or source_span.verbatim_text[start:end] != quote:
            raise ValueError("parse quote is not a continuous source slice")
        claim_type = _KIND_TO_CLAIM_TYPE[kind]
        if claim_type == "disclosed_fact" and input.source_authority != "primary_disclosure":
            claim_type = "reported_claim"
        statements.append({
            "source_span_id": source_span.source_span_id,
            "quote": quote,
            "quote_start": start,
            "quote_end": end,
            "normalized_text": normalized.strip(),
            "claim_type": claim_type,
            "actor": actor.strip() if isinstance(actor, str) else None,
        })
    return statements


def _validate_protocol_response(result: object) -> dict[str, object]:
    required = {"outcomes", "baseline", "horizon", "mechanisms", "verification_rules"}
    if not isinstance(result, dict) or not required.issubset(result) or set(result).difference(required | {"rationale"}):
        raise ValueError("protocol response has invalid keys")
    if not isinstance(result["outcomes"], list) or not result["outcomes"]:
        raise ValueError("protocol outcomes must be nonempty")
    if not isinstance(result["baseline"], dict):
        raise ValueError("protocol baseline must be an object")
    if not isinstance(result["mechanisms"], list) or not result["mechanisms"]:
        raise ValueError("protocol mechanisms must be nonempty")
    if not isinstance(result["verification_rules"], list) or not result["verification_rules"]:
        raise ValueError("protocol verification rules must be nonempty")
    if "rationale" in result and not isinstance(result["rationale"], str):
        raise ValueError("protocol rationale must be a string")
    horizon = result["horizon"]
    if not isinstance(horizon, dict) or set(horizon) != {"start", "end"}:
        raise ValueError("protocol horizon has invalid keys")
    start, end = horizon["start"], horizon["end"]
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("protocol horizon dates must be strings")
    start_date, end_date = _strict_iso_date(start), _strict_iso_date(end)
    if start_date > end_date:
        raise ValueError("protocol horizon is reversed")
    return result


def _strict_iso_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must be ISO YYYY-MM-DD")
    return parsed


def _validate_evidence_plan_response(result: object, factors: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(result, dict) or set(result) != {"items"} or not isinstance(result["items"], list) or not result["items"]:
        raise ValueError("evidence plan must contain nonempty items")
    allowed_factors = set(factors)
    seen: set[str] = set()
    keys = {"factor", "evidence_target", "allowed_source_roles", "priority", "stop_condition", "budget"}
    for item in result["items"]:
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError("evidence plan item has invalid keys")
        factor = item["factor"]
        if not isinstance(factor, str) or factor not in allowed_factors or factor in seen:
            raise ValueError("evidence plan factor is invalid")
        seen.add(factor)
        if any(not isinstance(item[field], str) or not item[field].strip() for field in ("evidence_target", "stop_condition")):
            raise ValueError("evidence plan strings must be nonempty")
        roles = item["allowed_source_roles"]
        if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or not role.strip() for role in roles):
            raise ValueError("evidence plan roles are invalid")
        if item["priority"] not in {"high", "normal", "low"}:
            raise ValueError("evidence plan priority is invalid")
        if type(item["budget"]) is not int or item["budget"] <= 0:
            raise ValueError("evidence plan budget is invalid")
    return result
