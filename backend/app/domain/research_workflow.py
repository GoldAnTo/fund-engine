"""Closed workflow projection policy shared by query and wire adapters."""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


PROTOCOL_DECISION_FIELDS = frozenset(
    {
        "action",
        "scope_version_id",
        "scope_version",
        "blocked_thesis_ids",
        "reason_codes",
        "blocked_theses",
        "missing",
        "attempted",
        "cannot_continue_reason",
        "recommendation",
        "alternatives",
    }
)


def as_utc(value: datetime | None) -> datetime | None:
    """Interpret naive persisted timestamps as UTC and convert aware values."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return as_utc(parsed)


def _nonblank(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _typed_option(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != {"kind", "label", "impact"}:
        return None
    if any(_nonblank(value.get(field)) is None for field in ("kind", "label", "impact")):
        return None
    return dict(value)


@dataclass(frozen=True, slots=True)
class WorkflowDecision:
    reason: str
    recommendation: dict[str, object]
    alternatives: tuple[dict[str, object], ...]
    payload: dict[str, object]

    @property
    def impact(self) -> str:
        return str(self.recommendation["impact"])


def classify_user_decision(
    kind: str | None,
    payload: object,
) -> WorkflowDecision | None:
    """Return only complete persisted decision facts; never manufacture advice."""
    if not kind or not isinstance(payload, Mapping):
        return None
    if kind == "complete_research_protocol":
        if set(payload) != PROTOCOL_DECISION_FIELDS:
            return None
        if payload.get("action") != "complete_research_protocol":
            return None
        scope_version = payload.get("scope_version")
        if (
            not isinstance(scope_version, int)
            or isinstance(scope_version, bool)
            or scope_version < 1
        ):
            return None
        try:
            uuid.UUID(str(payload.get("scope_version_id")))
        except (TypeError, ValueError, AttributeError):
            return None
        for field in (
            "blocked_thesis_ids",
            "blocked_theses",
            "missing",
            "attempted",
        ):
            if not isinstance(payload.get(field), list) or not payload[field]:
                return None
        if not isinstance(payload.get("reason_codes"), Mapping):
            return None
        reason = _nonblank(payload.get("cannot_continue_reason"))
    else:
        reason = _nonblank(payload.get("cannot_continue_reason")) or _nonblank(
            payload.get("reason")
        )
    recommendation = _typed_option(payload.get("recommendation"))
    raw_alternatives = payload.get("alternatives")
    if (
        reason is None
        or recommendation is None
        or not isinstance(raw_alternatives, list)
        or len(raw_alternatives) < 2
    ):
        return None
    alternatives = tuple(_typed_option(item) for item in raw_alternatives)
    if not alternatives or any(item is None for item in alternatives):
        return None
    return WorkflowDecision(
        reason=reason,
        recommendation=recommendation,
        alternatives=tuple(item for item in alternatives if item is not None),
        payload=dict(payload),
    )


LedgerKey = tuple[datetime, str, str]


@dataclass(frozen=True, slots=True)
class WorkflowLedgerCursor:
    status: str | None
    after: LedgerKey
    high_watermark: LedgerKey


def ledger_key(
    recorded_at: datetime,
    record_type: str,
    record_id: uuid.UUID,
) -> LedgerKey:
    normalized = as_utc(recorded_at)
    assert normalized is not None
    return normalized, record_type, str(record_id)


def _wire_key(value: LedgerKey) -> list[str]:
    return [value[0].isoformat(), value[1], value[2]]


def _parse_key(value: object) -> LedgerKey:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("invalid ledger cursor key")
    timestamp = parse_utc_datetime(value[0])
    if timestamp is None or not all(isinstance(item, str) for item in value[1:]):
        raise ValueError("invalid ledger cursor key")
    uuid.UUID(value[2])
    return timestamp, value[1], value[2]


def encode_ledger_cursor(cursor: WorkflowLedgerCursor) -> str:
    raw = json.dumps(
        {
            "v": 1,
            "status": cursor.status,
            "after": _wire_key(cursor.after),
            "high_watermark": _wire_key(cursor.high_watermark),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_ledger_cursor(value: str) -> WorkflowLedgerCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(value + padding))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("invalid ledger cursor version")
        status = payload.get("status")
        if status is not None and not isinstance(status, str):
            raise ValueError("invalid ledger cursor status")
        return WorkflowLedgerCursor(
            status=status,
            after=_parse_key(payload.get("after")),
            high_watermark=_parse_key(payload.get("high_watermark")),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid workflow ledger cursor") from exc
