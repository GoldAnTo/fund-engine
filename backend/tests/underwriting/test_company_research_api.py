"""Closed HTTP contract for the high-level company research flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.underwriting.api.company_research_schemas import (
    CompanyResearchArtifactResponse,
    CompanyResearchWorkbenchModuleResponse,
    CompanyResearchWorkspaceCompanyResponse,
    CompanyResearchWorkspaceDraftResponse,
    CompanyResearchWorkspacePreparationResponse,
    CompanyResearchWorkspaceResponse,
)
from app.underwriting.api.company_research_router import _artifact_response
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.models.operational import Job
from app.underwriting.persistence.company_research_models import (
    CompanyResearchPreparation,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_workbench import WorkbenchArtifact
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
                    "value": "1",
                    "value_kind": "reported",
                    "currency": "USD",
                    "unit": "million",
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


def _workspace_contract(modules) -> CompanyResearchWorkspaceResponse:
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
        ),
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
    missing_unit["payload"]["facts"][0].pop("unit")
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
    modules = tuple(
        CompanyResearchWorkbenchModuleResponse(
            key=key,
            state=(
                "needs_review"
                if key in {"overview", "evidence_and_gaps"}
                else "not_started"
            ),
            artifact=(artifact if key in {"overview", "evidence_and_gaps"} else None),
        )
        for key in EXPECTED_MODULES
    )
    _workspace_contract(modules)

    with pytest.raises(PydanticValidationError):
        _workspace_contract((modules[1], modules[0], *modules[2:]))
    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkbenchModuleResponse(
            key="business_map", state="ready", artifact=artifact
        )


@pytest.mark.parametrize("state", ("not_started", "preparing", "blocked"))
def test_company_research_empty_module_states_are_closed(state: str) -> None:
    module = CompanyResearchWorkbenchModuleResponse(
        key="operating_drivers", state=state, artifact=None
    )
    assert module.state == state


@pytest.mark.parametrize("state", ("ready", "needs_review"))
def test_company_research_artifact_module_states_are_closed(state: str) -> None:
    artifact = CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    module = CompanyResearchWorkbenchModuleResponse(
        key="overview", state=state, artifact=artifact
    )
    assert module.state == state


def test_complete_model_bundle_constructs_all_nine_discriminated_artifact_variants(
    session,
) -> None:
    initialized, _workbench, repository = _model_workspace(session)
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

    responses = []
    for kind in kinds:
        row = repository.current_artifact(initialized.project.id, kind)
        assert row is not None
        responses.append(
            _artifact_response(
                WorkbenchArtifact(
                    id=row.id,
                    kind=row.kind,
                    version=row.version,
                    input_hash=row.input_hash,
                    content_hash=row.content_hash,
                    payload=row.payload,
                    source_refs=tuple(row.source_refs),
                )
            )
        )

    assert tuple(item.kind for item in responses) == kinds


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
    }
    assert tuple(module["key"] for module in body["modules"]) == EXPECTED_MODULES
    assert all(module["state"] == "ready" for module in body["modules"])


def _alphabet_id(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    return loaded.objects["US:ALPHABET:COMPANY"].id


def _preview(api_client, company_id):
    response = api_client.post(
        f"{BASE}/preview",
        json={"company_id": str(company_id), "cutoff_at": NOW.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _initialize(api_client, company_id, preview_hash: str):
    return api_client.post(
        f"{BASE}/initializations",
        headers={"Idempotency-Key": "alphabet-api-initialization"},
        json={
            "company_id": str(company_id),
            "cutoff_at": NOW.isoformat(),
            "preview_hash": preview_hash,
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
        if item["artifact"] is not None and item["artifact"]["kind"] == "evidence_index"
    } == {"needs_review"}
    evidence = next(
        item["artifact"]
        for item in body["modules"]
        if item["key"] == "evidence_and_gaps"
    )
    assert evidence is not None
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
