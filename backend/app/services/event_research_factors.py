"""Validation shared by event creation and later scope revisions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventResearchScopeFactorValue:
    statement: str
    description: str | None = None


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


def normalize_event_research_scope_factors(
    factors: list[str | EventResearchScopeFactorValue],
) -> list[EventResearchScopeFactorValue]:
    """Normalize legacy strings alongside explanation-bearing scope factors."""
    statements = normalize_event_research_factors([
        factor if isinstance(factor, str) else factor.statement for factor in factors
    ])
    normalized: list[EventResearchScopeFactorValue] = []
    for statement, factor in zip(statements, factors, strict=True):
        description = None if isinstance(factor, str) else factor.description
        normalized.append(EventResearchScopeFactorValue(
            statement=statement,
            description=description.strip() if description and description.strip() else None,
        ))
    return normalized
