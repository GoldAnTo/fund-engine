"""Secret-safe internal contract for governed acquisition source adapters.

This module defines values and behavior only.  It deliberately performs no
network I/O; concrete providers own transport and mapping behind SourceAdapter.
"""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import parse_qsl, urlsplit

from app.domain.acquisition import ACQUISITION_SOURCE_ROLES


_FORBIDDEN_KEY_PARTS: Final = (
    "authorization",
    "credential",
    "password",
    "secret",
    "cookie",
    "apikey",
    "token",
)
_SECRET_TEXT_RE: Final = re.compile(
    r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie)\s*:|"
    r"(?:token|api[\s_-]*key|password|secret|credential)\s*[:=]|"
    r"\b(?:basic|bearer)\s+\S+"
)
_SCHEME_RE: Final = re.compile(r"^[a-z][a-z0-9+.-]*$")
_CANONICAL_URL_RE: Final = re.compile(
    r"^(?P<scheme>[a-z][a-z0-9+.-]*)://"
    r"(?P<authority>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)/"
    r"(?P<segment>[A-Za-z0-9][A-Za-z0-9:._~-]*)$"
)


def _required_string(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _forbidden_key(key: str) -> bool:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return any(part in compact for part in _FORBIDDEN_KEY_PARTS)


def _header_container_key(key: str) -> bool:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return "header" in compact


def _contains_secret_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return _SECRET_TEXT_RE.search(normalized) is not None


def _contains_ascii_control(value: str, *, allow_text_layout: bool = False) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_text_layout else set()
    return any(
        (ord(character) < 32 or ord(character) == 127) and character not in allowed
        for character in value
    )


def _freeze_safe(value: Any, path: str) -> Any:
    """Validate JSON-like metadata and recursively make it immutable."""
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metadata keys must be non-blank strings")
            if isinstance(nested, (Mapping, list, tuple)) and _header_container_key(
                key
            ):
                raise ValueError("forbidden metadata container")
            if _forbidden_key(key):
                raise ValueError("forbidden metadata key")
            frozen[key] = _freeze_safe(nested, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_safe(nested, f"{path}[{index}]")
            for index, nested in enumerate(value)
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _contains_secret_text(value):
            raise ValueError("forbidden credential value")
        return value
    raise ValueError("metadata must contain only JSON-safe structured values")


def _safe_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return _freeze_safe(value, field_name)


def _canonical_host(value: str) -> str:
    host = _required_string(value, "allowed_hosts").casefold().rstrip(".")
    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        raise ValueError("allowed_hosts must contain valid exact hosts")
    return host


def _url_has_credentials(url: str) -> bool:
    if (
        url != url.strip()
        or _contains_ascii_control(url)
        or "#" in url
        or re.search(r"%(?![0-9A-Fa-f]{2})", url)
    ):
        return True
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        return True
    if not parsed.scheme or parsed.username is not None or parsed.password is not None:
        return True
    if parsed.fragment:
        return True
    if _contains_secret_text(url):
        return True
    return any(
        _forbidden_key(unicodedata.normalize("NFKC", key))
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    )


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """An adapter identity and its exact canonical-URL trust boundary."""

    adapter_key: str
    provider_identity: str
    allowed_schemes: frozenset[str]
    allowed_hosts: frozenset[str]
    allowed_source_roles: frozenset[str] = ACQUISITION_SOURCE_ROLES

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "adapter_key", _required_string(self.adapter_key, "adapter_key")
        )
        object.__setattr__(
            self,
            "provider_identity",
            _required_string(self.provider_identity, "provider_identity"),
        )
        schemes = frozenset(
            _required_string(value, "allowed_schemes").casefold()
            for value in self.allowed_schemes
        )
        if not schemes or any(_SCHEME_RE.fullmatch(value) is None for value in schemes):
            raise ValueError("allowed_schemes must contain valid URL schemes")
        object.__setattr__(self, "allowed_schemes", schemes)
        hosts = frozenset(_canonical_host(value) for value in self.allowed_hosts)
        if not hosts:
            raise ValueError("allowed_hosts must not be empty")
        object.__setattr__(self, "allowed_hosts", hosts)
        roles = frozenset(
            _required_string(value, "allowed_source_roles")
            for value in self.allowed_source_roles
        )
        if not roles or not roles <= ACQUISITION_SOURCE_ROLES:
            raise ValueError("allowed_source_roles contains a disallowed role")
        object.__setattr__(self, "allowed_source_roles", roles)

    def validate_reference(self, reference: SourceReferenceValue) -> None:
        if reference.adapter_key != self.adapter_key:
            raise ValueError("reference adapter_key does not match descriptor")
        if reference.source_role not in self.allowed_source_roles:
            raise ValueError("reference source_role is not allowed by descriptor")
        raw_url = reference.canonical_url
        match = None if _contains_ascii_control(raw_url) else _CANONICAL_URL_RE.fullmatch(
            raw_url
        )
        if (
            match is None
            or match.group("scheme") not in self.allowed_schemes
            or match.group("authority") not in self.allowed_hosts
            or match.group("segment") in {".", ".."}
        ):
            error = ValueError(
                "canonical_url is outside the adapter URL boundary"
            )
            error.__context__ = None
            raise error from None


@dataclass(frozen=True, slots=True)
class SourceReferenceValue:
    adapter_key: str
    external_record_id: str
    external_version: str
    canonical_url: str
    title: str
    published_at: datetime
    source_role: str
    fetch_locator: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.canonical_url, str)
            or self.canonical_url != self.canonical_url.strip()
            or _contains_ascii_control(self.canonical_url)
        ):
            raise ValueError("canonical_url contains unsafe raw characters")
        for field_name in (
            "adapter_key",
            "external_record_id",
            "external_version",
            "canonical_url",
            "title",
            "source_role",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_string(getattr(self, field_name), field_name),
            )
        if self.source_role not in ACQUISITION_SOURCE_ROLES:
            raise ValueError("source_role is not allowed")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))
        object.__setattr__(
            self,
            "fetch_locator",
            _safe_mapping(self.fetch_locator, "fetch_locator"),
        )
        metadata = _safe_mapping(self.metadata, "metadata")
        provider_identity = metadata.get("provider_identity") or metadata.get("publisher")
        if not isinstance(provider_identity, str) or not provider_identity.strip():
            raise ValueError("metadata must contain provider identity")
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True, slots=True)
class RejectedSearchItem:
    """A provider row that must be recorded but cannot become a reference."""

    adapter_key: str
    reason: str
    external_record_id: str
    title: str
    published_at: datetime | None
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in ("adapter_key", "reason", "external_record_id"):
            object.__setattr__(
                self,
                field_name,
                _required_string(getattr(self, field_name), field_name),
            )
        if not isinstance(self.title, str):
            raise ValueError("title must be a string")
        object.__setattr__(self, "title", self.title.strip())
        if self.published_at is not None:
            if (
                self.published_at.tzinfo is None
                or self.published_at.utcoffset() is None
            ):
                raise ValueError("published_at must be timezone-aware when present")
            object.__setattr__(
                self, "published_at", self.published_at.astimezone(UTC)
            )
        object.__setattr__(self, "metadata", _safe_mapping(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class RetrievedEnvelope:
    content: bytes
    mime_type: str
    final_url: str
    etag: str | None
    last_modified: str | None
    provider_request_id: str | None
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("content must be non-empty bytes")
        if (
            not isinstance(self.final_url, str)
            or self.final_url != self.final_url.strip()
            or _contains_ascii_control(self.final_url)
        ):
            error = ValueError("final_url is malformed or contains credentials")
            error.__context__ = None
            raise error from None
        object.__setattr__(
            self, "mime_type", _required_string(self.mime_type, "mime_type")
        )
        object.__setattr__(
            self, "final_url", _required_string(self.final_url, "final_url")
        )
        if _url_has_credentials(self.final_url):
            error = ValueError("final_url is malformed or contains credentials")
            error.__context__ = None
            raise error from None
        for field_name in ("etag", "last_modified", "provider_request_id"):
            value = getattr(self, field_name)
            if value is not None:
                safe_value = _required_string(value, field_name)
                if _contains_secret_text(safe_value):
                    error = ValueError(
                        f"{field_name} contains forbidden credential text"
                    )
                    error.__context__ = None
                    raise error from None
                object.__setattr__(self, field_name, safe_value)
        object.__setattr__(self, "metadata", _safe_mapping(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class RetrievedSearchResult:
    """A transient provider body that must be checkpointed with its reference."""

    reference: SourceReferenceValue
    envelope: RetrievedEnvelope

    def __post_init__(self) -> None:
        if not isinstance(self.reference, SourceReferenceValue):
            raise TypeError("reference must be a SourceReferenceValue")
        if not isinstance(self.envelope, RetrievedEnvelope):
            raise TypeError("envelope must be a RetrievedEnvelope")
        adapter_key = self.envelope.metadata.get("adapter_key")
        if adapter_key is not None and adapter_key != self.reference.adapter_key:
            raise ValueError("retrieved search adapter identity mismatch")
        record_id = self.envelope.metadata.get("external_record_id")
        if record_id is not None and record_id != self.reference.external_record_id:
            raise ValueError("retrieved search provider identity mismatch")


SearchItem = SourceReferenceValue | RejectedSearchItem | RetrievedSearchResult


class SourceUnavailable(Exception):
    """A sanitized provider failure with an explicit retry decision."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        safe_message = _required_string(message, "message")
        if _contains_secret_text(safe_message):
            safe_message = "source provider request failed"
        super().__init__(safe_message)
        self.retryable = bool(retryable)
        self.diagnostics = _safe_mapping(diagnostics or {}, "diagnostics")


class SourceAdapter(ABC):
    """Narrow lifecycle contract implemented by each governed source."""

    @property
    @abstractmethod
    def descriptor(self) -> SourceDescriptor:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, cutoff: datetime) -> tuple[SearchItem, ...]:
        raise NotImplementedError

    def restore_reference(self, reference: SourceReferenceValue) -> None:
        """Hydrate safe adapter state from a durable reference or fail closed."""
        self.descriptor.validate_reference(reference)
        raise ValueError("adapter does not support persisted reference recovery")

    @abstractmethod
    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
