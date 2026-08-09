"""Admission and human review for source-grounded atomic claim candidates."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import AtomicClaimCandidate, AtomicClaimReview, SourceSpan, ValidationError


_CLAIM_TYPES = frozenset({"disclosed_fact", "reported_claim", "management_attribution", "forecast", "research_opinion"})
_AUTHORITY_LEVELS = frozenset({"primary_disclosure", "licensed_research", "secondary_source", "user_supplied", "unknown"})
_REVIEW_OUTCOMES = frozenset({"confirmed", "modified", "rejected"})


class AtomicClaimService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def admit(self, draft: AtomicClaimDraft, *, authority_level: str, run_ref: str) -> AtomicClaimCandidate:
        span = self._session.get(SourceSpan, draft.source_span_id)
        if span is None:
            raise ValidationError("source span not found")
        if draft.claim_type not in _CLAIM_TYPES:
            raise ValidationError("atomic claim type is invalid")
        if authority_level not in _AUTHORITY_LEVELS:
            raise ValidationError("atomic claim authority level is invalid")
        if not run_ref.strip():
            raise ValidationError("atomic claim run_ref must not be empty")
        if draft.quote_start < 0 or draft.quote_end <= draft.quote_start or span.verbatim_text[draft.quote_start:draft.quote_end] != draft.quote:
            raise ValidationError("atomic claim quote must be a continuous source span slice")

        quote_sha256 = hashlib.sha256(draft.quote.encode("utf-8")).hexdigest()
        canonical_payload = {"span": str(draft.source_span_id), "quote_sha256": quote_sha256, "claim_type": draft.claim_type, "normalized_text": draft.normalized_text, "scope": draft.scope}
        canonical_key = hashlib.sha256(json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        existing = self._session.scalar(select(AtomicClaimCandidate).where(AtomicClaimCandidate.canonical_key == canonical_key))
        if existing is not None:
            return existing
        candidate = AtomicClaimCandidate(
            source_span_id=draft.source_span_id,
            canonical_key=canonical_key,
            quote=draft.quote,
            quote_start=draft.quote_start,
            quote_end=draft.quote_end,
            quote_sha256=quote_sha256,
            normalized_text=draft.normalized_text,
            claim_type=draft.claim_type,
            assertion_actor=draft.assertion_actor,
            authority_level=authority_level,
            structured_fields={"subject": draft.subject, "predicate": draft.predicate, "object_text": draft.object_text, "numeric_value": draft.numeric_value, "unit": draft.unit, "observed_period": draft.observed_period.isoformat() if draft.observed_period else None, "scope": draft.scope, "run_ref": run_ref},
            validation_result={"quote_continuous": True, "quote_sha256": quote_sha256},
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(candidate)
        self._session.flush()
        return candidate

    def review(self, candidate_id, *, outcome: str, reviewer: str, reason: str, idempotency_key: str) -> AtomicClaimReview:
        if self._session.get(AtomicClaimCandidate, candidate_id) is None:
            raise ValidationError("atomic claim candidate not found")
        if outcome not in _REVIEW_OUTCOMES or not reviewer.strip() or not reason.strip() or not idempotency_key.strip():
            raise ValidationError("atomic claim review is incomplete or invalid")
        existing = self._session.scalar(select(AtomicClaimReview).where(AtomicClaimReview.atomic_claim_candidate_id == candidate_id, AtomicClaimReview.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
        review = AtomicClaimReview(atomic_claim_candidate_id=candidate_id, outcome=outcome, reviewer=reviewer.strip(), reason=reason.strip(), idempotency_key=idempotency_key.strip(), created_at=datetime.now(timezone.utc))
        self._session.add(review)
        self._session.flush()
        return review
