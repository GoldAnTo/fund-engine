"""Tests for the deterministic B-scope acquisition source policy."""
from __future__ import annotations

from collections import Counter
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.acquisition.policy import (
    B_SCOPE_POLICY,
    AcquisitionQueryPlanner,
    SourcePolicy,
)
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionRequest,
    EvidenceObjective,
    PlannedQuery,
)


def request(**overrides: object) -> AcquisitionRequest:
    run_id = uuid4()
    scope_id = uuid4()
    goal_id = "thesis:contradict"
    values: dict[str, object] = {
        "tenant_id": "team-a",
        "case_id": uuid4(),
        "thesis_id": uuid4(),
        "research_run_id": run_id,
        "scope_version_id": scope_id,
        "goal_id": goal_id,
        "round": 1,
        "objective": EvidenceObjective.CONTRADICT,
        "target_link_role": "contradicts",
        "thesis_statement": "公司收入将在 2026 年增长",
        "entity_names": ("示例公司",),
        "security_codes": ("600000",),
        "metric_terms": ("营业收入",),
        "metric_periods": ("2026-Q2",),
        "metric_units": ("亿元",),
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "cutoff": datetime(2026, 8, 12, tzinfo=UTC),
        "allowed_source_roles": frozenset(
            {"company_disclosure", "licensed_provider"}
        ),
        "source_policy_version": B_SCOPE_POLICY.version,
        "planner_version": ACQUISITION_PLANNER_VERSION,
        "previous_query_plan_id": None,
        "expansion": None,
        "idempotency_key": (
            f"run:{run_id}:scope:{scope_id}:goal:{goal_id}:round:1"
        ),
    }
    values.update(overrides)
    return AcquisitionRequest(**values)  # type: ignore[arg-type]


def policy(**overrides: object) -> SourcePolicy:
    values: dict[str, object] = {
        "version": "test-v1",
        "enabled_adapter_keys": frozenset({"test"}),
        "allowed_source_roles": frozenset({"company_disclosure"}),
        "exact_hosts": frozenset({"exact.allowed.example"}),
        "suffix_hosts": frozenset({"suffix.allowed.example"}),
        "max_response_bytes": 1024,
        "per_adapter_page_limit": 1,
        "permission_declarations": (("test", "public-disclosure"),),
    }
    values.update(overrides)
    return SourcePolicy(**values)  # type: ignore[arg-type]


def test_b_scope_policy_only_enables_gildata_and_official_exchanges():
    assert B_SCOPE_POLICY.enabled_adapter_keys == frozenset(
        {"gildata", "sse", "szse"}
    )
    assert B_SCOPE_POLICY.allowed_source_roles == frozenset(
        {"company_disclosure", "licensed_provider"}
    )
    assert {
        adapter
        for adapter, _declaration in B_SCOPE_POLICY.permission_declarations
    } == B_SCOPE_POLICY.enabled_adapter_keys


def test_b_scope_policy_version_tracks_exact_network_authority():
    assert B_SCOPE_POLICY.version == "b-scope-v2"


def test_query_planner_exposes_the_frozen_contract_version():
    assert AcquisitionQueryPlanner.version == ACQUISITION_PLANNER_VERSION


def test_b_scope_policy_declares_only_exact_exchange_hosts():
    assert B_SCOPE_POLICY.exact_hosts == frozenset(
        {
            "query.sse.com.cn",
            "www.sse.com.cn",
            "static.sse.com.cn",
            "big5.sse.com.cn",
            "www.szse.cn",
            "disc.static.szse.cn",
        }
    )
    assert B_SCOPE_POLICY.suffix_hosts == frozenset()


@pytest.mark.parametrize(
    "host",
    [
        "query.sse.com.cn",
        "www.sse.com.cn",
        "static.sse.com.cn",
        "big5.sse.com.cn",
        "www.szse.cn",
        "disc.static.szse.cn",
    ],
)
def test_b_scope_policy_allows_only_declared_exchange_hosts(host: str):
    assert B_SCOPE_POLICY.allows_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "example-news.invalid",
        "evil.sse.com.cn",
        "notwww.sse.com.cn",
        "evil.static.sse.com.cn",
        "notstatic.sse.com.cn",
        "static.sse.com.cn.evil.test",
        "evil.big5.sse.com.cn",
        "notbig5.sse.com.cn",
        "big5.sse.com.cn.evil.test",
        "evil.szse.cn",
        "notdisc.static.szse.cn",
    ],
)
def test_b_scope_policy_never_allows_arbitrary_or_sibling_hosts(host: str):
    assert not B_SCOPE_POLICY.allows_host(host)


def test_suffix_host_matching_respects_dns_label_boundaries():
    value = policy(
        exact_hosts=frozenset(),
        suffix_hosts=frozenset({"allowed.example"}),
    )

    assert value.allows_host("allowed.example")
    assert value.allows_host("child.allowed.example")
    assert not value.allows_host("evilallowed.example")
    assert not value.allows_host("allowed.example.evil")


def test_source_policy_canonicalizes_immutable_inputs():
    value = SourcePolicy(
        version=" test-v1 ",
        enabled_adapter_keys=[" test "],
        allowed_source_roles=[" company_disclosure "],
        exact_hosts=[" Exact.Allowed.Example. "],
        suffix_hosts=[" Suffix.Allowed.Example. "],
        max_response_bytes=1024,
        per_adapter_page_limit=1,
        permission_declarations=[[" test ", " public-disclosure "]],
    )

    assert value.version == "test-v1"
    assert value.enabled_adapter_keys == frozenset({"test"})
    assert value.allowed_source_roles == frozenset({"company_disclosure"})
    assert value.exact_hosts == frozenset({"exact.allowed.example"})
    assert value.suffix_hosts == frozenset({"suffix.allowed.example"})
    assert value.permission_declarations == (("test", "public-disclosure"),)


def test_host_matching_canonicalizes_case_root_dot_and_idna():
    value = policy(
        exact_hosts={"www.sse.com.cn", "xn--bcher-kva.example"},
        suffix_hosts=frozenset(),
    )

    assert value.allows_host("WWW.SSE.COM.CN.")
    assert value.allows_host("BÜCHER.example.")


@pytest.mark.parametrize(
    "host",
    ["", "  ", "bad host.example", "-bad.example", "bad-.example", "bad..example"],
)
def test_host_matching_rejects_malformed_hosts_closed(host: str):
    assert not B_SCOPE_POLICY.allows_host(host)


@pytest.mark.parametrize(
    "field_name",
    ["exact_hosts", "suffix_hosts"],
)
def test_source_policy_rejects_malformed_declared_hosts(field_name: str):
    with pytest.raises(ValueError, match=field_name):
        policy(**{field_name: {"bad host.example"}})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("version", "  "),
        ("enabled_adapter_keys", {"  "}),
        ("allowed_source_roles", {"  "}),
        ("exact_hosts", {"  "}),
        ("suffix_hosts", {"  "}),
        ("permission_declarations", (("test", "  "),)),
    ],
)
def test_source_policy_rejects_blank_values(field_name: str, value: object):
    with pytest.raises(ValueError, match=field_name):
        policy(**{field_name: value})


def test_source_policy_rejects_semantically_overlapping_host_scopes():
    with pytest.raises(ValueError, match="exact_hosts.*suffix_hosts"):
        policy(
            exact_hosts={"child.allowed.example"},
            suffix_hosts={"allowed.example"},
        )


@pytest.mark.parametrize("max_response_bytes", [0, -1])
def test_source_policy_requires_positive_response_limit(max_response_bytes: int):
    with pytest.raises(ValueError, match="max_response_bytes"):
        policy(max_response_bytes=max_response_bytes)


@pytest.mark.parametrize("per_adapter_page_limit", [0, 4])
def test_source_policy_bounds_page_limit(per_adapter_page_limit: int):
    with pytest.raises(ValueError, match="per_adapter_page_limit"):
        policy(per_adapter_page_limit=per_adapter_page_limit)


@pytest.mark.parametrize(
    "permission_declarations",
    [
        (),
        (("test", "public-disclosure"), ("extra", "other")),
        (("test", "one"), ("test", "two")),
    ],
)
def test_source_policy_requires_one_permission_per_enabled_adapter(
    permission_declarations: tuple[tuple[str, str], ...],
):
    with pytest.raises(ValueError, match="permission_declarations"):
        policy(permission_declarations=permission_declarations)


def test_source_policy_is_frozen():
    with pytest.raises(FrozenInstanceError):
        B_SCOPE_POLICY.max_response_bytes = 1


def test_contradiction_query_planning_is_deterministic_and_preserves_scope():
    planner = AcquisitionQueryPlanner()

    first = planner.plan(request(), B_SCOPE_POLICY)
    second = planner.plan(request(), B_SCOPE_POLICY)

    assert first == second
    assert first
    assert all(item.objective is EvidenceObjective.CONTRADICT for item in first)
    assert all("示例公司" in item.query for item in first)
    assert all("600000" in item.query for item in first)
    assert all("营业收入" in item.query for item in first)
    assert all("2026-Q2" in item.query for item in first)
    assert all("亿元" in item.query for item in first)
    assert all("2026-01-01" in item.query for item in first)
    assert all("2026-12-31" in item.query for item in first)
    assert all("反证" in item.query for item in first)
    assert {item.adapter_key for item in first} == {"gildata", "sse", "szse"}
    assert tuple(item.adapter_key for item in first) == ("gildata", "sse", "szse")
    assert max(Counter(item.adapter_key for item in first).values()) <= 3


def test_contradiction_query_plan_has_exact_public_value():
    query = (
        "示例公司 600000 营业收入 2026-Q2 亿元 2026-01-01 至 2026-12-31 "
        "反证 下滑 风险 不及预期"
    )

    assert AcquisitionQueryPlanner().plan(request(), B_SCOPE_POLICY) == (
        PlannedQuery(
            adapter_key="gildata",
            objective=EvidenceObjective.CONTRADICT,
            query=query,
        ),
        PlannedQuery(
            adapter_key="sse",
            objective=EvidenceObjective.CONTRADICT,
            query=query,
        ),
        PlannedQuery(
            adapter_key="szse",
            objective=EvidenceObjective.CONTRADICT,
            query=query,
        ),
    )


def test_query_planner_rejects_policy_version_mismatch():
    with pytest.raises(ValueError, match="source_policy_version"):
        AcquisitionQueryPlanner().plan(request(), policy())


def test_query_planner_rejects_source_roles_outside_policy():
    restricted = SourcePolicy(
        version=B_SCOPE_POLICY.version,
        enabled_adapter_keys=frozenset({"sse"}),
        allowed_source_roles=frozenset({"company_disclosure"}),
        exact_hosts=frozenset({"www.sse.com.cn"}),
        suffix_hosts=frozenset(),
        max_response_bytes=1024,
        per_adapter_page_limit=1,
        permission_declarations=(("sse", "official-public-disclosure"),),
    )

    with pytest.raises(ValueError, match="allowed_source_roles"):
        AcquisitionQueryPlanner().plan(request(), restricted)


@pytest.mark.parametrize(
    ("objective", "target_link_role", "objective_term"),
    [
        (EvidenceObjective.SUPPORT, "supports", "支持"),
        (EvidenceObjective.CONTRADICT, "contradicts", "反证"),
        (
            EvidenceObjective.ALTERNATIVE_EXPLANATION,
            "contextualizes",
            "替代解释",
        ),
        (EvidenceObjective.VERIFY_RULE, "supports", "规则核验"),
    ],
)
def test_query_planner_uses_objective_specific_terms(
    objective: EvidenceObjective, target_link_role: str, objective_term: str
):
    planned = AcquisitionQueryPlanner().plan(
        request(objective=objective, target_link_role=target_link_role),
        B_SCOPE_POLICY,
    )

    assert all(objective_term in item.query for item in planned)
