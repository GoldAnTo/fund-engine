"""Typed input contract for candidates extracted from frozen source spans."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class AtomicClaimDraft:
    source_span_id: uuid.UUID
    quote: str
    quote_start: int
    quote_end: int
    normalized_text: str
    claim_type: str
    assertion_actor: str | None
    subject: str | None
    predicate: str | None
    object_text: str | None
    numeric_value: str | None
    unit: str | None
    observed_period: date | None
    scope: dict[str, str]
