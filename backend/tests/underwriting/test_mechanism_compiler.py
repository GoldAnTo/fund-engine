"""Contracts for governed economic mechanism compilation."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.mechanisms import (
    Falsifier,
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from app.underwriting.domain.metrics import MetricObservation
from app.underwriting.services.mechanism_compiler import (
    MechanismDependencyContext,
    MetricDefinitionDependency,
    compile_mechanisms,
    transition_mechanism,
    validate_compiled_mechanism_integrity,
    validate_formal_mechanism,
)
from app.underwriting.services.source_freeze import (
    FrozenObservationSet,
    freeze_manifest,
    freeze_observations,
)


CUTOFF = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def frozen_observations(
    metric_bindings: dict[str, str],
    source_manifest,
    *,
    definition_versions: dict[str, int] | None = None,
):
    versions = definition_versions or {}
    records = []
    for key in metric_bindings:
        is_industry = key.startswith("industry.")
        records.append(
            {
                "definition_key": key,
                "definition_version": versions.get(key, 1),
                "value": "1",
                "unit": "unit",
                "observed_start": "2025-01-01T00:00:00+00:00",
                "observed_end": "2025-01-01T00:00:00+00:00",
                "effective_at": "2025-05-14T00:00:00+00:00",
                "available_at": "2025-05-14T00:00:00+00:00",
                "source_id": "iea-2025" if is_industry else "catl-2025",
                "source_locator": "p1",
                "dimensions": {},
                "source_role": "official_industry" if is_industry else "reported",
                "research_object_kind": "industry" if is_industry else "company",
            }
        )
    return freeze_observations(records, source_manifest=source_manifest)


def dependency_context(
    bindings: dict[str, str] | None = None,
) -> MechanismDependencyContext:
    metric_bindings = bindings or {
        "industry.ev_demand_gwh": "metric-demand",
        "company.battery_shipments_gwh": "metric-shipment",
        "company.battery_shipments_growth": "metric-growth",
    }
    source_manifest = freeze_manifest(
        {
            "schema_version": "underwriting.source-manifest.v1",
            "sources": [
                {
                    "source_id": "iea-2025",
                    "title": "Global EV Outlook 2025",
                    "locator": "https://example.test/iea-2025",
                    "published_at": "2025-05-14T00:00:00+00:00",
                    "first_available_at": "2025-05-14T00:00:00+00:00",
                    "retrieved_at": "2025-05-14T00:00:00+00:00",
                    "content_sha256": "a" * 64,
                    "authority": "official_industry",
                    "authorization": "authorized",
                    "display_policy": "derived_only",
                    "provider_capability": "public_http",
                    "retention": "hash_locator_and_derived_observations",
                }
                ,
                {
                    "source_id": "catl-2025",
                    "title": "CATL annual report 2024",
                    "locator": "https://example.test/catl-2025",
                    "published_at": "2025-03-15T00:00:00+00:00",
                    "first_available_at": "2025-03-15T00:00:00+00:00",
                    "retrieved_at": "2025-03-15T00:00:00+00:00",
                    "content_sha256": "c" * 64,
                    "authority": "issuer_filing",
                    "authorization": "authorized",
                    "display_policy": "derived_only",
                    "provider_capability": "public_http",
                    "retention": "hash_locator_and_derived_observations",
                },
            ],
        },
        cutoff=CUTOFF,
    )
    return MechanismDependencyContext(
        cutoff=CUTOFF,
        metric_definitions=tuple(
            MetricDefinitionDependency(
                metric_key=key,
                definition_id=definition_id,
                definition_version=1,
                content_hash="b" * 64,
                source_manifest_hash=source_manifest.manifest_hash,
                available_at=datetime(2025, 5, 14, tzinfo=UTC),
            )
            for key, definition_id in metric_bindings.items()
        ),
        source_manifest=source_manifest,
        frozen_observations=frozen_observations(metric_bindings, source_manifest),
    )


def formal_mechanism(**overrides: object) -> MechanismPack:
    candidate = MechanismPack(
        key="ev_storage_demand_to_shipments",
        version=1,
        status=MechanismStatus.CANDIDATE,
        scope_object_id=uuid4(),
        driver_keys=("industry.ev_demand_gwh",),
        formula="battery_shipments = end_demand * battery_intensity",
        applicability=("global power battery market",),
        invalidation_conditions=("battery intensity materially changes",),
        financial_mappings=(
            FinancialMapping(
                driver_key="industry.ev_demand_gwh",
                target_metric_key="company.battery_shipments_gwh",
                direction="positive",
                lag_periods=1,
                magnitude_low=Decimal("0.1"),
                magnitude_high=Decimal("0.9"),
            ),
        ),
        alternative_explanations=("inventory destocking",),
        falsifiers=(
            Falsifier(
                key="shipment_growth_fails",
                metric_key="company.battery_shipments_growth",
                operator="lt",
                threshold_low=Decimal("0"),
                threshold_high=None,
                evaluation_periods=2,
                consequence="challenge mechanism",
            ),
        ),
        source_ids=("iea-2025",),
    )
    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    confirmed = transition_mechanism(
        calibrated,
        MechanismStatus.HUMAN_CONFIRMED,
        human_confirmation_identity="researcher-001",
        review_evidence_id=uuid4(),
    )
    formal = transition_mechanism(confirmed, MechanismStatus.FORMAL)
    return replace(formal, **overrides)


def test_only_formal_mechanisms_can_compile() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    with pytest.raises(ValidationError, match="mechanism must be formal"):
        compile_mechanisms(
            (candidate,),
            dependencies=dependency_context(),
        )


def test_formal_mechanism_requires_mapping_alternative_and_falsifier() -> None:
    value = formal_mechanism(financial_mappings=(), alternative_explanations=(), falsifiers=())
    with pytest.raises(ValidationError, match="formal mechanism is incomplete"):
        validate_formal_mechanism(value)


def test_transition_requires_adjacent_lifecycle_and_human_confirmation() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    with pytest.raises(ValidationError, match="not allowed"):
        transition_mechanism(candidate, MechanismStatus.CALIBRATED)

    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    with pytest.raises(ValidationError, match="human_confirmation_identity"):
        transition_mechanism(calibrated, MechanismStatus.HUMAN_CONFIRMED)


def test_formal_transition_creates_successor_and_retains_named_reviewer() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    confirmed = transition_mechanism(
        calibrated,
        MechanismStatus.HUMAN_CONFIRMED,
        human_confirmation_identity="reviewer-001",
        review_evidence_id=uuid4(),
    )
    formal = transition_mechanism(confirmed, MechanismStatus.FORMAL)
    assert formal.version == candidate.version + 4
    assert formal.human_confirmation_identity == "reviewer-001"


def test_direct_v1_formal_mechanism_is_rejected() -> None:
    candidate = formal_mechanism()
    with pytest.raises(ValidationError, match="formal mechanism requires a reviewed predecessor"):
        MechanismPack(
            key=candidate.key,
            version=1,
            status=MechanismStatus.FORMAL,
            scope_object_id=candidate.scope_object_id,
            driver_keys=candidate.driver_keys,
            formula=candidate.formula,
            applicability=candidate.applicability,
            invalidation_conditions=candidate.invalidation_conditions,
            financial_mappings=candidate.financial_mappings,
            alternative_explanations=candidate.alternative_explanations,
            falsifiers=candidate.falsifiers,
            source_ids=candidate.source_ids,
            human_confirmation_identity="reviewer-001",
            review_evidence_id=uuid4(),
        )


def test_formal_mechanism_rejects_unmapped_driver_and_circular_chain() -> None:
    unmapped = formal_mechanism(driver_keys=("industry.ev_demand_gwh", "industry.storage_demand_gwh"))
    with pytest.raises(ValidationError, match="cover every driver_key"):
        validate_formal_mechanism(unmapped)

    cyclic = formal_mechanism(
        driver_keys=("industry.ev_demand_gwh", "company.battery_shipments_gwh"),
        financial_mappings=(
            FinancialMapping(
                "industry.ev_demand_gwh",
                "company.battery_shipments_gwh",
                "positive",
                1,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
            FinancialMapping(
                "company.battery_shipments_gwh",
                "industry.ev_demand_gwh",
                "positive",
                0,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="causal chain must be acyclic"):
        validate_formal_mechanism(cyclic)


def test_compiler_requires_known_canonical_inputs_outputs_and_sources() -> None:
    value = formal_mechanism()
    with pytest.raises(ValidationError, match="target metric definition"):
        compile_mechanisms(
            (value,),
            dependencies=dependency_context({"industry.ev_demand_gwh": "metric-demand"}),
        )


def test_compiler_returns_deterministic_hash_and_dependency_ids() -> None:
    value = formal_mechanism()
    metric_definition_ids = {
        "industry.ev_demand_gwh": "metric-demand",
        "company.battery_shipments_gwh": "metric-shipment",
        "company.battery_shipments_growth": "metric-growth",
    }
    first = compile_mechanisms(
        (value,), dependencies=dependency_context(metric_definition_ids)
    )
    second = compile_mechanisms(
        (value,), dependencies=dependency_context(dict(reversed(tuple(metric_definition_ids.items()))))
    )
    assert first.content_hash == second.content_hash
    assert first.metric_definition_ids == (
        "metric-demand",
        "metric-growth",
        "metric-shipment",
    )
    assert first.source_ids == ("catl-2025", "iea-2025")
    assert first.cutoff == CUTOFF


def test_compiler_rejects_causal_cycle_across_formal_pack_union() -> None:
    scope_object_id = uuid4()
    forward = formal_mechanism(key="demand_to_shipments", scope_object_id=scope_object_id)
    reverse = formal_mechanism(
        key="shipments_to_demand",
        scope_object_id=scope_object_id,
        driver_keys=("company.battery_shipments_gwh",),
        financial_mappings=(
            FinancialMapping(
                "company.battery_shipments_gwh",
                "industry.ev_demand_gwh",
                "positive",
                0,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="causal chain must be acyclic"):
        compile_mechanisms((forward, reverse), dependencies=dependency_context())


def test_compiler_rejects_formal_packs_from_multiple_scopes() -> None:
    with pytest.raises(ValidationError, match="one scope_object_id"):
        compile_mechanisms(
            (formal_mechanism(), formal_mechanism(key="other", scope_object_id=uuid4())),
            dependencies=dependency_context(),
        )


def test_dependency_id_rejects_non_string_non_uuid_object() -> None:
    with pytest.raises(ValidationError, match="definition_id must be a UUID or non-empty string"):
        MetricDefinitionDependency(
            metric_key="industry.ev_demand_gwh",
            definition_id=object(),
            definition_version=1,
            content_hash="b" * 64,
            source_manifest_hash=dependency_context().source_manifest.manifest_hash,
            available_at=datetime(2025, 5, 14, tzinfo=UTC),
        )


def test_compiler_rejects_mechanism_pack_subclasses_at_boundary() -> None:
    class DerivedMechanismPack(MechanismPack):
        pass

    base = formal_mechanism()
    derived = DerivedMechanismPack(
        key=base.key,
        version=base.version,
        status=base.status,
        scope_object_id=base.scope_object_id,
        driver_keys=base.driver_keys,
        formula=base.formula,
        applicability=base.applicability,
        invalidation_conditions=base.invalidation_conditions,
        financial_mappings=base.financial_mappings,
        alternative_explanations=base.alternative_explanations,
        falsifiers=base.falsifiers,
        source_ids=base.source_ids,
        human_confirmation_identity=base.human_confirmation_identity,
        review_evidence_id=base.review_evidence_id,
        predecessor_status=base.predecessor_status,
        predecessor_version=base.predecessor_version,
    )
    with pytest.raises(ValidationError, match="MechanismPack values"):
        compile_mechanisms((derived,), dependencies=dependency_context())


def test_dependency_context_rejects_metric_unavailable_at_cutoff() -> None:
    with pytest.raises(ValidationError, match="metric definition is unavailable at cutoff"):
        MechanismDependencyContext(
            cutoff=CUTOFF,
            metric_definitions=(
                MetricDefinitionDependency(
                    metric_key="industry.ev_demand_gwh",
                    definition_id="metric-demand",
                    definition_version=1,
                    content_hash="b" * 64,
                    source_manifest_hash=dependency_context().source_manifest.manifest_hash,
                    available_at=datetime(2025, 5, 16, tzinfo=UTC),
                ),
            ),
            source_manifest=dependency_context().source_manifest,
            frozen_observations=dependency_context().frozen_observations,
        )


def test_dependency_context_rejects_raw_observation_tuple() -> None:
    """A tuple of valid-looking observations is not an authenticated freeze batch."""
    baseline = dependency_context()
    with pytest.raises(ValidationError, match="frozen observation set"):
        MechanismDependencyContext(
            cutoff=baseline.cutoff,
            metric_definitions=baseline.metric_definitions,
            source_manifest=baseline.source_manifest,
            frozen_observations=baseline.metric_observations,
        )


def test_dependency_context_rejects_forged_and_tampered_observation_batches() -> None:
    baseline = dependency_context()
    forged = object.__new__(FrozenObservationSet)
    for field in (
        "cutoff",
        "source_manifest_hash",
        "observation_set_hash",
        "observations",
    ):
        object.__setattr__(forged, field, getattr(baseline.frozen_observations, field))
    with pytest.raises(ValidationError, match="created by freeze_observations"):
        MechanismDependencyContext(
            cutoff=baseline.cutoff,
            metric_definitions=baseline.metric_definitions,
            source_manifest=baseline.source_manifest,
            frozen_observations=forged,
        )

    tampered = dependency_context()
    object.__setattr__(
        tampered.frozen_observations,
        "observations",
        (replace(tampered.frozen_observations.observations[0], value=Decimal("2")),)
        + tampered.frozen_observations.observations[1:],
    )
    with pytest.raises(ValidationError, match="set hash|authenticated batch"):
        MechanismDependencyContext(
            cutoff=tampered.cutoff,
            metric_definitions=tampered.metric_definitions,
            source_manifest=tampered.source_manifest,
            frozen_observations=tampered.frozen_observations,
        )


def test_compiled_mechanism_integrity_revalidates_its_authenticated_batch() -> None:
    compiled = compile_mechanisms((formal_mechanism(),), dependencies=dependency_context())
    forged = object.__new__(FrozenObservationSet)
    for field in (
        "cutoff",
        "source_manifest_hash",
        "observation_set_hash",
        "observations",
    ):
        object.__setattr__(forged, field, getattr(compiled.frozen_observations, field))

    with pytest.raises(ValidationError, match="created by freeze_observations"):
        validate_compiled_mechanism_integrity(
            replace(compiled, frozen_observations=forged)
        )


def test_context_and_compiled_integrity_reject_a_forged_source_manifest() -> None:
    baseline = dependency_context()
    forged = object.__new__(type(baseline.source_manifest))
    for field in ("cutoff", "source_ids", "manifest_hash", "sources"):
        object.__setattr__(forged, field, getattr(baseline.source_manifest, field))
    with pytest.raises(ValidationError, match="frozen source manifest"):
        MechanismDependencyContext(
            cutoff=baseline.cutoff,
            metric_definitions=baseline.metric_definitions,
            source_manifest=forged,
            frozen_observations=baseline.frozen_observations,
        )

    compiled = compile_mechanisms((formal_mechanism(),), dependencies=baseline)
    with pytest.raises(ValidationError, match="frozen source manifest"):
        validate_compiled_mechanism_integrity(
            replace(compiled, source_manifest=forged)
        )


def test_compiled_hash_keeps_metric_key_to_definition_id_binding() -> None:
    value = formal_mechanism()
    first = compile_mechanisms(
        (value,),
        dependencies=dependency_context(
            {
                "industry.ev_demand_gwh": "metric-demand",
                "company.battery_shipments_gwh": "metric-shipment",
                "company.battery_shipments_growth": "metric-growth",
            }
        ),
    )
    second = compile_mechanisms(
        (value,),
        dependencies=dependency_context(
            {
                "industry.ev_demand_gwh": "metric-shipment",
                "company.battery_shipments_gwh": "metric-demand",
                "company.battery_shipments_growth": "metric-growth",
            }
        ),
    )
    assert first.content_hash != second.content_hash
    assert first.metric_definition_bindings == (
        ("company.battery_shipments_growth", "metric-growth"),
        ("company.battery_shipments_gwh", "metric-shipment"),
        ("industry.ev_demand_gwh", "metric-demand"),
    )


def test_dependency_context_rejects_definition_from_foreign_manifest() -> None:
    frozen = dependency_context().source_manifest
    with pytest.raises(ValidationError, match="definition source manifest hash"):
        MechanismDependencyContext(
            cutoff=CUTOFF,
            metric_definitions=(
                MetricDefinitionDependency(
                    metric_key="industry.ev_demand_gwh",
                    definition_id="metric-demand",
                    definition_version=1,
                    content_hash="b" * 64,
                    source_manifest_hash="c" * 64,
                    available_at=datetime(2025, 5, 14, tzinfo=UTC),
                ),
            ),
            source_manifest=frozen,
            frozen_observations=dependency_context().frozen_observations,
        )


def test_compiled_hash_changes_when_manifest_content_changes_under_same_source_id() -> None:
    first = compile_mechanisms((formal_mechanism(),), dependencies=dependency_context())
    changed_manifest = freeze_manifest(
        {
            "schema_version": "underwriting.source-manifest.v1",
            "sources": [
                {
                    "source_id": "iea-2025",
                    "title": "Global EV Outlook 2025 revised retrieval",
                    "locator": "https://example.test/iea-2025",
                    "published_at": "2025-05-14T00:00:00+00:00",
                    "first_available_at": "2025-05-14T00:00:00+00:00",
                    "retrieved_at": "2025-05-14T00:00:00+00:00",
                    "content_sha256": "d" * 64,
                    "authority": "official_industry",
                    "authorization": "authorized",
                    "display_policy": "derived_only",
                    "provider_capability": "public_http",
                    "retention": "hash_locator_and_derived_observations",
                },
                {
                    "source_id": "catl-2025",
                    "title": "CATL annual report 2024",
                    "locator": "https://example.test/catl-2025",
                    "published_at": "2025-03-15T00:00:00+00:00",
                    "first_available_at": "2025-03-15T00:00:00+00:00",
                    "retrieved_at": "2025-03-15T00:00:00+00:00",
                    "content_sha256": "c" * 64,
                    "authority": "issuer_filing",
                    "authorization": "authorized",
                    "display_policy": "derived_only",
                    "provider_capability": "public_http",
                    "retention": "hash_locator_and_derived_observations",
                },
            ],
        },
        cutoff=CUTOFF,
    )
    changed_context = MechanismDependencyContext(
        cutoff=CUTOFF,
        metric_definitions=tuple(
            MetricDefinitionDependency(
                metric_key=dependency.metric_key,
                definition_id=dependency.definition_id,
                definition_version=dependency.definition_version,
                content_hash=dependency.content_hash,
                source_manifest_hash=changed_manifest.manifest_hash,
                available_at=dependency.available_at,
            )
            for dependency in dependency_context().metric_definitions
        ),
        source_manifest=changed_manifest,
        frozen_observations=frozen_observations(
            {item.metric_key: str(item.definition_id) for item in dependency_context().metric_definitions},
            changed_manifest,
        ),
    )
    second = compile_mechanisms((formal_mechanism(),), dependencies=changed_context)
    assert first.source_ids == second.source_ids == ("catl-2025", "iea-2025")
    assert first.source_manifest_hash != second.source_manifest_hash
    assert first.content_hash != second.content_hash


def test_compiled_hash_keeps_definition_version_and_content_provenance() -> None:
    baseline = dependency_context()
    first = compile_mechanisms((formal_mechanism(),), dependencies=baseline)
    changed_definition = replace(
        baseline.metric_definitions[0], definition_version=2, content_hash="e" * 64
    )
    changed = MechanismDependencyContext(
        cutoff=baseline.cutoff,
        metric_definitions=(changed_definition,) + baseline.metric_definitions[1:],
        source_manifest=baseline.source_manifest,
        frozen_observations=frozen_observations(
            {item.metric_key: str(item.definition_id) for item in baseline.metric_definitions},
            baseline.source_manifest,
            definition_versions={changed_definition.metric_key: 2},
        ),
    )
    second = compile_mechanisms((formal_mechanism(),), dependencies=changed)
    assert first.metric_definition_bindings == second.metric_definition_bindings
    assert first.content_hash != second.content_hash


def test_falsifier_requires_complete_operator_bounds() -> None:
    with pytest.raises(ValidationError, match="outside"):
        Falsifier(
            key="range_break",
            metric_key="industry.utilization",
            operator="outside",
            threshold_low=Decimal("0.8"),
            threshold_high=None,
            evaluation_periods=1,
            consequence="challenge",
        )


@pytest.mark.parametrize(
    "key",
    (
        "ev_storage_demand_to_shipments",
        "effective_capacity_to_utilization_and_price",
        "material_cost_pass_through_to_unit_margin",
        "certification_overseas_footprint_to_obtainable_share",
        "capex_working_capital_to_free_cash_flow",
        "counter_model_customer_bargaining_and_oversupply",
    ),
)
def test_required_catl_mechanism_shapes_are_formalizable(key: str) -> None:
    value = formal_mechanism(key=key)
    validate_formal_mechanism(value)
