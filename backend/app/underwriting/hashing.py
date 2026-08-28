"""Dependency-free canonical hashing for underwriting immutable payloads."""

from __future__ import annotations

import hashlib
import json


def canonical_hash(value: object) -> str:
    """Return the stable SHA-256 digest for a JSON-compatible value."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
