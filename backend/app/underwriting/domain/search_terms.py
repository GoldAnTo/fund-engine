"""Auditable normalization contract for research-object discovery terms.

Supported writers must use this module. Database checks intentionally cover only
structural and printable-ASCII mismatches because SQLite and PostgreSQL cannot
both enforce Unicode NFC + lowercase semantics. Raw/Core non-ASCII writes are a
trusted administrator bypass; discovery revalidates every selected persisted row
and fails closed instead of accepting a corrupt projection.
"""

from __future__ import annotations

import unicodedata


class SearchTermIntegrityError(RuntimeError):
    """A persisted derived search term does not match its authoritative value."""


def normalize_search_term(value: str) -> str:
    """Return the cross-dialect NFC + trim + lower search key."""
    return unicodedata.normalize("NFC", value.strip()).lower()


def require_valid_search_term(raw_value: str, normalized_value: str) -> None:
    """Fail closed when a selected persisted projection is not canonical."""
    if normalized_value != normalize_search_term(raw_value):
        raise SearchTermIntegrityError(
            "persisted research search term normalization conflict"
        )
