"""Validation shared by event creation and later scope revisions."""
from __future__ import annotations


def normalize_event_research_factors(factors: list[str]) -> list[str]:
    """Return a bounded, trimmed, non-duplicate factor set."""
    if not 3 <= len(factors) <= 5:
        raise ValueError("event research factors require 3 to 5 items")
    normalized = [factor.strip() for factor in factors]
    if any(not factor for factor in normalized):
        raise ValueError("event research factors must not be blank")
    if len(set(normalized)) != len(normalized):
        raise ValueError("event research factors must be unique after trimming whitespace")
    return normalized
