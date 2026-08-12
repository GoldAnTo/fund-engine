"""Datetime normalization at API projection boundaries."""
from __future__ import annotations

from datetime import UTC, datetime


def api_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
