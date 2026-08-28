"""Closed HTTP contract for the high-level company research flow."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.underwriting.api.company_research_schemas import (
    CompanyResearchArtifactResponse,
    CompanyResearchNumericObservationResponse,
    CompanyResearchWorkbenchModuleResponse,
    CompanyResearchWorkspaceCompanyResponse,
    CompanyResearchWorkspaceDraftResponse,
    CompanyResearchWorkspacePreparationResponse,
    CompanyResearchWorkspaceResponse,
)
from app.underwriting.api.company_research_router import _workspace_response
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.models.operational import Job
from app.underwriting.persistence.company_research_models import (
    CompanyResearchPreparation,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from tests.underwriting.test_company_research_workbench import _model_workspace

BASE = "/api/underwriting/v1/product/company-research"
NOW = datetime(2026, 8, 25, 9, tzinfo=UTC)
HASH = "a" * 64
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000001")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000003")
EXPECTED_MODULES = (
    "overview",
    "business_map",
    "operating_drivers",
    "evidence_and_gaps",
    "industry_competition_regulation",
    "financials_cash_flow_capital_allocation",
    "scenarios_valuation_implied_expectations",
    "counterevidence_risks_next_checks",
    "versions_changes_memo",
)


def _evidence_artifact_payload() -> dict:
    return {
        "id": ARTIFACT_ID,
        "project_id": PROJECT_ID,
        "kind": "evidence_index",
        "version": 1,
        "input_hash": HASH,
        "content_hash": HASH,
        "payload": {
            "fixture_content_hash": HASH,
            "cutoff": NOW.isoformat(),
            "company_external_key": "US:ALPHABET:COMPANY",
            "security_external_keys": ["NASDAQ:GOOG", "NASDAQ:GOOGL"],
            "facts": [
                {
                    "fact_key": "reported_revenue",
                    "company_external_key": "US:ALPHABET:COMPANY",
                    "business_module": "search_and_other_ads",
                    "metric_key": "revenue",
                    "observation": {
                        "key": "revenue",
                        "value": "1",
                        "unit": "USD_million",
                        "currency": "USD",
                        "period": "2025-01-01/2025-12-31",
                        "state": "reported",
                        "source_ref": {
                            "kind": "external",
                            "fact_key": "reported_revenue",
                            "source_role": "regulatory_filing",
                            "source_url": "https://example.test/source",
                            "source_locator": "p. 1",
                            "raw_hash": HASH,
                        },
                        "gap_key": None,
                        "assumption_key": None,
                    },
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "published_at": NOW.isoformat(),
                    "available_at": NOW.isoformat(),
                    "source_role": "regulatory_filing",
                    "source_url": "https://example.test/source",
                    "source_locator": "p. 1",
                    "raw_hash": HASH,
                }
            ],
        },
        "source_refs": [
            {
                "source_url": "https://example.test/source",
                "raw_hash": HASH,
                "source_locator": "p. 1",
                "source_role": "regulatory_filing",
            }
        ],
    }


def _workspace_contract(modules, artifact=None) -> CompanyResearchWorkspaceResponse:
    artifact = artifact or CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    return CompanyResearchWorkspaceResponse(
        project_id=PROJECT_ID,
        company=CompanyResearchWorkspaceCompanyResponse(
            id=COMPANY_ID,
            object_id=COMPANY_ID,
            external_key="US:ALPHABET:COMPANY",
            canonical_name="Alphabet Inc.",
        ),
        preparation=CompanyResearchWorkspacePreparationResponse(
            id=ARTIFACT_ID,
            status="awaiting_evidence_review",
            current_step="research_gaps",
            progress=25,
            error=None,
        ),
        artifacts=(artifact,),
        modules=modules,
        source_count=1,
        gap_count=0,
        draft=CompanyResearchWorkspaceDraftResponse(
            id=ARTIFACT_ID, lock_version=1, base_revision_id=None
        ),
        selected_revision=None,
        change_summary={
            "artifact_versions": {"evidence_index": 1},
            "reviewed_fact_count": 0,
        },
    )


def test_company_research_wire_contract_rejects_missing_numeric_metadata_and_unknown_fields() -> (
    None
):
    missing_unit = _evidence_artifact_payload()
    missing_unit["payload"]["facts"][0]["observation"].pop("unit")
    unknown = _evidence_artifact_payload()
    unknown["payload"]["unexpected"] = True

    with pytest.raises(PydanticValidationError):
        CompanyResearchArtifactResponse.model_validate(missing_unit)
    with pytest.raises(PydanticValidationError):
        CompanyResearchArtifactResponse.model_validate(unknown)


def test_company_research_wire_contract_rejects_duplicate_refs_and_valuation_without_exact_market_refs() -> (
    None
):
    duplicate = _evidence_artifact_payload()
    duplicate["source_refs"].append(dict(duplicate["source_refs"][0]))
    valuation = {
        "id": ARTIFACT_ID,
        "kind": "valuation_set",
        "version": 1,
        "input_hash": HASH,
        "content_hash": HASH,
        "payload": {
            "scenario_dcf_values": [],
            "reverse_dcf": None,
            "security_value_ranges": [],
            "required_return": "0.12",
            "required_return_comparisons": [],
            "_lineage": {
                "artifact_refs": [],
                "market_snapshot_ids": [],
                "market_snapshot_bindings": [],
            },
        },
        "source_refs": [],
    }

    with pytest.raises(PydanticValidationError):
        CompanyResearchArtifactResponse.model_validate(duplicate)
    with pytest.raises(PydanticValidationError):
        CompanyResearchArtifactResponse.model_validate(valuation)


def test_company_research_workspace_requires_exact_module_order_and_state_artifact_pairs() -> (
    None
):
    artifact = CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    artifact_ref = {
        "id": artifact.root.id,
        "kind": artifact.root.kind,
        "content_hash": artifact.root.content_hash,
    }
    modules = tuple(
        CompanyResearchWorkbenchModuleResponse(
            key=key,
            state=(
                "needs_review"
                if key == "evidence_and_gaps"
                else "not_started"
            ),
            artifact_refs=(artifact_ref,) if key == "evidence_and_gaps" else (),
            valuation_state=(
                "pending"
                if key == "scenarios_valuation_implied_expectations"
                else "not_applicable"
            ),
        )
        for key in EXPECTED_MODULES
    )
    _workspace_contract(modules)

    with pytest.raises(PydanticValidationError):
        _workspace_contract((modules[1], modules[0], *modules[2:]))
    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkbenchModuleResponse(
            key="business_map",
            state="ready",
            artifact_refs=(artifact_ref,),
            valuation_state="not_applicable",
        )


@pytest.mark.parametrize("state", ("not_started", "preparing", "blocked"))
def test_company_research_empty_module_states_are_closed(state: str) -> None:
    module = CompanyResearchWorkbenchModuleResponse(
        key="operating_drivers",
        state=state,
        artifact_refs=(),
        valuation_state="not_applicable",
    )
    assert module.state == state


@pytest.mark.parametrize("state", ("ready", "needs_review"))
def test_company_research_artifact_module_states_are_closed(state: str) -> None:
    artifact = CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    module = CompanyResearchWorkbenchModuleResponse(
        key="evidence_and_gaps",
        state=state,
        artifact_refs=(
            {
                "id": artifact.root.id,
                "kind": artifact.root.kind,
                "content_hash": artifact.root.content_hash,
            },
        ),
        valuation_state="not_applicable",
    )
    assert module.state == state


def test_complete_model_bundle_constructs_all_nine_discriminated_artifact_variants(
    session,
) -> None:
    initialized, workbench, _repository = _model_workspace(session)
    kinds = (
        "evidence_index",
        "research_gaps",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
        "judgment_context",
        "memo",
    )

    response = _workspace_response(
        workbench.workspace(project_id=initialized.project.id),
        expected_project_id=initialized.project.id,
    )

    assert tuple(item.kind for item in response.artifacts) == kinds


def test_complete_pipeline_route_exposes_exact_project_status_and_ordered_modules(
    api_client, session
) -> None:
    initialized, _workbench, _repository = _model_workspace(session)

    response = api_client.get(f"{BASE}/projects/{initialized.project.id}/workspace")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(initialized.project.id)
    assert body["preparation"] == {
        "schema_version": "underwriting.v1",
        "id": str(initialized.preparation.id),
        "status": "awaiting_judgment_review",
        "current_step": "judgment_context",
        "progress": 85,
        "error": None,
    }
    assert tuple(item["kind"] for item in body["artifacts"]) == (
        "evidence_index",
        "research_gaps",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
        "judgment_context",
        "memo",
    )
    assert tuple(module["key"] for module in body["modules"]) == EXPECTED_MODULES
    assert all(module["state"] == "ready" for module in body["modules"])


def test_workspace_response_rejects_nonexistent_foreign_or_substituted_registry_refs(
    api_client, session
) -> None:
    initialized, _workbench, _repository = _model_workspace(session)
    response = api_client.get(f"{BASE}/projects/{initialized.project.id}/workspace")
    assert response.status_code == 200, response.text
    body = response.json()
    nonexistent = deepcopy(body)
    nonexistent["modules"][1]["artifact_refs"][0]["id"] = str(COMPANY_ID)
    foreign = deepcopy(body)
    foreign["artifacts"][0]["project_id"] = str(COMPANY_ID)
    substituted = deepcopy(body)
    substituted["modules"][1]["artifact_refs"][0]["content_hash"] = "b" * 64
    lineage_substitution = deepcopy(body)
    business = next(
        item for item in lineage_substitution["artifacts"] if item["kind"] == "business_map"
    )
    business["payload"]["_lineage"]["artifact_refs"][0]["content_hash"] = "b" * 64
    duplicate = deepcopy(body)
    duplicate["artifacts"].append(deepcopy(duplicate["artifacts"][0]))

    for invalid in (
        nonexistent,
        foreign,
        substituted,
        lineage_substitution,
        duplicate,
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchWorkspaceResponse.model_validate(invalid)


def _alphabet_id(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    return loaded.objects["US:ALPHABET:COMPANY"].id


def _preview(api_client, company_id, *, cutoff_at: datetime = NOW):
    response = api_client.post(
        f"{BASE}/preview",
        json={"company_id": str(company_id), "cutoff_at": cutoff_at.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _initialize(
    api_client,
    company_id,
    preview_hash: str,
    *,
    cutoff_at: datetime = NOW,
):
    return api_client.post(
        f"{BASE}/initializations",
        headers={"Idempotency-Key": "alphabet-api-initialization"},
        json={
            "company_id": str(company_id),
            "cutoff_at": cutoff_at.isoformat(),
            "preview_hash": preview_hash,
        },
    )


def _run_public_company_research_pipeline(api_client, session) -> dict:
    cutoff = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id, cutoff_at=cutoff)
    initialized = _initialize(
        api_client,
        company_id,
        preview["preview_hash"],
        cutoff_at=cutoff,
    )
    assert initialized.status_code == 201, initialized.text
    project_id = initialized.json()["project_id"]
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert workspace.status_code == 200, workspace.text
    evidence = next(
        item
        for item in workspace.json()["artifacts"]
        if item["kind"] == "evidence_index"
    )
    current = evidence
    for fact in evidence["payload"]["facts"]:
        reviewed = api_client.post(
            f"{BASE}/projects/{project_id}/evidence-reviews",
            json={
                "evidence_artifact_id": current["id"],
                "fact_key": fact["fact_key"],
                "decision": "confirmed",
                "expected_head_id": current["id"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()["evidence_artifact"]
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    outcome = worker.run_claim(claim)
    preparation = session.get(CompanyResearchPreparation, claim.preparation_id)
    assert outcome == "awaiting_judgment_review", (
        preparation.status if preparation is not None else None,
        preparation.last_error_code if preparation is not None else None,
    )
    result = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert result.status_code == 200, result.text
    return result.json()


def test_public_pipeline_exposes_every_current_artifact_once_and_references_them_from_modules(
    api_client, session
) -> None:
    body = _run_public_company_research_pipeline(api_client, session)

    assert body["preparation"]["status"] == "awaiting_judgment_review"
    assert body["preparation"]["progress"] == 85
    assert tuple(module["key"] for module in body["modules"]) == EXPECTED_MODULES
    assert tuple(artifact["kind"] for artifact in body["artifacts"]) == (
        "evidence_index",
        "research_gaps",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "judgment_context",
        "memo",
    )
    assert len({artifact["id"] for artifact in body["artifacts"]}) == 8
    scenario_module = next(
        module
        for module in body["modules"]
        if module["key"] == "scenarios_valuation_implied_expectations"
    )
    assert [ref["kind"] for ref in scenario_module["artifact_refs"]] == [
        "scenario_set"
    ]
    assert scenario_module["state"] == "ready"
    assert scenario_module["valuation_state"] == "blocked"
    overview = next(module for module in body["modules"] if module["key"] == "overview")
    assert [ref["kind"] for ref in overview["artifact_refs"]] == [
        "judgment_context"
    ]
    evidence = next(
        item for item in body["artifacts"] if item["kind"] == "evidence_index"
    )
    facts = {item["fact_key"]: item for item in evidence["payload"]["facts"]}
    driver_map = next(
        item for item in body["artifacts"] if item["kind"] == "driver_map"
    )
    reported = [
        observation
        for driver in driver_map["payload"]["drivers"]
        for observation in driver["values"]
        if observation["state"] == "reported"
    ]
    assert reported
    for observation in reported:
        source = observation["source_ref"]
        fact = facts[source["fact_key"]]
        assert observation["unit"] == fact["observation"]["unit"]
        assert observation["currency"] == fact["observation"]["currency"]
        assert observation["period"] == fact["observation"]["period"]
        assert source == fact["observation"]["source_ref"]


def test_recoverable_workspace_exposes_typed_error_semantics(api_client, session) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    preparation = session.scalar(select(CompanyResearchPreparation))
    job = session.scalar(select(Job))
    assert preparation is not None and job is not None
    preparation.status = "recoverable_failure"
    preparation.current_step = "evidence_index"
    preparation.progress = 10
    preparation.next_attempt_at = NOW + timedelta(minutes=5)
    preparation.last_error_code = "source_unavailable"
    job.status = "failed"
    job.step = "evidence_index"
    job.error = "source_unavailable"
    session.commit()

    response = api_client.get(f"{BASE}/projects/{project_id}/workspace")

    assert response.status_code == 200, response.text
    assert response.json()["preparation"]["error"] == {
        "schema_version": "underwriting.v1",
        "code": "source_unavailable",
        "failed_step": "evidence_index",
        "retryable": True,
        "next_attempt_at": (NOW + timedelta(minutes=5)).isoformat().replace(
            "+00:00", "Z"
        ),
    }


def _numeric_observation_payload() -> dict:
    return {
        "key": "revenue",
        "value": "119796",
        "unit": "USD_million",
        "currency": "USD",
        "period": "2026-Q2",
        "state": "reported",
        "source_ref": {
            "kind": "external",
            "fact_key": "q2_2026_revenue",
            "source_role": "regulatory_filing",
            "source_url": "https://www.sec.gov/example",
            "source_locator": "income statement",
            "raw_hash": HASH,
        },
        "gap_key": None,
        "assumption_key": None,
    }


@pytest.mark.parametrize(
    "missing",
    ("value", "unit", "currency", "period", "state"),
)
def test_numeric_observation_requires_each_semantic_field(missing: str) -> None:
    payload = _numeric_observation_payload()
    payload.pop(missing)

    with pytest.raises(PydanticValidationError):
        CompanyResearchNumericObservationResponse.model_validate(payload)


def test_numeric_observation_requires_exactly_one_provenance_path() -> None:
    ambiguous = _numeric_observation_payload()
    ambiguous["assumption_key"] = "alphabet-candidate.v1:revenue"
    absent = _numeric_observation_payload()
    absent["source_ref"] = None

    with pytest.raises(PydanticValidationError):
        CompanyResearchNumericObservationResponse.model_validate(ambiguous)
    with pytest.raises(PydanticValidationError):
        CompanyResearchNumericObservationResponse.model_validate(absent)


def test_numeric_observation_derived_state_requires_computation_provenance() -> None:
    invalid = _numeric_observation_payload()
    invalid["state"] = "derived"

    with pytest.raises(PydanticValidationError):
        CompanyResearchNumericObservationResponse.model_validate(invalid)


def test_scenario_module_rejects_valuation_without_its_scenario() -> None:
    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkbenchModuleResponse(
            key="scenarios_valuation_implied_expectations",
            state="ready",
            artifact_refs=(
                {
                    "id": ARTIFACT_ID,
                    "kind": "valuation_set",
                    "content_hash": HASH,
                },
            ),
            valuation_state="pending",
        )


@pytest.mark.parametrize(
    ("status", "retryable", "next_attempt_at"),
    (
        ("recoverable_failure", True, None),
        ("blocked", False, NOW),
    ),
)
def test_failed_preparation_requires_closed_retry_timing(
    status: str, retryable: bool, next_attempt_at: datetime | None
) -> None:
    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkspacePreparationResponse(
            id=ARTIFACT_ID,
            status=status,
            current_step="evidence_index",
            progress=10,
            error={
                "code": "source_unavailable",
                "failed_step": "evidence_index",
                "retryable": retryable,
                "next_attempt_at": next_attempt_at,
            },
        )


def test_company_research_routes_are_closed_and_do_not_fall_through_to_legacy_routes(
    api_client,
) -> None:
    assert api_client.post(f"{BASE}/preview", json={}).status_code == 422
    assert api_client.post(f"{BASE}/initializations", json={}).status_code == 422
    assert (
        api_client.get(
            f"{BASE}/projects/00000000-0000-4000-8000-000000000001"
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            f"{BASE}/projects/00000000-0000-4000-8000-000000000001/retry"
        ).status_code
        == 404
    )


def test_preview_initialization_and_status_expose_only_the_high_level_company_flow(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)

    assert set(preview) == {
        "schema_version",
        "company",
        "securities",
        "strategy_version",
        "horizon_years",
        "base_currency",
        "required_return",
        "permanent_loss_limit",
        "cutoff_at",
        "agenda",
        "preview_hash",
    }
    assert preview["company"]["external_key"] == "US:ALPHABET:COMPANY"
    assert [item["symbol"] for item in preview["securities"]] == ["GOOG", "GOOGL"]
    assert len(preview["agenda"]) == 9
    assert "mandate_id" not in preview

    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201, initialized.text
    body = initialized.json()
    assert body["project_id"]
    assert body["preparation"]["project_id"] == body["project_id"]
    assert body["preparation"]["status"] == "queued"
    assert body["preparation"]["current_step"] == "evidence_index"

    status = api_client.get(f"{BASE}/projects/{body['project_id']}")
    assert status.status_code == 200, status.text
    assert status.json() == body


def test_initialize_requires_idempotency_and_binds_the_returned_preparation_to_preview(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)

    missing = api_client.post(
        f"{BASE}/initializations",
        json={
            "company_id": str(company_id),
            "cutoff_at": NOW.isoformat(),
            "preview_hash": preview["preview_hash"],
        },
    )
    assert missing.status_code == 422

    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    assert initialized.json()["preparation"]["request_hash"] == preview["preview_hash"]


def test_preview_and_status_never_take_write_transaction_control(
    api_client, session, monkeypatch
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201

    calls: list[str] = []

    def forbidden(name: str):
        def operation(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"read route must not call {name}")

        return operation

    monkeypatch.setattr(session, "commit", forbidden("commit"))
    monkeypatch.setattr(session, "rollback", forbidden("rollback"))

    assert (
        api_client.post(
            f"{BASE}/preview",
            json={"company_id": str(company_id), "cutoff_at": NOW.isoformat()},
        ).status_code
        == 200
    )
    assert (
        api_client.get(
            f"{BASE}/projects/{initialized.json()['project_id']}"
        ).status_code
        == 200
    )
    assert calls == []


def test_retry_requeues_only_a_recoverable_preparation(api_client, session) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    preparation = session.scalar(select(CompanyResearchPreparation))
    job = session.scalar(select(Job))
    assert preparation is not None
    assert job is not None
    preparation.status = "recoverable_failure"
    preparation.current_step = "evidence_index"
    preparation.progress = 35
    preparation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    preparation.last_error_code = "source_unavailable"
    job.status = "failed"
    job.step = "evidence_index"
    job.error = "source_unavailable"
    session.commit()

    retried = api_client.post(f"{BASE}/projects/{project_id}/retry")
    assert retried.status_code == 202, retried.text
    body = retried.json()
    assert body["project_id"] == project_id
    assert body["preparation"]["status"] == "queued"
    assert body["preparation"]["current_step"] == "evidence_index"
    assert body["preparation"]["progress"] == 0
    assert body["preparation"]["attempt"] == 2
    assert body["preparation"]["request_hash"] == preview["preview_hash"]


def test_retry_preserves_the_server_declared_failed_financial_bridge_step(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    preparation = session.scalar(select(CompanyResearchPreparation))
    job = session.scalar(select(Job))
    assert preparation is not None
    assert job is not None
    preparation.status = "recoverable_failure"
    preparation.current_step = "financial_bridge"
    preparation.progress = 65
    preparation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    preparation.last_error_code = "financial_data_unavailable"
    job.status = "failed"
    job.step = "financial_bridge"
    job.error = "financial_data_unavailable"
    session.commit()

    retried = api_client.post(f"{BASE}/projects/{project_id}/retry")

    assert retried.status_code == 202, retried.text
    assert retried.json()["preparation"]["current_step"] == "financial_bridge"
    session.expire_all()
    persisted_preparation = session.scalar(select(CompanyResearchPreparation))
    persisted_job = session.scalar(select(Job))
    assert persisted_preparation is not None
    assert persisted_job is not None
    assert persisted_preparation.current_step == "financial_bridge"
    assert persisted_job.status == "queued"
    assert persisted_job.step == "financial_bridge"


def test_retry_rejects_mismatched_failed_preparation_and_job_steps(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    preparation = session.scalar(select(CompanyResearchPreparation))
    job = session.scalar(select(Job))
    assert preparation is not None
    assert job is not None
    preparation.status = "recoverable_failure"
    preparation.current_step = "financial_bridge"
    preparation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    job.status = "failed"
    job.step = "evidence_index"
    session.commit()

    retried = api_client.post(f"{BASE}/projects/{project_id}/retry")

    assert retried.status_code == 422
    assert retried.json()["error"]["code"] == "validation_failed"
    session.expire_all()
    persisted_preparation = session.scalar(select(CompanyResearchPreparation))
    persisted_job = session.scalar(select(Job))
    assert persisted_preparation is not None
    assert persisted_job is not None
    assert persisted_preparation.status == "recoverable_failure"
    assert persisted_preparation.current_step == "financial_bridge"
    assert persisted_job.status == "failed"
    assert persisted_job.step == "evidence_index"


def test_retry_rejects_a_preparation_that_is_not_recoverable(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201

    response = api_client.post(
        f"{BASE}/projects/{initialized.json()['project_id']}/retry"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_workspace_and_evidence_review_routes_are_closed(api_client, session) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and worker.run_claim(claim) == "awaiting_evidence_review"

    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    assert set(body) == {
        "schema_version",
        "project_id",
        "company",
            "preparation",
            "artifacts",
        "modules",
        "source_count",
        "gap_count",
        "draft",
        "selected_revision",
        "change_summary",
    }
    assert body["preparation"]["status"] == "awaiting_evidence_review"
    assert body["preparation"]["current_step"] == "research_gaps"
    assert body["change_summary"] == {
        "artifact_versions": {"evidence_index": 1, "research_gaps": 1},
        "reviewed_fact_count": 0,
    }
    assert {
        item["state"]
        for item in body["modules"]
        if any(ref["kind"] == "evidence_index" for ref in item["artifact_refs"])
    } == {"needs_review"}
    evidence = next(
        item for item in body["artifacts"] if item["kind"] == "evidence_index"
    )
    reviewed = api_client.post(
        f"{BASE}/projects/{project_id}/evidence-reviews",
        json={
            "evidence_artifact_id": evidence["id"],
            "fact_key": evidence["payload"]["facts"][0]["fact_key"],
            "decision": "confirmed",
            "expected_head_id": evidence["id"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["evidence_artifact"]["version"] == 2
    assert (
        api_client.post(
            f"{BASE}/projects/{project_id}/evidence-reviews",
            json={
                "evidence_artifact_id": evidence["id"],
                "fact_key": evidence["payload"]["facts"][0]["fact_key"],
                "decision": "confirmed",
                "expected_head_id": evidence["id"],
                "unexpected": True,
            },
        ).status_code
        == 422
    )
