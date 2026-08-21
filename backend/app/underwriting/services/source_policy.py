"""Authorization and reproducibility gates for frozen underwriting sources."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import re

from app.models.ledger import ValidationError


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_DEFAULT_PROVIDER_CAPABILITIES = frozenset({"public_http"})
_DEFAULT_RETENTION_MODES = frozenset({"hash_locator_and_derived_observations"})


class AuthorizationState(StrEnum):
    AUTHORIZED = "authorized"
    REFERENCE_ONLY = "reference_only"
    FORBIDDEN = "forbidden"


class DisplayPolicy(StrEnum):
    PUBLIC_EXCERPT = "public_excerpt"
    DERIVED_ONLY = "derived_only"
    METADATA_ONLY = "metadata_only"


@dataclass(frozen=True, slots=True)
class SourcePolicyDecision:
    source_id: str
    authorization: AuthorizationState
    display_policy: DisplayPolicy
    reproducible: bool
    reasons: tuple[str, ...]


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValidationError(f"{field} must not be empty")
    return normalized


def _required_retention(source: Mapping[str, object]) -> str:
    value = source.get("retention", source.get("required_retention_mode"))
    return _require_text(value, "retention")


def _required_capabilities(source: Mapping[str, object]) -> tuple[str, ...]:
    singular = source.get("provider_capability")
    plural = source.get("provider_capabilities")
    if singular is not None and plural is not None:
        raise ValidationError("provider capability must be specified once")
    if singular is not None:
        return (_require_text(singular, "provider_capability"),)
    if plural is None:
        raise ValidationError("provider_capability must not be empty")
    if isinstance(plural, str):
        return (_require_text(plural, "provider_capability"),)
    if not isinstance(plural, (list, tuple, set, frozenset)):
        raise ValidationError("provider_capabilities must be a list")
    values = tuple(_require_text(value, "provider_capability") for value in plural)
    if not values:
        raise ValidationError("provider_capabilities must not be empty")
    return values


def _available_capabilities(value: Iterable[str] | Mapping[str, bool]) -> frozenset[str]:
    if isinstance(value, Mapping):
        return frozenset(name for name, available in value.items() if available is True)
    return frozenset(value)


def evaluate_source_policy(
    source: Mapping[str, object],
    *,
    available_provider_capabilities: Iterable[str] | Mapping[str, bool] = _DEFAULT_PROVIDER_CAPABILITIES,
    supported_retention_modes: Iterable[str] = _DEFAULT_RETENTION_MODES,
) -> SourcePolicyDecision:
    """Validate whether one source may enter an immutable source manifest.

    A source that fails one of these gates is rejected rather than represented
    as a permissive-but-unusable record.  This keeps later model compilation
    from accidentally treating a metadata-only or irreproducible source as
    formal evidence.
    """
    if not isinstance(source, Mapping):
        raise ValidationError("source must be an object")

    source_id = _require_text(source.get("source_id"), "source_id")
    locator = _require_text(source.get("locator"), "locator")
    del locator  # Validation is intentional; normalized storage happens in source_freeze.
    for field in ("published_at", "first_available_at", "retrieved_at"):
        value = source.get(field)
        if value is None:
            raise ValidationError(f"{field} is required")
        _require_text(value, field)

    digest = source.get("content_sha256")
    if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
        raise ValidationError("content_sha256 must be a sha256 hex digest")

    try:
        authorization = AuthorizationState(source.get("authorization"))
    except ValueError as exc:
        raise ValidationError("authorization is invalid") from exc
    if authorization is AuthorizationState.FORBIDDEN:
        raise ValidationError("source authorization is forbidden")
    try:
        display_policy = DisplayPolicy(source.get("display_policy"))
    except ValueError as exc:
        raise ValidationError("display_policy is invalid") from exc

    available = _available_capabilities(available_provider_capabilities)
    for capability in _required_capabilities(source):
        if capability not in available:
            raise ValidationError("provider capability is unavailable")

    retention = _required_retention(source)
    if retention not in frozenset(supported_retention_modes):
        raise ValidationError("required retention mode is not supported")
    if source.get("reproducible") is False:
        raise ValidationError("source is not reproducible")

    return SourcePolicyDecision(
        source_id=source_id,
        authorization=authorization,
        display_policy=display_policy,
        reproducible=True,
        reasons=(),
    )
