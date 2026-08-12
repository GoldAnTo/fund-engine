"""Governed acquisition adapter for licensed Gildata text materials."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    SearchItem,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.datasources.gildata.adapters import (
    fetch_announcement,
    fetch_research_report,
    parse_table_markdown_payload,
)
from app.datasources.gildata.client import GildataMCPClient, GildataMCPError


DEFAULT_MAX_CACHE_BYTES: Final = 20 * 1024 * 1024
_DESCRIPTOR: Final = SourceDescriptor(
    adapter_key="gildata",
    provider_identity="Gildata",
    allowed_schemes=frozenset({"gildata"}),
    allowed_hosts=frozenset({"research-report", "announcement"}),
    allowed_source_roles=frozenset({"licensed_provider"}),
)
_SAFE_SECURITY_CODE_RE: Final = re.compile(
    r"^[A-Za-z0-9]{1,16}(?:[.-][A-Za-z0-9]{1,16})?$"
)
_SECRET_TEXT_RE: Final = re.compile(
    r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie)\s*:|"
    r"(?:token|api[\s_-]*key|password|secret|credential)\s*[:=]|"
    r"\bbearer\s+\S+"
)


CacheKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    reference: SourceReferenceValue
    payload: bytes


class _StrictGildataClient:
    """Validate the Gildata inner envelope before existing parsers consume it."""

    def __init__(self, client: GildataMCPClient) -> None:
        self._client = client

    def call_tool(
        self, name: str, arguments: dict, timeout: float = 60
    ) -> str:
        text = self._client.call_tool(name, arguments, timeout=timeout)
        try:
            inner = json.loads(text)
        except (TypeError, ValueError):
            raise GildataMCPError("invalid Gildata response envelope") from None
        if (
            not isinstance(inner, dict)
            or type(inner.get("code")) not in {int, str}
            or inner.get("code") not in {0, "0"}
            or not isinstance(inner.get("results"), list)
        ):
            raise GildataMCPError("invalid Gildata response envelope") from None
        for result in inner["results"]:
            if not isinstance(result, Mapping):
                raise GildataMCPError("invalid Gildata response envelope") from None
            table_markdown = result.get("table_markdown")
            if not isinstance(table_markdown, str) or not table_markdown.strip():
                raise GildataMCPError("invalid Gildata response envelope") from None
            try:
                raw_table = json.loads(table_markdown)
            except (TypeError, ValueError):
                raw_table = None
            else:
                if isinstance(raw_table, list):
                    if any(not isinstance(row, Mapping) for row in raw_table):
                        raise GildataMCPError(
                            "invalid Gildata response envelope"
                        ) from None
                elif not isinstance(raw_table, Mapping):
                    raise GildataMCPError(
                        "invalid Gildata response envelope"
                    ) from None
            rows = parse_table_markdown_payload(table_markdown)
            if not rows or any(
                not isinstance(row, Mapping)
                or not row
                or any(
                    not isinstance(key, str)
                    or not key.strip()
                    or not isinstance(value, str)
                    for key, value in row.items()
                )
                or not any(value.strip() for value in row.values())
                for row in rows
            ):
                raise GildataMCPError("invalid Gildata response envelope") from None
        return text


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _payload_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _normalize_identity_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return " ".join(normalized.split())


def _contains_ascii_control(value: str, *, allow_text_layout: bool = False) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_text_layout else set()
    return any(
        (ord(character) < 32 or ord(character) == 127) and character not in allowed
        for character in value
    )


def _contains_secret_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return _SECRET_TEXT_RE.search(normalized) is not None


def _safe_provider_text(value: str, *, allow_text_layout: bool = False) -> bool:
    return not _contains_ascii_control(
        value, allow_text_layout=allow_text_layout
    ) and not _contains_secret_text(value)


def _display_title(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", _text(value)).split())


def _publication(value: object) -> tuple[str | None, datetime | None, str]:
    """Return source-calendar date, UTC instant, and fixed rejection marker."""
    raw = _text(value)
    if not raw:
        return None, None, "missing-date"
    if not _safe_provider_text(raw):
        return None, None, "invalid-date"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, None, "invalid-date"
    source_date = parsed.date().isoformat()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return source_date, parsed.astimezone(UTC), source_date


def _safe_security_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if (
        _safe_provider_text(normalized)
        and _SAFE_SECURITY_CODE_RE.fullmatch(normalized) is not None
    ):
        return normalized
    return "unknown"


def _digest(parts: list[str]) -> str:
    material = json.dumps(
        [_normalize_identity_text(part) for part in parts],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def _opaque_row_digest(row: Mapping[str, Any]) -> str:
    try:
        serialized = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        serialized = json.dumps(
            sorted((str(key), type(value).__name__) for key, value in row.items()),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _stable_id(
    *,
    prefix: str,
    source_type: str,
    security_code: str,
    title: str,
    date_marker: str,
    provider_identity: str,
    issuer_identity: str = "",
) -> str:
    safe_code = _safe_security_code(security_code)
    hash_code = security_code if safe_code != "unknown" else "unknown"
    return (
        f"{prefix}:{safe_code}:{date_marker}:"
        f"{_digest([source_type, hash_code, title, date_marker, provider_identity, issuer_identity])}"
    )


def _cache_key(reference: SourceReferenceValue) -> CacheKey:
    return (
        reference.adapter_key,
        reference.external_record_id,
        reference.external_version,
    )


class GildataResearchSource(SourceAdapter):
    """Map complete licensed Gildata payloads into cache-backed references."""

    def __init__(
        self,
        client: GildataMCPClient,
        *,
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        if not isinstance(max_cache_bytes, int) or isinstance(max_cache_bytes, bool):
            raise ValueError("max_cache_bytes must be a positive integer")
        if max_cache_bytes <= 0:
            raise ValueError("max_cache_bytes must be greater than zero")
        self._client = client
        self._strict_client = _StrictGildataClient(client)
        self._max_cache_bytes = max_cache_bytes
        self._cache_bytes = 0
        self._payloads: dict[CacheKey, _CacheEntry] = {}
        self._conflicts: set[CacheKey] = set()

    @property
    def descriptor(self) -> SourceDescriptor:
        return _DESCRIPTOR

    def search(self, query: str, cutoff: datetime) -> tuple[SearchItem, ...]:
        normalized_query = _text(query)
        if not normalized_query:
            raise ValueError("query must not be blank")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        utc_cutoff = cutoff.astimezone(UTC)

        operation_results: list[tuple[SearchItem, bytes | None]] = []
        completed_operations = 0
        operation_errors: list[SourceUnavailable] = []
        operations: tuple[
            tuple[
                str,
                str,
                Callable[[Any, str], list[dict]],
                Callable[[dict[str, Any], datetime], tuple[SearchItem, bytes | None]],
            ],
            ...,
        ] = (
            (
                "research_report",
                "research_report",
                fetch_research_report,
                self._map_report,
            ),
            (
                "announcement",
                "announcement",
                fetch_announcement,
                self._map_announcement,
            ),
        )
        for operation, source_type, fetcher, mapper in operations:
            mapped_rows: list[tuple[SearchItem, bytes | None]] | None = None
            unavailable: SourceUnavailable | None = None
            try:
                rows = fetcher(self._strict_client, normalized_query)
                mapped_rows = [mapper(row, utc_cutoff) for row in rows]
            except (GildataMCPError, UnicodeError, TypeError, ValueError):
                unavailable = SourceUnavailable(
                    "Gildata provider request failed",
                    retryable=True,
                    diagnostics={
                        "adapter_key": self.descriptor.adapter_key,
                        "operation": operation,
                        "error_type": "GildataMCPError",
                    },
                )
            if unavailable is not None:
                operation_errors.append(unavailable)
                operation_results.append(
                    (self._operation_rejection(operation, source_type), None)
                )
                continue
            completed_operations += 1
            assert mapped_rows is not None
            operation_results.extend(mapped_rows)

        if completed_operations == 0 and operation_errors:
            raise operation_errors[0]
        return self._resolve_results(operation_results)

    def _operation_rejection(
        self, operation: str, source_type: str
    ) -> RejectedSearchItem:
        external_id = (
            f"operation:{operation}:unavailable:"
            f"{_digest([self.descriptor.adapter_key, operation, 'unavailable'])}"
        )
        return RejectedSearchItem(
            adapter_key=self.descriptor.adapter_key,
            reason="source_unavailable",
            external_record_id=external_id,
            title="Provider operation unavailable",
            published_at=None,
            metadata={
                "error_class": "provider_operation_failure",
                "operation": operation,
                "provider_identity": self.descriptor.provider_identity,
                "retryable": True,
                "source_type": source_type,
            },
        )

    def _map_report(
        self, row: dict[str, Any], cutoff: datetime
    ) -> tuple[SearchItem, bytes | None]:
        title = _display_title(row.get("title"))
        body = _payload_text(row.get("content"))
        security_code = _text(row.get("sec_code"))
        provider_identity = _text(row.get("org"))
        author = _text(row.get("author"))
        return self._map_row(
            cutoff=cutoff,
            source_type="research_report",
            prefix="report",
            url_host="research-report",
            title=title,
            publication_raw=row.get("publish_date"),
            body=body,
            security_code=security_code,
            provider_identity=provider_identity,
            metadata={
                "source_type": "research_report",
                "security_code": _safe_security_code(security_code),
                "publisher": provider_identity,
                "author": author,
            },
            identity_values=(title, security_code, provider_identity, author, body),
            unsafe_row_digest=_opaque_row_digest(row),
            require_document_publisher=True,
        )

    def _map_announcement(
        self, row: dict[str, Any], cutoff: datetime
    ) -> tuple[SearchItem, bytes | None]:
        title = _display_title(row.get("title"))
        body = _payload_text(row.get("content"))
        security_code = _text(row.get("stock_code"))
        security_name = _text(row.get("sec_name"))
        issuer_identity = _normalize_identity_text(security_name)
        return self._map_row(
            cutoff=cutoff,
            source_type="announcement",
            prefix="announcement",
            url_host="announcement",
            title=title,
            publication_raw=row.get("publish_date"),
            body=body,
            security_code=security_code,
            provider_identity=self.descriptor.provider_identity,
            metadata={
                "source_type": "announcement",
                "security_code": _safe_security_code(security_code),
                "security_name": security_name,
                "issuer_identity": issuer_identity or "unknown",
                "provider_identity": self.descriptor.provider_identity,
            },
            identity_values=(title, security_code, security_name, body),
            unsafe_row_digest=_opaque_row_digest(row),
            require_document_publisher=False,
            issuer_identity=issuer_identity,
            require_issuer_discriminator=True,
        )

    def _map_row(
        self,
        *,
        cutoff: datetime,
        source_type: str,
        prefix: str,
        url_host: str,
        title: str,
        publication_raw: object,
        body: str,
        security_code: str,
        provider_identity: str,
        metadata: dict[str, Any],
        identity_values: tuple[str, ...],
        unsafe_row_digest: str,
        require_document_publisher: bool,
        issuer_identity: str = "",
        require_issuer_discriminator: bool = False,
    ) -> tuple[SearchItem, bytes | None]:
        raw_date = _text(publication_raw)
        source_date, published_at, date_marker = _publication(publication_raw)
        all_provider_values = (*identity_values, raw_date)
        unsafe = any(
            not _safe_provider_text(
                value, allow_text_layout=value is body
            )
            for value in all_provider_values
        )
        if unsafe:
            external_id = (
                f"{prefix}:unknown:invalid-date:"
                f"{unsafe_row_digest}"
            )
            return (
                RejectedSearchItem(
                    adapter_key=self.descriptor.adapter_key,
                    reason="unsafe_provider_data",
                    external_record_id=external_id,
                    title="Rejected provider item",
                    published_at=None,
                    metadata={
                        "source_type": source_type,
                        "security_code": "unknown",
                        "provider_identity": self.descriptor.provider_identity,
                    },
                ),
                None,
            )

        external_id = _stable_id(
            prefix=prefix,
            source_type=source_type,
            security_code=security_code,
            title=title,
            date_marker=date_marker,
            provider_identity=provider_identity,
            issuer_identity=issuer_identity,
        )
        safe_code = _safe_security_code(security_code)
        reject_metadata = {
            "source_type": source_type,
            "security_code": safe_code,
            "provider_identity": provider_identity
            or self.descriptor.provider_identity,
        }
        if require_issuer_discriminator:
            reject_metadata["issuer_identity"] = issuer_identity or "unknown"
        reason: str | None = None
        if not raw_date:
            reason = "missing_publication_date"
        elif published_at is None:
            reason = "invalid_publication_date"
        elif not body.strip():
            reason = "missing_body"
        elif require_document_publisher and not provider_identity:
            reason = "missing_provider_identity"
        elif (
            require_issuer_discriminator
            and safe_code == "unknown"
            and not issuer_identity
        ):
            reason = "missing_provider_identity"
        elif not title:
            reason = "missing_title"
        elif published_at > cutoff:
            reason = "after_cutoff"
        if reason is not None:
            return (
                RejectedSearchItem(
                    adapter_key=self.descriptor.adapter_key,
                    reason=reason,
                    external_record_id=external_id,
                    title=title,
                    published_at=published_at,
                    metadata=reject_metadata,
                ),
                None,
            )

        assert source_date is not None and published_at is not None
        reference = SourceReferenceValue(
            adapter_key=self.descriptor.adapter_key,
            external_record_id=external_id,
            external_version=f"published:{source_date}",
            canonical_url=f"gildata://{url_host}/{external_id}",
            title=title,
            published_at=published_at,
            source_role="licensed_provider",
            fetch_locator={"record_id": external_id},
            metadata=metadata,
        )
        self.descriptor.validate_reference(reference)
        return reference, body.encode("utf-8")

    def _resolve_results(
        self, candidates: list[tuple[SearchItem, bytes | None]]
    ) -> tuple[SearchItem, ...]:
        rejected: dict[tuple[str, str], RejectedSearchItem] = {}
        accepted: dict[CacheKey, list[tuple[SourceReferenceValue, bytes]]] = {}
        for item, payload in candidates:
            if isinstance(item, RejectedSearchItem):
                rejected.setdefault((item.reason, item.external_record_id), item)
            else:
                assert payload is not None
                accepted.setdefault(_cache_key(item), []).append((item, payload))

        resolved: list[SearchItem] = list(rejected.values())
        for key in sorted(accepted):
            variants = accepted[key]
            payloads = {payload for _reference, payload in variants}
            cached = self._payloads.get(key)
            if key in self._conflicts:
                resolved.append(
                    self._derived_rejection(variants[0][0], "variant_conflict")
                )
                continue
            if len(payloads) != 1 or (
                cached is not None and cached.payload not in payloads
            ):
                self._conflicts.add(key)
                resolved.append(
                    self._derived_rejection(variants[0][0], "variant_conflict")
                )
                continue
            payload = next(iter(payloads))
            if cached is not None:
                resolved.append(cached.reference)
                continue
            reference = min(variants, key=lambda value: repr(value[0]))[0]
            if len(payload) > self._max_cache_bytes - self._cache_bytes:
                resolved.append(self._derived_rejection(reference, "payload_too_large"))
                continue
            self._payloads[key] = _CacheEntry(reference=reference, payload=payload)
            self._cache_bytes += len(payload)
            resolved.append(reference)
        return tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.external_record_id,
                    0 if isinstance(item, SourceReferenceValue) else 1,
                    item.reason if isinstance(item, RejectedSearchItem) else "",
                ),
            )
        )

    def _derived_rejection(
        self, reference: SourceReferenceValue, reason: str
    ) -> RejectedSearchItem:
        return RejectedSearchItem(
            adapter_key=reference.adapter_key,
            reason=reason,
            external_record_id=reference.external_record_id,
            title=reference.title,
            published_at=reference.published_at,
            metadata={
                "source_type": reference.metadata["source_type"],
                "security_code": reference.metadata.get("security_code", "unknown"),
                "provider_identity": reference.metadata.get("provider_identity")
                or reference.metadata.get("publisher"),
            },
        )

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        entry = self._payloads.get(_cache_key(reference))
        if entry is None or entry.reference != reference:
            error = ValueError("unknown or mismatched Gildata source reference")
            error.__context__ = None
            raise error from None
        self.descriptor.validate_reference(reference)
        provider_identity = reference.metadata.get(
            "provider_identity"
        ) or reference.metadata.get("publisher")
        return RetrievedEnvelope(
            content=entry.payload,
            mime_type="text/plain; charset=utf-8",
            final_url=reference.canonical_url,
            etag=None,
            last_modified=None,
            provider_request_id=None,
            metadata={
                "adapter_key": self.descriptor.adapter_key,
                "external_record_id": reference.external_record_id,
                "source_type": reference.metadata["source_type"],
                "provider_identity": provider_identity,
            },
        )

    def close(self) -> None:
        self._payloads.clear()
        self._conflicts.clear()
        self._cache_bytes = 0
        self._client.close()
