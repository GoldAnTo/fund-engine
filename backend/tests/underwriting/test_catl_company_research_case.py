from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import app.underwriting.fixtures.catl_answerable_case as catl_fixture_module
import pytest
from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import (
    CatlCompanyResearchAdapter,
)
from app.underwriting.api.company_research_router import (
    _artifact_response,
    _run_response,
    _workspace_response,
)
from app.underwriting.domain.company_research import CompanyResearchIdentitySet
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CriticalInputDecision,
    CriticalInputSet,
    critical_input_successor_change,
)
from app.underwriting.domain.company_research_contracts import StrategyAssumptionSet
from app.underwriting.fixtures.catl_answerable_case import (
    CatlAnswerableCaseFixtureError,
    load_catl_answerable_case_fixture,
)
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.catl_baseline import CatlBaselineService
from app.underwriting.services.company_research_boundary import (
    resolve_company_research_boundary,
)
from app.underwriting.services.company_research_critical_input_confirmation import (
    CompanyResearchCriticalInputConfirmationService,
)
from app.underwriting.services.company_research_foundation import (
    company_research_adapter_for_company,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchBuildInput,
    CompanyResearchModelBuilder,
    EvidenceBuildMode,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_run import CompanyResearchRunService
from app.underwriting.services.company_research_sources import (
    CompanyResearchProviderInput,
    CompanyResearchSourceCompiler,
    CompanyResearchSourceService,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
    WorkbenchArtifact,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from sqlalchemy import func, select

CATL_COMPANY_KEY = "CN:300750:COMPANY"
CATL_SECURITY_KEY = "SZSE:300750"
_FIXTURE_ROOT = (
    Path(__file__).parents[2]
    / "app"
    / "underwriting"
    / "fixtures"
    / "catl_answerable_case"
)


def _custom_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "catl-answerable"
    shutil.copytree(_FIXTURE_ROOT, root)
    return root


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _refresh_fixture_hashes(root: Path, changed_name: str) -> None:
    from app.underwriting.hashing import canonical_hash

    changed_path = root / changed_name
    changed = _read_json(changed_path)
    if changed_name != "strategy_assumptions.json":
        changed["content_hash"] = canonical_hash(
            {key: value for key, value in changed.items() if key != "content_hash"}
        )
        _write_json(changed_path, changed)
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    files = manifest["files"]
    assert isinstance(files, list)
    for entry in files:
        assert isinstance(entry, dict)
        if entry["name"] == changed_name:
            entry["content_hash"] = hashlib.sha256(
                changed_path.read_bytes()
            ).hexdigest()
    manifest["content_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "content_hash"}
    )
    _write_json(manifest_path, manifest)


def _production_case(session):
    fixture = load_catl_answerable_case_fixture()
    loaded = ProductFoundationFixtureService(session, now=lambda: fixture.cutoff).load(
        load_product_foundation_fixture()
    )
    company = loaded.objects[CATL_COMPANY_KEY]
    initializer = CompanyResearchInitializer(session, now=lambda: fixture.cutoff)
    preview = initializer.preview(company_id=company.id, cutoff_at=fixture.cutoff)
    initialized = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=company.id,
        cutoff_at=fixture.cutoff,
        idempotency_key="catl-answerable-company-research",
    )
    governed = initializer.governed_inputs(
        project_id=initialized.project.id,
        cutoff_at=fixture.cutoff,
    )
    compiled = CompanyResearchSourceCompiler().compile_evidence_index(
        CompanyResearchProviderInput(
            preparation_id=initialized.preparation.id,
            project_id=initialized.project.id,
            company_external_key=CATL_COMPANY_KEY,
            request_hash=preview.input_hash,
            strategy_version=fixture.strategy_assumptions.strategy_version,
        )
    )
    model = CompanyResearchModelBuilder().build(
        CompanyResearchBuildInput(
            project_id=initialized.project.id,
            identity_set=CompanyResearchIdentitySet(
                company=preview.company,
                securities=preview.securities,
            ),
            cutoff_at=fixture.cutoff,
            required_return=fixture.strategy_assumptions.required_return.value,
            evidence_artifact_id=uuid4(),
            evidence_content_hash=canonical_hash(compiled.evidence_index_payload),
            evidence_payload=compiled.evidence_index_payload,
            gap_payload=compiled.research_gaps_payload,
            source_refs=compiled.source_refs,
            model_template=governed.model_template,
            strategy_assumptions=governed.strategy_assumptions,
            market_context=governed.market_context,
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )
    return fixture, preview, initialized, governed, compiled, model


def test_existing_catl_baseline_remains_evidence_only(session) -> None:
    cutoff = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)

    result = CatlBaselineService(session, now=lambda: cutoff).import_fixture(
        load_catl_fixture()
    )

    assert result.research_version.version_kind == "catl_economic_model_evidence_only"
    assert result.answerability.state == "not_answerable"


def test_answerable_case_is_a_distinct_authenticated_company_research_fixture() -> None:
    fixture = load_catl_answerable_case_fixture()

    assert fixture.company_external_key == CATL_COMPANY_KEY
    assert fixture.security_external_keys == (CATL_SECURITY_KEY,)
    assert fixture.base_currency == "CNY"
    assert fixture.cutoff.isoformat() == "2025-11-06T15:59:59+00:00"
    assert fixture.content_hash != load_catl_fixture().source_manifest.manifest_hash
    assert fixture.market_inputs.price.security_external_key == CATL_SECURITY_KEY
    assert fixture.market_inputs.price.currency == "CNY"


def test_catl_adapter_uses_shared_closed_model_and_assumption_contracts() -> None:
    fixture = load_catl_answerable_case_fixture()
    adapter = CatlCompanyResearchAdapter()

    assert adapter.supports(CATL_COMPANY_KEY)
    assert not adapter.supports("CN:300751:COMPANY")
    assert (
        adapter.validate_source_modules(
            fixture.company_external_key, fixture.business_modules
        )
        == fixture.business_modules
    )
    template = adapter.model_template()
    assumptions = adapter.strategy_assumptions(fixture.strategy_assumptions)

    assert template.company_external_key == CATL_COMPANY_KEY
    assert template.security_external_keys == (CATL_SECURITY_KEY,)
    assert tuple(item.scenario_id for item in template.scenario_mechanisms) == (
        "base",
        "bull",
        "bear",
    )
    assert tuple(item.driver_key for item in assumptions.driver_paths) == (
        "revenue",
        "operating_margin",
        "cash_tax_rate",
        "depreciation",
        "capex",
        "working_capital_change",
    )
    assert all(not item.source_refs for item in assumptions.driver_paths)


def test_catl_strategy_hash_uses_neutral_schema_not_alphabet_legacy_namespace() -> None:
    captured = load_catl_answerable_case_fixture().strategy_assumptions
    typed = CatlCompanyResearchAdapter().strategy_assumptions(captured)
    inputs = {
        "strategy_version": typed.strategy_version,
        "first_fiscal_year": typed.first_fiscal_year,
        "driver_paths": typed.driver_paths,
        "scenario_overrides": typed.scenario_overrides,
        "terminal_growth": typed.terminal_growth,
        "sensitivity_assumptions": typed.sensitivity_assumptions,
    }

    neutral = StrategyAssumptionSet.calculate_content_hash(**inputs)
    legacy = StrategyAssumptionSet.calculate_legacy_alphabet_content_hash(**inputs)

    assert typed.content_hash == neutral
    assert neutral != legacy


def test_exact_company_dispatch_selects_catl_and_rejects_unknown_companies() -> None:
    adapter = company_research_adapter_for_company(CATL_COMPANY_KEY)

    assert type(adapter) is CatlCompanyResearchAdapter
    with pytest.raises(
        ValidationError, match="company research adapter is unavailable"
    ):
        company_research_adapter_for_company("CN:300751:COMPANY")


def test_catl_boundary_is_distinct_and_binds_the_exact_manifest() -> None:
    fixture = load_catl_answerable_case_fixture()

    boundary = resolve_company_research_boundary(
        CATL_COMPANY_KEY,
        fixture.cutoff,
    )

    assert boundary.cutoff_at == fixture.cutoff
    assert boundary.basis_input.source_manifest_hash == fixture.content_hash
    with pytest.raises(ValidationError, match="manifest is not governed"):
        resolve_company_research_boundary(
            CATL_COMPANY_KEY,
            fixture.cutoff,
            source_manifest_hash="0" * 64,
        )
    with pytest.raises(ValidationError, match="boundary is unavailable"):
        resolve_company_research_boundary("CN:300751:COMPANY", fixture.cutoff)


def test_catl_source_compiler_dispatches_the_exact_frozen_company_fixture() -> None:
    fixture = load_catl_answerable_case_fixture()
    provider_input = CompanyResearchProviderInput(
        preparation_id=uuid4(),
        project_id=uuid4(),
        company_external_key=CATL_COMPANY_KEY,
        request_hash="request",
        strategy_version=fixture.strategy_assumptions.strategy_version,
    )

    compiled = CompanyResearchSourceCompiler().compile_evidence_index(provider_input)

    assert compiled.input_hash == fixture.content_hash
    assert compiled.evidence_index_payload["company_external_key"] == CATL_COMPANY_KEY
    assert compiled.evidence_index_payload["security_external_keys"] == [
        CATL_SECURITY_KEY
    ]
    assert compiled.source_refs


def test_catl_source_provider_failure_is_neutral_and_persists_no_artifact(
    session,
) -> None:
    fixture, _preview, initialized, _governed, _compiled, _model = _production_case(
        session
    )

    def unavailable():
        raise CatlAnswerableCaseFixtureError("fixture source unavailable")

    outcome = CompanyResearchSourceService(
        session,
        now=lambda: fixture.cutoff,
        fixture_loaders={CATL_COMPANY_KEY: unavailable},
    ).prepare_evidence_index(preparation_id=initialized.preparation.id)

    assert outcome.status == "recoverable_failure"
    assert outcome.error == {
        "code": "company_research_source_unavailable",
        "recoverable": True,
    }
    assert (
        session.scalar(
            select(func.count())
            .select_from(CompanyResearchArtifactVersion)
            .where(CompanyResearchArtifactVersion.project_id == initialized.project.id)
        )
        == 0
    )


def test_catl_governed_evidence_is_readable_through_workspace_api(session) -> None:
    fixture, _preview, initialized, _governed, _compiled, _model = _production_case(
        session
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: fixture.cutoff)
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "building_model"

    workspace = CompanyResearchWorkbench(
        session,
        now=lambda: fixture.cutoff,
    ).workspace(project_id=initialized.project.id)
    serialized = _workspace_response(
        workspace,
        expected_project_id=initialized.project.id,
    ).model_dump(mode="json", by_alias=True)
    evidence = next(
        item for item in serialized["artifacts"] if item["kind"] == "evidence_index"
    )

    assert evidence["payload"]["counterevidence_fact_keys"] == [
        "revenue_yoy_change_2024"
    ]
    assert evidence["payload"]["next_verification_events"]


def test_catl_machine_draft_is_serializable_through_run_api(session, api_client) -> None:
    fixture, _preview, initialized, _governed, _compiled, _model = _production_case(
        session
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: fixture.cutoff)
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "building_model"
    model_claim = worker.claim_next()
    assert model_claim is not None
    assert worker.run_claim(model_claim) == "awaiting_judgment_review"

    response = _run_response(
        CompanyResearchRunService(session, now=lambda: fixture.cutoff).read(
            initialized.project.id
        )
    ).model_dump(mode="json", by_alias=True)
    business_map = next(
        item for item in response["workspace"]["artifacts"] if item["kind"] == "business_map"
    )

    assert any(
        item["observation"]["currency"] is None
        for module in business_map["payload"]["modules"]
        for item in module["classified_evidence"]
    )
    api_response = api_client.get(
        f"/api/underwriting/v1/product/company-research/projects/{initialized.project.id}/run"
    )
    assert api_response.status_code == 200


def test_catl_real_workers_select_only_blocking_inputs_and_reach_ready_to_freeze(
    session,
) -> None:
    fixture, _preview, initialized, _governed, _compiled, model = _production_case(
        session
    )
    worker = CompanyResearchPreparationWorker(session, now=lambda: fixture.cutoff)

    source_claim = worker.claim_next()
    assert source_claim is not None and source_claim.step == "evidence_index"
    assert worker.run_claim(source_claim) == "building_model"
    model_claim = worker.claim_next()
    assert model_claim is not None and model_claim.step == "model_bundle"
    assert worker.run_claim(model_claim) == "awaiting_judgment_review"

    repository = CompanyResearchRepository(session)
    critical_inputs = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    memo = repository.current_artifact(initialized.project.id, "memo")
    scenario_set = repository.current_artifact(initialized.project.id, "scenario_set")
    valuation_set = repository.current_artifact(
        initialized.project.id, "valuation_set"
    )

    assert critical_inputs is not None
    assert len(critical_inputs.payload["inputs"]) == 55
    assert {item["decision"] for item in critical_inputs.payload["inputs"]} == {
        "pending"
    }
    non_blocking_gap_key = "unknown:maintenance_vs_growth_capex_not_disaggregated"
    assert any(
        node.key == non_blocking_gap_key
        for node in model.critical_input_dependency_graph.unknown_nodes
    )
    assert all(
        edge.parent_key != non_blocking_gap_key
        for edge in model.critical_input_dependency_graph.edges
    )
    assert all(
        item["key"] != non_blocking_gap_key
        for item in critical_inputs.payload["inputs"]
    )
    assert memo is not None
    assert memo.payload["candidate_status"] == "machine_draft"
    assert memo.payload["assessment_status"] == "answerable"
    assert memo.payload["strongest_counterevidence"]
    assert memo.payload["next_verification_events"]
    assert scenario_set is not None
    assert {item["scenario_id"] for item in scenario_set.payload["scenarios"]} == {
        "base",
        "bull",
        "bear",
    }
    assert valuation_set is not None
    assert valuation_set.payload["security_value_ranges"][0]["value_currency"] == (
        "CNY"
    )
    assert valuation_set.payload["sensitivity_analyses"]

    confirmation = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: fixture.cutoff
    )
    decoded = CompanyResearchArtifactCodec.decode(
        "critical_inputs", critical_inputs.payload
    )
    assert type(decoded) is CriticalInputSet
    source_refs = deepcopy(critical_inputs.source_refs)
    parent_id = critical_inputs.id
    parent_content_hash = critical_inputs.content_hash
    version = critical_inputs.version
    fixture_rows = []
    # Prove every selected item has a valid one-at-a-time domain successor while
    # preparing an authenticated incremental chain without 54 repeated full reads.
    for selected in decoded.inputs[:-1]:
        successor = CriticalInputSet(
            inputs=tuple(
                replace(item, decision=CriticalInputDecision.CONFIRMED)
                if item.key == selected.key
                else item
                for item in decoded.inputs
            )
        )
        changed = critical_input_successor_change(decoded, successor)
        assert changed.key == selected.key
        assert changed.input_fingerprint == selected.input_fingerprint
        payload = CompanyResearchArtifactCodec.encode("critical_inputs", successor)
        input_hash = repository.critical_input_successor_input_hash(
            parent_content_hash=parent_content_hash,
            input_key=selected.key,
            input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.CONFIRMED,
            replacement=None,
        )
        artifact_id = uuid4()
        version += 1
        content_hash = repository.artifact_content_hash(
            project_id=initialized.project.id,
            kind="critical_inputs",
            version=version,
            supersedes_id=parent_id,
            parent_content_hash=parent_content_hash,
            input_hash=input_hash,
            payload=payload,
            source_refs=source_refs,
        )
        fixture_rows.append(
            CompanyResearchArtifactVersion(
                id=artifact_id,
                project_id=initialized.project.id,
                kind="critical_inputs",
                version=version,
                supersedes_id=parent_id,
                parent_content_hash=parent_content_hash,
                input_hash=input_hash,
                payload=payload,
                source_refs=deepcopy(source_refs),
                content_hash=content_hash,
                created_at=fixture.cutoff,
            )
        )
        decoded = successor
        parent_id = artifact_id
        parent_content_hash = content_hash
    assert sum(
        item.decision is CriticalInputDecision.PENDING for item in decoded.inputs
    ) == 1
    session.add_all(fixture_rows)
    session.flush()

    selected = next(
        item
        for item in decoded.inputs
        if item.decision is CriticalInputDecision.PENDING
    )
    run = confirmation.decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=parent_id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.CONFIRMED,
    )
    preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    assert preparation is not None
    assert (preparation.status, preparation.current_step, preparation.progress) == (
        "ready_to_freeze",
        "memo",
        95,
    )
    assert run.status.value == "completed"
    assert run.progress == 95
    confirmed_memo = repository.current_artifact(initialized.project.id, "memo")
    assert confirmed_memo is not None
    memo_value = CompanyResearchArtifactCodec.decode(
        "memo",
        {
            key: value
            for key, value in confirmed_memo.payload.items()
            if key != "_lineage"
        },
    )
    assert memo_value.assessment_status == "answerable"
    assert "maintenance_vs_growth_capex_not_disaggregated" in memo_value.gap_keys
    assert memo_value.next_verification_events
    confirmed_gaps = repository.current_artifact(
        initialized.project.id, "research_gaps"
    )
    assert confirmed_gaps is not None
    assert any(
        gap["gap_key"] == "maintenance_vs_growth_capex_not_disaggregated"
        for gap in confirmed_gaps.payload["gaps"]
    )


def test_catl_initializer_and_governed_model_handoff_use_exact_company_dispatch(
    session,
) -> None:
    fixture, preview, initialized, governed, compiled, model = _production_case(session)

    assert preview.company.external_key == CATL_COMPANY_KEY
    assert preview.security_external_keys == (CATL_SECURITY_KEY,)
    assert initialized.basis.source_manifest_hash == fixture.content_hash
    assert governed.model_template.company_external_key == CATL_COMPANY_KEY
    assert governed.strategy_assumptions.content_hash == (
        fixture.strategy_assumptions.content_hash
    )
    assert governed.market_context is not None
    assert governed.market_context.security_external_keys == (CATL_SECURITY_KEY,)
    CompanyResearchRepository(session).validate_market_snapshot_bindings(
        project_id=initialized.project.id,
        bindings=governed.market_context.snapshot_bindings,
        cutoff_at=fixture.cutoff,
    )

    assert model.assessment.status == "answerable"
    assert model.valuation_set is not None
    critical_inputs = {
        node.key: node.candidate
        for node in model.critical_input_dependency_graph.input_nodes
    }
    assert critical_inputs["assumption:revenue"].unit == "CNY million"
    assert not any(key.startswith("market:fx:") for key in critical_inputs)
    payload = CompanyResearchArtifactCodec.encode("valuation_set", model.valuation_set)
    ranges = payload["security_value_ranges"]
    assert isinstance(ranges, list)
    assert ranges[0]["value_currency"] == "CNY"
    assert "value_per_share" in ranges[0]
    assert "usd_per_share" not in ranges[0]

    legacy = deepcopy(payload)
    legacy_ranges = legacy["security_value_ranges"]
    assert isinstance(legacy_ranges, list)
    legacy_item = legacy_ranges[0]
    legacy_item["usd_per_share"] = legacy_item.pop("value_per_share")
    legacy_item["cny_return"] = legacy_item.pop("base_currency_return")
    legacy_item.pop("value_currency")
    legacy_item.pop("schema_version")
    with pytest.raises(ValidationError, match="valuation_set payload is invalid"):
        CompanyResearchArtifactCodec.validate_payload("valuation_set", legacy)
    legacy.pop("sensitivity_analyses")
    decoded_legacy = CompanyResearchArtifactCodec.decode("valuation_set", legacy)
    assert decoded_legacy.security_value_ranges[0].value_currency == "USD"
    assert decoded_legacy.security_value_ranges[0].value_per_share == (
        model.valuation_set.security_value_ranges[0].value_per_share
    )


def test_answerable_case_produces_governed_scenarios_sensitivity_and_judgment(
    session,
) -> None:
    _fixture, _preview, _initialized, _governed, _compiled, model = _production_case(
        session
    )
    valuation = model.valuation_set

    assert not hasattr(catl_fixture_module, "build_catl_answerable_research_case")
    assert model.assessment.status == "answerable"
    assert valuation is not None
    scenario_ids = tuple(item.scenario_id for item in valuation.scenario_dcf_values)
    assert scenario_ids[0] == "base"
    assert set(scenario_ids) == {"base", "bull", "bear"}
    security_range = valuation.security_value_ranges[0]
    assert security_range.value_currency == "CNY"
    assert security_range.value_per_share.minimum > Decimal(0)
    assert {item.variable_key for item in valuation.sensitivity_analyses} == {
        "required_return",
        "terminal_growth",
    }
    assert all(
        item.value_currency == "CNY" and item.equation_id == "dcf_sensitivity.v1"
        for item in valuation.sensitivity_analyses
    )
    assert tuple(
        item.fact_key for item in model.judgment_context.strongest_counterevidence
    ) == ("revenue_yoy_change_2024",)
    assert model.memo.strongest_counterevidence == (
        model.judgment_context.strongest_counterevidence
    )
    assert model.memo.next_verification_events
    assert all("%" not in item for item in model.memo.next_verification_events)


def test_catl_valuation_real_api_serialization_is_currency_neutral(session) -> None:
    fixture, _preview, initialized, governed, compiled, model = _production_case(
        session
    )
    assert model.valuation_set is not None
    payload = CompanyResearchArtifactCodec.encode("valuation_set", model.valuation_set)
    parent_refs = [
        {
            "artifact_id": str(uuid4()),
            "artifact_kind": kind,
            "content_hash": "1" * 64,
        }
        for kind in ("scenario_set", "financial_bridge")
    ]
    payload["_lineage"] = {
        "artifact_refs": parent_refs,
        "market_snapshot_ids": [
            str(item.snapshot_id) for item in governed.market_context.snapshot_bindings
        ],
        "market_snapshot_bindings": [
            CompanyResearchRepository._market_binding_payload(item)
            for item in governed.market_context.snapshot_bindings
        ],
    }
    response = _artifact_response(
        WorkbenchArtifact(
            id=uuid4(),
            kind="valuation_set",
            version=1,
            input_hash="2" * 64,
            content_hash=canonical_hash(payload),
            payload=payload,
            source_refs=compiled.source_refs,
        ),
        project_id=initialized.project.id,
        context={
            "cutoff": fixture.cutoff.isoformat(),
            "strategy_version": fixture.strategy_assumptions.strategy_version,
            "financial_currency": "CNY",
            "base_currency": "CNY",
        },
    ).model_dump(mode="json", by_alias=True)

    serialized = response["payload"]
    value_range = serialized["security_value_ranges"][0]
    assert set(value_range) == {
        "security_external_key",
        "value_per_share",
        "value_currency",
        "base_currency_return",
    }
    assert value_range["value_currency"] == "CNY"
    assert value_range["value_per_share"]["minimum"]["currency"] == "CNY"
    assert value_range["base_currency_return"]["minimum"]["currency"] == "CNY"
    assert {item["variable_key"] for item in serialized["sensitivity_analyses"]} == {
        "required_return",
        "terminal_growth",
    }
    assert "usd_per_share" not in str(serialized)
    assert "cny_return" not in str(serialized)


def test_every_production_numeric_input_has_typed_dependency_provenance(
    session,
) -> None:
    _fixture, _preview, _initialized, _governed, _compiled, model = _production_case(
        session
    )
    graph = model.critical_input_dependency_graph

    numeric_inputs = tuple(
        node.candidate
        for node in graph.input_nodes
        if type(node.candidate.value) is Decimal
    )
    assert numeric_inputs
    assert all(
        candidate.source_ref is not None
        or candidate.assumption_key is not None
        or candidate.equation_id is not None
        or candidate.gap_key is not None
        for candidate in numeric_inputs
    )
    assert {
        candidate.assumption_key
        for candidate in numeric_inputs
        if candidate.key.startswith("assumption:sensitivity:")
    } == {
        "catl-machine-candidate.v1:sensitivity_required_return_low",
        "catl-machine-candidate.v1:sensitivity_required_return_high",
        "catl-machine-candidate.v1:sensitivity_terminal_growth_low",
        "catl-machine-candidate.v1:sensitivity_terminal_growth_high",
    }
    assert model.valuation_set is not None
    assert all(
        analysis.low_assumption_key and analysis.high_assumption_key
        for analysis in model.valuation_set.sensitivity_analyses
    )


@pytest.mark.parametrize("replacement", (None, "0"))
def test_required_fact_cannot_be_missing_or_serialized_as_zero(
    tmp_path: Path,
    replacement: str | None,
) -> None:
    root = _custom_fixture(tmp_path)
    path = root / "source_facts.json"
    source = _read_json(path)
    facts = source["facts"]
    assert isinstance(facts, list)
    target = next(
        item
        for item in facts
        if isinstance(item, dict) and item.get("fact_key") == "capex_2024"
    )
    if replacement is None:
        facts.remove(target)
    else:
        target["value"] = replacement
    _write_json(path, source)
    _refresh_fixture_hashes(root, "source_facts.json")

    with pytest.raises(
        CatlAnswerableCaseFixtureError,
        match="required inputs are incomplete|required input cannot be zero",
    ):
        load_catl_answerable_case_fixture(root)


def test_required_price_and_scenario_inputs_cannot_be_removed(tmp_path: Path) -> None:
    market_root = _custom_fixture(tmp_path / "market")
    market_path = market_root / "market_inputs.json"
    market = _read_json(market_path)
    market.pop("price")
    _write_json(market_path, market)
    _refresh_fixture_hashes(market_root, "market_inputs.json")
    with pytest.raises(
        CatlAnswerableCaseFixtureError, match="market inputs.*invalid fields"
    ):
        load_catl_answerable_case_fixture(market_root)

    strategy_root = _custom_fixture(tmp_path / "strategy")
    strategy_path = strategy_root / "strategy_assumptions.json"
    strategy = _read_json(strategy_path)
    paths = strategy["driver_paths"]
    assert isinstance(paths, list)
    paths.pop()
    _write_json(strategy_path, strategy)
    _refresh_fixture_hashes(strategy_root, "strategy_assumptions.json")
    with pytest.raises(
        CatlAnswerableCaseFixtureError, match="driver paths are incomplete"
    ):
        load_catl_answerable_case_fixture(strategy_root)


def test_derived_required_fact_must_reconcile_to_its_reported_parents(
    tmp_path: Path,
) -> None:
    root = _custom_fixture(tmp_path)
    path = root / "source_facts.json"
    source = _read_json(path)
    facts = source["facts"]
    assert isinstance(facts, list)
    target = next(
        item
        for item in facts
        if isinstance(item, dict)
        and item.get("fact_key") == "working_capital_change_2024"
    )
    target["value"] = "-1"
    _write_json(path, source)
    _refresh_fixture_hashes(root, "source_facts.json")

    with pytest.raises(
        CatlAnswerableCaseFixtureError,
        match="derived fact does not reconcile",
    ):
        load_catl_answerable_case_fixture(root)


def test_governed_metric_contract_rejects_revenue_currency_drift(
    tmp_path: Path,
) -> None:
    root = _custom_fixture(tmp_path)
    path = root / "source_facts.json"
    source = _read_json(path)
    facts = source["facts"]
    assert isinstance(facts, list)
    revenue = next(
        item
        for item in facts
        if isinstance(item, dict) and item.get("fact_key") == "revenue_2024"
    )
    revenue["currency"] = None
    _write_json(path, source)
    _refresh_fixture_hashes(root, "source_facts.json")

    with pytest.raises(
        CatlAnswerableCaseFixtureError,
        match="governed metric contract",
    ):
        load_catl_answerable_case_fixture(root)


def test_research_gap_keys_must_be_unique(tmp_path: Path) -> None:
    root = _custom_fixture(tmp_path)
    path = root / "source_facts.json"
    source = _read_json(path)
    gaps = source["research_gaps"]
    assert isinstance(gaps, list) and gaps
    gaps.append(deepcopy(gaps[0]))
    _write_json(path, source)
    _refresh_fixture_hashes(root, "source_facts.json")

    with pytest.raises(
        CatlAnswerableCaseFixtureError,
        match="gap keys must be unique",
    ):
        load_catl_answerable_case_fixture(root)
