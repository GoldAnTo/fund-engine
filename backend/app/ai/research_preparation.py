"""LLM drafts for the review-gated research-preparation workbench.

The module deliberately stops at draft artifacts: it may admit source-grounded
atomic *candidates*, but never publishes source statements, creates a research
run, starts collection, or materializes a research protocol.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
from app.domain.research_preparation import MAX_CANDIDATES, candidate_context_fingerprint
from app.errors import NotFoundError
from app.models.event_research import EventResearchScopeFactor, EventResearchScopeVersion
from app.models.ledger import (
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)
from app.models.research_preparation import ResearchPreparation
from app.models.research_preparation import ResearchPreparationArtifact
from app.models.source_governance import ProviderRecord, SourceContract
from app.services.atomic_claims import AtomicClaimService
from app.services.source_admission import source_contract_is_active


PREPARATION_PROVIDER_ERROR_MESSAGE = (
    "preparation provider unavailable or returned an invalid response"
)
PREPARATION_INPUT_UNAVAILABLE_MESSAGE = "preparation input is unavailable for AI processing"
PREPARATION_CONTEXT_LIMIT_MESSAGE = "preparation input exceeds safe context limits"
MAX_CONTEXT_CHARACTERS = 120_000
MAX_PARSE_CONTEXT_CHARACTERS = 120_000
MAX_FACTOR_CHARACTERS = 4_000
MAX_CANDIDATE_QUOTE_CHARACTERS = 2_000
MAX_CANDIDATE_NORMALIZED_CHARACTERS = 4_000


class ResearchPreparationProviderError(RuntimeError):
    """A fixed, safe boundary for preparation-provider failures."""


class PreparationInputUnavailableError(RuntimeError):
    """Raised when case-governed frozen input cannot be sent to an LLM."""


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
    parse_artifact_sequence: int | None
    candidate_context_fingerprint: str


def load_preparation_input(session: Session, case_id: uuid.UUID) -> PreparationInput:
    """Load the case-scoped frozen input required for one preparation draft."""
    if session.get(ResearchCase, case_id) is None:
        raise NotFoundError("research case not found")
    preparation = session.scalar(
        select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id)
    )
    if preparation is None:
        raise NotFoundError("research preparation not found")
    admission = session.scalar(
        select(CaseTenantAdmission)
        .where(CaseTenantAdmission.research_case_id == case_id)
        .limit(1)
    )
    if admission is None:
        _unavailable_input()
    case_document = session.scalar(
        select(CaseDocumentVersion.id).where(
            CaseDocumentVersion.research_case_id == case_id,
            CaseDocumentVersion.document_version_id == admission.initial_document_version_id,
        )
    )
    if case_document is None:
        _unavailable_input()
    document = session.get(DocumentVersion, admission.initial_document_version_id)
    if document is None:
        _unavailable_input()
    if not preparation_ai_input_is_available(session, document):
        _unavailable_input()

    spans = tuple(
        PreparationSourceSpan(source_span_id=span.id, verbatim_text=span.verbatim_text)
        for span in session.scalars(
            select(SourceSpan)
            .where(SourceSpan.document_version_id == document.id)
            .order_by(SourceSpan.id)
        )
    )
    if len(spans) > 50 or any(len(span.verbatim_text) > 20_000 for span in spans):
        _unavailable_input()
    parse_artifact = session.scalar(
        select(ResearchPreparationArtifact)
        .where(
            ResearchPreparationArtifact.research_preparation_id == preparation.id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        )
        .limit(1)
    )
    candidates, candidate_context_fingerprint = _confirmed_artifact_candidates(
        session,
        parse_artifact,
        spans,
    )
    if len(candidates) > MAX_CANDIDATES:
        _unavailable_input()
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
        parse_artifact_sequence=parse_artifact.sequence if parse_artifact else None,
        candidate_context_fingerprint=candidate_context_fingerprint,
    )


def _unavailable_input() -> None:
    raise PreparationInputUnavailableError(PREPARATION_INPUT_UNAVAILABLE_MESSAGE)


def preparation_ai_input_is_available(
    session: Session,
    document: DocumentVersion,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether frozen document use is still allowed for preparation AI."""
    contract = session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document.id)
    )
    instant = now or datetime.now(timezone.utc)
    if (
        contract is None
        or not contract.allow_ai_processing
        or not source_contract_is_active(contract, at=document.available_at)
        or not source_contract_is_active(contract, at=instant)
    ):
        return False
    if contract.source_type != "licensed_provider":
        return True
    provider_record = session.scalar(
        select(ProviderRecord).where(ProviderRecord.document_version_id == document.id)
    )
    return (
        provider_record is not None
        and provider_record.content_sha256 == document.content_sha256
    )


def _context_limit_unavailable() -> None:
    raise PreparationInputUnavailableError(PREPARATION_CONTEXT_LIMIT_MESSAGE)


def _confirmed_artifact_candidates(
    session: Session,
    artifact: ResearchPreparationArtifact | None,
    spans: tuple[PreparationSourceSpan, ...],
) -> tuple[tuple[PreparationCandidateSummary, ...], str]:
    """Return latest-human-approved candidates in the artifact's stored order."""
    if artifact is None:
        return (), _candidate_context_fingerprint(None, ())
    raw_candidates = artifact.payload.get("candidates")
    if not isinstance(raw_candidates, list):
        _unavailable_input()
    candidate_ids: list[uuid.UUID] = []
    for item in raw_candidates:
        raw_id = item.get("candidate_id") if isinstance(item, dict) else None
        if not isinstance(raw_id, str):
            _unavailable_input()
        try:
            candidate_id = uuid.UUID(raw_id)
        except ValueError:
            _unavailable_input()
        if candidate_id in candidate_ids:
            _unavailable_input()
        candidate_ids.append(candidate_id)
    if not candidate_ids:
        return (), _candidate_context_fingerprint(artifact.sequence, ())
    candidate_by_id = {
        candidate.id: candidate
        for candidate in session.scalars(
            select(AtomicClaimCandidate).where(AtomicClaimCandidate.id.in_(candidate_ids))
        )
    }
    if set(candidate_by_id) != set(candidate_ids):
        _unavailable_input()
    span_ids = {span.source_span_id for span in spans}
    if any(candidate.source_span_id not in span_ids for candidate in candidate_by_id.values()):
        _unavailable_input()
    latest_reviews: dict[uuid.UUID, AtomicClaimReview] = {}
    for review in session.scalars(
        select(AtomicClaimReview)
        .where(AtomicClaimReview.atomic_claim_candidate_id.in_(candidate_ids))
        .order_by(
            AtomicClaimReview.atomic_claim_candidate_id,
            AtomicClaimReview.created_at.desc(),
            AtomicClaimReview.id.desc(),
        )
    ):
        latest_reviews.setdefault(review.atomic_claim_candidate_id, review)
    decision_tuples: list[tuple[str, str, str, str | None, str | None]] = []
    summaries: list[PreparationCandidateSummary] = []
    for candidate_id in candidate_ids:
        review = latest_reviews.get(candidate_id)
        if review is None:
            continue
        candidate = candidate_by_id[candidate_id]
        effective_text_hash: str | None = None
        normalized_text: str | None = None
        if review.outcome in {"confirmed", "modified"}:
            normalized_text = candidate.normalized_text
            if review.outcome == "modified":
                normalized_text = _modified_statement_text(
                    session,
                    review,
                    candidate,
                    span_ids,
                )
            effective_text_hash = hashlib.sha256(
                normalized_text.encode("utf-8")
            ).hexdigest()
        decision_tuples.append((
            str(candidate_id),
            str(review.id),
            review.outcome,
            str(review.published_source_statement_id)
            if review.published_source_statement_id else None,
            effective_text_hash,
        ))
        if normalized_text is not None:
            summaries.append(PreparationCandidateSummary(
                candidate_id=candidate.id,
                source_span_id=candidate.source_span_id,
                quote=candidate.quote,
                normalized_text=normalized_text,
                claim_type=candidate.claim_type,
            ))
    return tuple(summaries), _candidate_context_fingerprint(
        artifact.sequence,
        tuple(decision_tuples),
    )


def _modified_statement_text(
    session: Session,
    review: AtomicClaimReview,
    candidate: AtomicClaimCandidate,
    span_ids: set[uuid.UUID],
) -> str:
    statement_id = review.published_source_statement_id
    if statement_id is None:
        _unavailable_input()
    statement = session.get(SourceStatement, statement_id)
    if (
        statement is None
        or statement.atomic_claim_candidate_id != candidate.id
        or statement.source_span_id != candidate.source_span_id
        or statement.source_span_id not in span_ids
        or not statement.normalized_text.strip()
    ):
        _unavailable_input()
    return statement.normalized_text


def _candidate_context_fingerprint(
    sequence: int | None,
    decisions: tuple[tuple[str, str, str, str | None, str | None], ...],
) -> str:
    return candidate_context_fingerprint(sequence, decisions)


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
        drafts = self.validate_claim_drafts(input)
        payload = self.persist_claim_drafts(
            input,
            drafts,
            session,
            before_persist=lambda: True,
        )
        assert payload is not None
        return payload

    def validate_claim_drafts(self, input: PreparationInput) -> list[AtomicClaimDraft]:
        """Call the LLM and validate claim drafts without any database writes."""
        _ensure_input_bounds(input)
        if not input.source_spans:
            return []
        payload = _parse_context(input)
        if len(json.dumps(payload, ensure_ascii=False)) > MAX_PARSE_CONTEXT_CHARACTERS:
            _context_limit_unavailable()
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_PARSE_CLAIMS_SYSTEM,
            payload,
            "preparation_parse_claims",
        ))
        statements = self._provider_call(lambda: _validate_parse_response(result, input))
        return [
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
            )
            for statement in statements
        ]

    def persist_claim_drafts(
        self,
        input: PreparationInput,
        drafts: list[AtomicClaimDraft],
        session: Session,
        *,
        before_persist: Callable[[], bool],
    ) -> dict[str, object] | None:
        """Persist validated drafts only after the caller owns its output slot.

        Task4 calls ``validate_claim_drafts``, takes its case/preparation lock,
        then calls this method with its freshness guard and completes the
        preparation artifact in the same caller-managed transaction.
        """
        if not before_persist():
            return None
        _validate_drafts_for_input(drafts, input)
        claims = AtomicClaimService(session)
        run_ref = f"preparation:parse:{uuid.uuid4()}"
        candidates = []
        for draft in drafts:
            candidate = claims.admit(
                draft,
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
        _ensure_draft_context_bounds(input)
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_PROTOCOL_SYSTEM,
            _draft_context(input),
            "preparation_draft_protocol",
        ))
        draft = self._provider_call(lambda: _validate_protocol_response(result))
        return draft

    def draft_evidence_plan(self, input: PreparationInput) -> dict[str, object]:
        _ensure_draft_context_bounds(input)
        result = self._provider_call(lambda: self._chat_json(
            PREPARATION_EVIDENCE_PLAN_SYSTEM,
            _draft_context(input),
            "preparation_draft_evidence_plan",
        ))
        draft = self._provider_call(lambda: _validate_evidence_plan_response(result, input.current_factors))
        return draft

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


def _parse_context(input: PreparationInput) -> dict[str, object]:
    return {"spans": [
        {
            "source_span_id": str(span.source_span_id),
            "verbatim_text": span.verbatim_text,
        }
        for span in input.source_spans
    ]}


def _ensure_input_bounds(input: PreparationInput) -> None:
    if (
        len(input.source_spans) > 50
        or any(len(span.verbatim_text) > 20_000 for span in input.source_spans)
        or len(input.candidate_claim_summaries) > MAX_CANDIDATES
    ):
        _unavailable_input()


def _ensure_draft_context_bounds(input: PreparationInput) -> None:
    _ensure_input_bounds(input)
    if (
        any(len(factor) > MAX_FACTOR_CHARACTERS for factor in input.current_factors)
        or any(
            len(candidate.quote) > MAX_CANDIDATE_QUOTE_CHARACTERS
            or len(candidate.normalized_text) > MAX_CANDIDATE_NORMALIZED_CHARACTERS
            for candidate in input.candidate_claim_summaries
        )
        or len(json.dumps(_draft_context(input), ensure_ascii=False)) > MAX_CONTEXT_CHARACTERS
    ):
        _unavailable_input()


def _validate_drafts_for_input(
    drafts: list[AtomicClaimDraft], input: PreparationInput
) -> None:
    """Recheck complete drafts before the first candidate write."""
    spans = {span.source_span_id: span.verbatim_text for span in input.source_spans}
    allowed_claim_types = {
        "disclosed_fact",
        "reported_claim",
        "management_attribution",
        "forecast",
        "research_opinion",
    }
    for draft in drafts:
        text = spans.get(draft.source_span_id)
        if (
            text is None
            or draft.claim_type not in allowed_claim_types
            or not draft.normalized_text.strip()
            or draft.quote_start < 0
            or draft.quote_end <= draft.quote_start
            or text[draft.quote_start:draft.quote_end] != draft.quote
            or (
                draft.claim_type == "disclosed_fact"
                and input.source_authority != "primary_disclosure"
            )
        ):
            raise ValueError("claim drafts are not valid for this preparation input")


def _validate_parse_response(result: object, input: PreparationInput) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or set(result) != {"statements"}:
        raise ValueError("parse response must contain exactly statements")
    raw_statements = result["statements"]
    if not isinstance(raw_statements, list):
        raise ValueError("parse statements must be a list")
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
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("protocol response has invalid keys")
    if not _nonempty_object_list(result["outcomes"]):
        raise ValueError("protocol outcomes must be nonempty")
    if not isinstance(result["baseline"], dict):
        raise ValueError("protocol baseline must be an object")
    if not _nonempty_object_list(result["mechanisms"]):
        raise ValueError("protocol mechanisms must be nonempty")
    if not _nonempty_object_list(result["verification_rules"]):
        raise ValueError("protocol verification rules must be nonempty")
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


def _nonempty_object_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and bool(item) for item in value)
    )


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
