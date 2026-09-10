"""Typed input contract for candidates extracted from frozen source spans."""
from __future__ import annotations

import re
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime


def normalize_claim_period(value: date | str | None) -> str | None:
    """Validate a disclosed ISO period without increasing its precision."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        raise TypeError("atomic claim period must be an ISO year, month or date")
    if re.fullmatch(r"[0-9]{4}", value):
        date(int(value), 1, 1)  # Validate the year; retain the original grain.
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        date(int(value[:4]), int(value[5:7]), 1)
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        date.fromisoformat(value)
    else:
        raise ValueError("atomic claim period must be an ISO year, month or date")
    return value


def claim_period_end(value: date | str | None) -> date | None:
    """Apply the ledger's existing period-end projection only on publication.

    The candidate and automatic semantic audit keep the precise source grain;
    this date is a storage convention, not a newly asserted observation date.
    """
    period = normalize_claim_period(value)
    if period is None:
        return None
    if len(period) == 4:
        return date(int(period), 12, 31)
    if len(period) == 7:
        year, month = int(period[:4]), int(period[5:7])
        return date(year, month, monthrange(year, month)[1])
    return date.fromisoformat(period)


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
    observed_period: date | str | None
    scope: dict[str, str]
