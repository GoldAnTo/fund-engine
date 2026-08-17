"""Contract tests for the public governed-acquisition vocabulary."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.acquisition import (
    AcquisitionJobRef,
    AcquisitionJobView,
    AcquisitionPrincipal,
    AcquisitionRequest,
    AdmittedEvidenceRef,
    EvidenceObjective,
    PlannedQuery,
)
from app.acquisition.policy import B_SCOPE_POLICY


def request(**overrides: object) -> AcquisitionRequest:
    values: dict[str, object] = {
        "tenant_id": "team-a",
        "case_id": uuid4(),
        "thesis_id": uuid4(),
        "research_run_id": None,
        "round": 1,
        "objective": EvidenceObjective.CONTRADICT,
        "target_link_role": "contradicts",
        "thesis_statement": "公司收入将在 2026 年增长",
        "entity_names": ("示例公司",),
        "security_codes": ("600000",),
        "metric_terms": ("营业收入",),
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "cutoff": datetime(2026, 8, 12, tzinfo=UTC),
        "allowed_source_roles": frozenset(
            {"company_disclosure", "licensed_provider"}
        ),
        "source_policy_version": B_SCOPE_POLICY.version,
        "idempotency_key": "run:none:thesis:contradict:1",
    }
    values.update(overrides)
    return AcquisitionRequest(**values)  # type: ignore[arg-type]


def test_evidence_objective_has_exact_public_vocabulary():
    assert [(item.name, item.value) for item in EvidenceObjective] == [
        ("SUPPORT", "support"),
        ("CONTRADICT", "contradict"),
        ("ALTERNATIVE_EXPLANATION", "alternative_explanation"),
        ("VERIFY_RULE", "verify_rule"),
    ]


def test_request_is_frozen_slotted_and_uses_utc_cutoff():
    value = request()

    with pytest.raises(FrozenInstanceError):
        value.round = 2

    assert not hasattr(value, "__dict__")
    assert value.cutoff.tzinfo is not None
    assert value.cutoff.utcoffset() == timedelta(0)


def test_request_normalizes_aware_cutoff_to_utc():
    china_standard_time = timezone(timedelta(hours=8))

    value = request(
        cutoff=datetime(2026, 8, 12, 8, tzinfo=china_standard_time)
    )

    assert value.cutoff == datetime(2026, 8, 12, tzinfo=UTC)
    assert value.cutoff.tzinfo is UTC


def test_request_canonicalizes_mutable_collection_inputs():
    entity_names = [" 示例公司 "]
    security_codes = [" 600000 "]
    metric_terms = [" 营业收入 "]
    allowed_source_roles = {" company_disclosure ", "licensed_provider"}

    value = request(
        entity_names=entity_names,
        security_codes=security_codes,
        metric_terms=metric_terms,
        allowed_source_roles=allowed_source_roles,
    )
    entity_names.append("后来公司")
    security_codes.append("000001")
    metric_terms.append("净利润")
    allowed_source_roles.add("arbitrary_web")

    assert value.entity_names == ("示例公司",)
    assert value.security_codes == ("600000",)
    assert value.metric_terms == ("营业收入",)
    assert value.allowed_source_roles == frozenset(
        {"company_disclosure", "licensed_provider"}
    )


@pytest.mark.parametrize(
    "field_name",
    ["entity_names", "security_codes", "metric_terms", "allowed_source_roles"],
)
def test_request_rejects_blank_collection_elements(field_name: str):
    with pytest.raises(ValueError, match=field_name):
        request(**{field_name: ["  "]})


def test_request_strips_scalar_string_fields():
    value = request(
        tenant_id=" team-a ",
        target_link_role=" contradicts ",
        thesis_statement=" 公司收入将在 2026 年增长 ",
        period_start=" 2026-01-01 ",
        period_end=" 2026-12-31 ",
        source_policy_version=f" {B_SCOPE_POLICY.version} ",
        idempotency_key=" run:none:thesis:contradict:1 ",
    )

    assert value.tenant_id == "team-a"
    assert value.target_link_role == "contradicts"
    assert value.thesis_statement == "公司收入将在 2026 年增长"
    assert value.period_start == "2026-01-01"
    assert value.period_end == "2026-12-31"
    assert value.source_policy_version == B_SCOPE_POLICY.version
    assert value.idempotency_key == "run:none:thesis:contradict:1"


@pytest.mark.parametrize(
    ("field_name", "blank_value"),
    [
        ("tenant_id", "  "),
        ("idempotency_key", ""),
        ("target_link_role", "  "),
        ("thesis_statement", "  "),
        ("period_start", "  "),
        ("period_end", "  "),
        ("source_policy_version", "  "),
    ],
)
def test_request_rejects_blank_scalar_fields(field_name: str, blank_value: str):
    with pytest.raises(ValueError, match=field_name):
        request(**{field_name: blank_value})


def test_request_rejects_naive_cutoff():
    with pytest.raises(ValueError, match="cutoff"):
        request(cutoff=datetime(2026, 8, 12))


def test_request_rejects_empty_objective_scope():
    with pytest.raises(ValueError, match="objective scope"):
        request(entity_names=(), security_codes=(), metric_terms=())


def test_request_rejects_empty_source_role_scope():
    with pytest.raises(ValueError, match="allowed_source_roles"):
        request(allowed_source_roles=frozenset())


def test_request_rejects_unknown_source_roles():
    with pytest.raises(ValueError, match="unknown source roles"):
        request(allowed_source_roles=frozenset({"arbitrary_web"}))


def test_request_keeps_existing_callers_on_external_gap_defaults():
    value = request()

    assert value.acquisition_kind == "external_gap"
    assert value.document_version_id is None


def test_request_accepts_only_exact_document_bound_intake_material_scope():
    document_id = uuid4()

    value = request(
        objective=EvidenceObjective.SUPPORT,
        target_link_role="supports",
        acquisition_kind="intake_material",
        document_version_id=document_id,
        allowed_source_roles=frozenset({"user_provided_material"}),
    )

    assert value.document_version_id == document_id
    assert value.allowed_source_roles == frozenset({"user_provided_material"})


def test_request_rejects_intake_role_on_external_acquisition():
    with pytest.raises(ValueError, match="network source roles"):
        request(allowed_source_roles=frozenset({"user_provided_material"}))


def test_request_rejects_unbound_intake_material():
    with pytest.raises(ValueError, match="document_version_id"):
        request(
            objective=EvidenceObjective.SUPPORT,
            target_link_role="supports",
            acquisition_kind="intake_material",
            allowed_source_roles=frozenset({"user_provided_material"}),
        )


def test_request_rejects_source_policy_version_mismatch():
    with pytest.raises(ValueError, match="source_policy_version"):
        request(source_policy_version="future-policy")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("period_start", "not-a-date"),
        ("period_end", "2026-02-30"),
        ("period_start", "2026-01-01T00:00:00"),
    ],
)
def test_request_rejects_non_iso_period_dates(field_name: str, value: str):
    with pytest.raises(ValueError, match=field_name):
        request(**{field_name: value})


def test_request_canonicalizes_iso_period_dates():
    value = request(period_start="20260101", period_end="20261231")

    assert value.period_start == "2026-01-01"
    assert value.period_end == "2026-12-31"


def test_request_rejects_reversed_period():
    with pytest.raises(ValueError, match="period_start must not be after period_end"):
        request(period_start="2026-12-31", period_end="2026-01-01")


@pytest.mark.parametrize(
    ("objective", "target_link_role"),
    [
        (EvidenceObjective.SUPPORT, "supports"),
        (EvidenceObjective.CONTRADICT, "contradicts"),
        (EvidenceObjective.ALTERNATIVE_EXPLANATION, "contextualizes"),
        (EvidenceObjective.VERIFY_RULE, "supports"),
        (EvidenceObjective.VERIFY_RULE, "contradicts"),
    ],
)
def test_request_accepts_objective_compatible_target_roles(
    objective: EvidenceObjective, target_link_role: str
):
    assert (
        request(objective=objective, target_link_role=target_link_role).target_link_role
        == target_link_role
    )


@pytest.mark.parametrize(
    ("objective", "target_link_role"),
    [
        (EvidenceObjective.SUPPORT, "contradicts"),
        (EvidenceObjective.CONTRADICT, "supports"),
        (EvidenceObjective.ALTERNATIVE_EXPLANATION, "supports"),
        (EvidenceObjective.VERIFY_RULE, "contextualizes"),
    ],
)
def test_request_rejects_objective_incompatible_target_roles(
    objective: EvidenceObjective, target_link_role: str
):
    with pytest.raises(ValueError, match="target_link_role"):
        request(objective=objective, target_link_role=target_link_role)


def test_request_contract_has_no_host_or_url_inputs():
    field_names = {item.name for item in fields(AcquisitionRequest)}

    assert field_names.isdisjoint({"adapter_key", "host", "url", "source_url"})


@pytest.mark.parametrize(
    "value",
    [
        AcquisitionPrincipal(tenant_id="team-a", actor="system:test"),
        PlannedQuery(
            adapter_key="gildata",
            objective=EvidenceObjective.SUPPORT,
            query="示例公司 营业收入",
        ),
        AcquisitionJobRef(id=uuid4(), status="queued"),
        AcquisitionJobView(id=uuid4(), status="queued", stage="queued", attempt=0),
        AdmittedEvidenceRef(
            evidence_link_id=uuid4(),
            source_statement_id=uuid4(),
            document_version_id=uuid4(),
        ),
    ],
)
def test_public_values_are_frozen_and_slotted(value: object):
    assert not hasattr(value, "__dict__")
    first_field = fields(value)[0].name

    with pytest.raises(FrozenInstanceError):
        setattr(value, first_field, getattr(value, first_field))


@pytest.mark.parametrize("field_name", ["tenant_id", "actor"])
def test_principal_rejects_blank_server_resolved_identity(field_name: str):
    values = {"tenant_id": "team-a", "actor": "system:test"}
    values[field_name] = "  "

    with pytest.raises(ValueError, match=field_name):
        AcquisitionPrincipal(**values)


def test_principal_canonicalizes_server_resolved_identity():
    value = AcquisitionPrincipal(
        tenant_id=" team-a ", actor=" system:test "
    )

    assert value.tenant_id == "team-a"
    assert value.actor == "system:test"
