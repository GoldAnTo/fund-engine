"""Versioned source policy and deterministic acquisition query planning."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.domain.acquisition import (
    ACQUISITION_SOURCE_ROLES,
    B_SCOPE_POLICY_VERSION,
    AcquisitionRequest,
    EvidenceObjective,
    PlannedQuery,
)


def _canonical_string(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not contain blank values")
    return normalized


def _canonical_string_set(
    values: Iterable[str], field_name: str
) -> frozenset[str]:
    return frozenset(
        _canonical_string(value, field_name) for value in values
    )


def _canonical_host(host: object) -> str | None:
    if not isinstance(host, str):
        return None
    candidate = host.strip()
    if not candidate:
        return None
    try:
        ascii_host = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if ascii_host.endswith("."):
        ascii_host = ascii_host[:-1]
    if not ascii_host or ascii_host.endswith(".") or len(ascii_host) > 253:
        return None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        return None
    return ascii_host


def _canonical_hosts(
    values: Iterable[str], field_name: str
) -> frozenset[str]:
    normalized: set[str] = set()
    for host in values:
        canonical = _canonical_host(host)
        if canonical is None:
            raise ValueError(f"{field_name} must not contain blank or malformed hosts")
        normalized.add(canonical)
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    version: str
    enabled_adapter_keys: frozenset[str]
    allowed_source_roles: frozenset[str]
    exact_hosts: frozenset[str]
    suffix_hosts: frozenset[str]
    max_response_bytes: int
    per_adapter_page_limit: int
    permission_declarations: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _canonical_string(self.version, "version"))
        object.__setattr__(
            self,
            "enabled_adapter_keys",
            _canonical_string_set(
                self.enabled_adapter_keys, "enabled_adapter_keys"
            ),
        )
        object.__setattr__(
            self,
            "allowed_source_roles",
            _canonical_string_set(
                self.allowed_source_roles, "allowed_source_roles"
            ),
        )
        object.__setattr__(
            self, "exact_hosts", _canonical_hosts(self.exact_hosts, "exact_hosts")
        )
        object.__setattr__(
            self, "suffix_hosts", _canonical_hosts(self.suffix_hosts, "suffix_hosts")
        )

        declarations = tuple(
            sorted(
                (
                    _canonical_string(adapter_key, "permission_declarations"),
                    _canonical_string(declaration, "permission_declarations"),
                )
                for adapter_key, declaration in self.permission_declarations
            )
        )
        object.__setattr__(self, "permission_declarations", declarations)

        if any(
            exact_host == suffix_host
            or exact_host.endswith("." + suffix_host)
            for exact_host in self.exact_hosts
            for suffix_host in self.suffix_hosts
        ):
            raise ValueError("exact_hosts must not overlap suffix_hosts")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")
        if not 1 <= self.per_adapter_page_limit <= 3:
            raise ValueError("per_adapter_page_limit must be between 1 and 3")

        declared_adapters = tuple(adapter for adapter, _value in declarations)
        if (
            len(declared_adapters) != len(self.enabled_adapter_keys)
            or len(set(declared_adapters)) != len(declared_adapters)
            or set(declared_adapters) != set(self.enabled_adapter_keys)
        ):
            raise ValueError(
                "permission_declarations must contain exactly one entry "
                "per enabled adapter"
            )

    def allows_host(self, host: str) -> bool:
        canonical = _canonical_host(host)
        if canonical is None:
            return False
        if canonical in self.exact_hosts:
            return True
        return any(
            canonical == suffix or canonical.endswith("." + suffix)
            for suffix in self.suffix_hosts
        )


B_SCOPE_POLICY: Final = SourcePolicy(
    version=B_SCOPE_POLICY_VERSION,
    enabled_adapter_keys=frozenset({"gildata", "sse", "szse"}),
    allowed_source_roles=ACQUISITION_SOURCE_ROLES,
    exact_hosts=frozenset(
        {
            "query.sse.com.cn",
            "www.sse.com.cn",
            "static.sse.com.cn",
            "big5.sse.com.cn",
            "www.szse.cn",
            "disc.static.szse.cn",
        }
    ),
    suffix_hosts=frozenset(),
    max_response_bytes=20 * 1024 * 1024,
    per_adapter_page_limit=3,
    permission_declarations=(
        ("gildata", "licensed-provider-contract"),
        ("sse", "official-public-disclosure"),
        ("szse", "official-public-disclosure"),
    ),
)


_OBJECTIVE_QUERY_TERMS: Final = {
    EvidenceObjective.SUPPORT: "支持 增长 改善",
    EvidenceObjective.CONTRADICT: "反证 下滑 风险 不及预期",
    EvidenceObjective.ALTERNATIVE_EXPLANATION: "替代解释 其他原因 归因",
    EvidenceObjective.VERIFY_RULE: "规则核验 条件 例外",
}


class AcquisitionQueryPlanner:
    """Compose bounded, replayable queries without accepting source locations."""

    def plan(
        self, request: AcquisitionRequest, policy: SourcePolicy
    ) -> tuple[PlannedQuery, ...]:
        if request.source_policy_version != policy.version:
            raise ValueError(
                "request source_policy_version does not match policy version"
            )
        if not request.allowed_source_roles <= policy.allowed_source_roles:
            raise ValueError("request allowed_source_roles exceed policy roles")

        query = " ".join(
            part
            for part in (
                " ".join(request.entity_names),
                " ".join(request.security_codes),
                " ".join(request.metric_terms),
                f"{request.period_start} 至 {request.period_end}",
                _OBJECTIVE_QUERY_TERMS[request.objective],
            )
            if part
        )
        return tuple(
            PlannedQuery(
                adapter_key=adapter_key,
                objective=request.objective,
                query=query,
            )
            for adapter_key in sorted(policy.enabled_adapter_keys)
        )
