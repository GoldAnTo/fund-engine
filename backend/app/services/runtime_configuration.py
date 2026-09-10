"""Shared, side-effect-free runtime configuration validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ai.client import validate_llm_configuration
from app.errors import UpstreamUnavailableError
from app.security.oidc import OIDCSettings


_KNOWN_ADAPTERS = frozenset({"gildata", "sse", "szse"})


BASE_REQUIRED = {
    "api": (
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URL",
        "OIDC_TENANT_CLAIM",
        "LLM_API_KEY",
        "GILDATA_TOKEN",
    ),
    "research-worker": ("LLM_API_KEY",),
    "acquisition-worker": ("LLM_API_KEY", "ACQUISITION_ENABLED_ADAPTERS"),
    "scheduler": ("GILDATA_TOKEN",),
}
PUBLIC_CONFIGURATION_ISSUES = frozenset(
    {
        *(name for required in BASE_REQUIRED.values() for name in required),
        "OIDC_CONFIGURATION",
        "LLM_CONFIGURATION",
        "ACQUISITION_ENABLED_ADAPTERS",
    }
)


def validate_adapter_configuration(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Validate configured adapter names without initializing providers."""
    raw = environment.get("ACQUISITION_ENABLED_ADAPTERS", "").strip()
    production = environment.get("APP_ENV", "development").strip().casefold() in {
        "production",
        "prod",
    }
    if not raw:
        if production:
            raise RuntimeError("ACQUISITION_ENABLED_ADAPTERS is required in production")
        raw = "sse,szse"
    keys = tuple(part.strip().casefold() for part in raw.split(",") if part.strip())
    if not keys or len(keys) != len(set(keys)):
        raise RuntimeError("configured acquisition adapters must be unique")
    if set(keys) - _KNOWN_ADAPTERS:
        raise RuntimeError("unknown configured acquisition adapters")
    return keys


def required_provider_config(
    service: str,
    environment: Mapping[str, str],
) -> list[str]:
    required = list(BASE_REQUIRED[service])
    if service == "acquisition-worker":
        adapters = {
            item.strip().casefold()
            for item in environment.get("ACQUISITION_ENABLED_ADAPTERS", "").split(",")
            if item.strip()
        }
        if "gildata" in adapters:
            required.append("GILDATA_TOKEN")
    return sorted(name for name in required if not environment.get(name, "").strip())


def provider_configuration_status(
    service: str,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Validate configuration shape without contacting a provider or exposing values."""
    missing = required_provider_config(service, environment)
    invalid: list[str] = []
    if not missing:
        if service == "api":
            try:
                OIDCSettings.from_mapping(environment)
            except UpstreamUnavailableError:
                invalid.append("OIDC_CONFIGURATION")
        elif service == "acquisition-worker":
            try:
                validate_adapter_configuration(environment)
            except RuntimeError:
                invalid.append("ACQUISITION_ENABLED_ADAPTERS")
        if service in {"api", "research-worker", "acquisition-worker"}:
            try:
                validate_llm_configuration(environment)
            except RuntimeError:
                invalid.append("LLM_CONFIGURATION")
    result: dict[str, Any] = {
        "status": "healthy" if not missing and not invalid else "unhealthy",
        "missing": missing,
    }
    if invalid:
        result["invalid"] = invalid
    return result
