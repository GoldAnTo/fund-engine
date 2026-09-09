"""Closed HTTP contract for the high-level company research flow."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import bindparam, select, text

from app.underwriting.api.company_research_schemas import (
    CompanyResearchArtifactResponse,
    CompanyResearchFrozenRevisionResponse,
    CompanyResearchJudgmentConfirmationResponse,
    CompanyResearchMarkdownExportResponse,
    CompanyResearchNumericObservationResponse,
    CompanyResearchPreparationResponse,
    CompanyResearchPublicationPreviewResponse,
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
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.product_models import UnderwritingRevisionManifest
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
from app.underwriting.hashing import canonical_hash
from tests.underwriting.test_company_research_workbench import _model_workspace
from tests.underwriting.test_company_research_persistence import _tamper_row

BASE = "/api/underwriting/v1/product/company-research"
NOW = datetime(2026, 8, 28, tzinfo=UTC)
HASH = "a" * 64
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000001")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000003")
GAPS_ID = UUID("00000000-0000-4000-8000-000000000004")
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
REJECTED_PUBLICATION_FACT_KEY = "fy2025_other_bets_revenue"


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


def _gaps_artifact_payload() -> dict:
    return {
        "id": GAPS_ID,
        "project_id": PROJECT_ID,
        "kind": "research_gaps",
        "version": 1,
        "input_hash": HASH,
        "content_hash": "b" * 64,
        "payload": {
            "fixture_content_hash": HASH,
            "company_external_key": "US:ALPHABET:COMPANY",
            "gaps": [],
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
    artifact = CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    gaps = CompanyResearchArtifactResponse.model_validate(_gaps_artifact_payload())
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
        artifacts=(artifact, gaps),
        modules=modules,
        source_count=1,
        gap_count=0,
        draft=CompanyResearchWorkspaceDraftResponse(
            id=ARTIFACT_ID, lock_version=1, base_revision_id=None
        ),
        selected_revision=None,
        change_summary={
            "artifact_versions": {"evidence_index": 1, "research_gaps": 1},
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
    gaps_ref = {
        "id": GAPS_ID,
        "kind": "research_gaps",
        "content_hash": "b" * 64,
    }
    modules = tuple(
        CompanyResearchWorkbenchModuleResponse(
            key=key,
            state=("needs_review" if key == "evidence_and_gaps" else "not_started"),
            artifact_refs=(artifact_ref, gaps_ref)
            if key == "evidence_and_gaps"
            else (),
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
        CompanyResearchWorkbenchModuleResponse(
            key="evidence_and_gaps",
            state="needs_review",
            artifact_refs=(artifact_ref,),
            valuation_state="not_applicable",
        )

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

    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkbenchModuleResponse(
            key="operating_drivers",
            state=state,
            artifact_refs=(
                {
                    "id": ARTIFACT_ID,
                    "kind": "driver_map",
                    "content_hash": HASH,
                },
            ),
            valuation_state="not_applicable",
        )


@pytest.mark.parametrize("state", ("ready", "needs_review"))
def test_company_research_artifact_module_states_are_closed(state: str) -> None:
    artifact = CompanyResearchArtifactResponse.model_validate(
        _evidence_artifact_payload()
    )
    gaps = CompanyResearchArtifactResponse.model_validate(_gaps_artifact_payload())
    module = CompanyResearchWorkbenchModuleResponse(
        key="evidence_and_gaps",
        state=state,
        artifact_refs=(
            {
                "id": artifact.root.id,
                "kind": artifact.root.kind,
                "content_hash": artifact.root.content_hash,
            },
            {
                "id": gaps.root.id,
                "kind": gaps.root.kind,
                "content_hash": gaps.root.content_hash,
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
        item
        for item in lineage_substitution["artifacts"]
        if item["kind"] == "business_map"
    )
    business["payload"]["_lineage"]["artifact_refs"][0]["content_hash"] = "b" * 64
    duplicate = deepcopy(body)
    duplicate["artifacts"].append(deepcopy(duplicate["artifacts"][0]))
    reported_value_substitution = deepcopy(body)
    driver_map = next(
        item
        for item in reported_value_substitution["artifacts"]
        if item["kind"] == "driver_map"
    )
    reported_observation = next(
        observation
        for driver in driver_map["payload"]["drivers"]
        for observation in driver["values"]
        if observation["state"] == "reported"
    )
    reported_observation["value"] = "999"
    rejected_downstream_fact = deepcopy(body)
    rejected_driver = next(
        item
        for item in rejected_downstream_fact["artifacts"]
        if item["kind"] == "driver_map"
    )
    rejected_observation = next(
        observation
        for driver in rejected_driver["payload"]["drivers"]
        for observation in driver["values"]
        if observation["state"] == "reported"
    )
    rejected_evidence = next(
        item
        for item in rejected_downstream_fact["artifacts"]
        if item["kind"] == "evidence_index"
    )
    rejected_fact = next(
        fact
        for fact in rejected_evidence["payload"]["facts"]
        if fact["fact_key"] == rejected_observation["source_ref"]["fact_key"]
    )
    rejected_fact["review_decision"] = "rejected"
    wrong_version = deepcopy(body)
    wrong_version["change_summary"]["artifact_versions"]["driver_map"] += 1
    wrong_reviewed_count = deepcopy(body)
    wrong_reviewed_count["change_summary"]["reviewed_fact_count"] += 1
    wrong_source_count = deepcopy(body)
    wrong_source_count["source_count"] += 1
    wrong_gap_count = deepcopy(body)
    wrong_gap_count["gap_count"] += 1
    fallback_market_provenance = deepcopy(body)
    valuation = next(
        item
        for item in fallback_market_provenance["artifacts"]
        if item["kind"] == "valuation_set"
    )
    valuation["payload"]["_lineage"]["market_snapshot_bindings"][0][
        "provenance_role"
    ] = "fallback"
    wrong_scenario_unit = deepcopy(body)
    scenario = next(
        item
        for item in wrong_scenario_unit["artifacts"]
        if item["kind"] == "scenario_set"
    )
    scenario["payload"]["scenarios"][0]["driver_overrides"][0]["observation"][
        "unit"
    ] = "USD_million"

    for invalid in (
        nonexistent,
        foreign,
        substituted,
        lineage_substitution,
        duplicate,
        reported_value_substitution,
        rejected_downstream_fact,
        wrong_version,
        wrong_reviewed_count,
        wrong_source_count,
        wrong_gap_count,
        fallback_market_provenance,
        wrong_scenario_unit,
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchWorkspaceResponse.model_validate(invalid)


@pytest.mark.parametrize(
    "missing_kind",
    ("research_gaps", "scenario_set", "judgment_context"),
)
def test_ready_modules_cannot_shrink_when_a_required_head_is_missing(
    api_client, session, missing_kind: str
) -> None:
    initialized, _workbench, _repository = _model_workspace(session)
    response = api_client.get(f"{BASE}/projects/{initialized.project.id}/workspace")
    assert response.status_code == 200, response.text
    body = response.json()
    missing = next(item for item in body["artifacts"] if item["kind"] == missing_kind)
    body["artifacts"] = [
        item for item in body["artifacts"] if item["id"] != missing["id"]
    ]
    body["change_summary"]["artifact_versions"].pop(missing_kind)
    for module in body["modules"]:
        module["artifact_refs"] = [
            ref for ref in module["artifact_refs"] if ref["id"] != missing["id"]
        ]

    with pytest.raises(PydanticValidationError):
        CompanyResearchWorkspaceResponse.model_validate(body)


def _alphabet_id(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    return loaded.objects["US:ALPHABET:COMPANY"].id


def _preview(api_client, company_id, *, cutoff_at: datetime = NOW, user_focus: str | None = None):
    response = api_client.post(
        f"{BASE}/preview",
        json={"company_id": str(company_id), "cutoff_at": cutoff_at.isoformat(), "user_focus": user_focus},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _initialize(
    api_client,
    company_id,
    preview_hash: str,
    *,
    cutoff_at: datetime = NOW,
    user_focus: str | None = None,
):
    return api_client.post(
        f"{BASE}/initializations",
        headers={"Idempotency-Key": "alphabet-api-initialization"},
        json={
            "company_id": str(company_id),
            "cutoff_at": cutoff_at.isoformat(),
            "preview_hash": preview_hash,
            "user_focus": user_focus,
        },
    )


def _run_public_company_research_pipeline(
    api_client,
    session,
    *,
    fixed_model_clock: bool = False,
    rejected_fact_key: str | None = None,
    user_focus: str | None = None,
) -> dict:
    cutoff = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)
    company_id = _alphabet_id(session)
    request = {"company_id": str(company_id), "cutoff_at": cutoff.isoformat(), "user_focus": user_focus}
    response = api_client.post(f"{BASE}/preview", json=request)
    assert response.status_code == 200, response.text
    preview = response.json()
    initialized = api_client.post(
        f"{BASE}/initializations",
        headers={"Idempotency-Key": "alphabet-api-initialization"},
        json={**request, "preview_hash": preview["preview_hash"]},
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
                "decision": (
                    "rejected" if fact["fact_key"] == rejected_fact_key else "confirmed"
                ),
                "expected_head_id": current["id"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()["evidence_artifact"]
    model_time = datetime.now(UTC)
    worker = CompanyResearchPreparationWorker(
        session,
        now=(lambda: model_time) if fixed_model_clock else (lambda: datetime.now(UTC)),
    )
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


def _publication_invariants(workspace: dict) -> dict[str, object]:
    artifacts = {item["kind"]: item for item in workspace["artifacts"]}
    evidence = artifacts["evidence_index"]
    gaps = artifacts["research_gaps"]
    return {
        "evidence": deepcopy(evidence),
        "research_gaps": deepcopy(gaps),
        "decisions": tuple(
            (fact["fact_key"], fact["review_decision"])
            for fact in evidence["payload"]["facts"]
        ),
    }


@pytest.mark.parametrize("user_focus", [None, "AI 资本开支的长期现金回报"])
def test_company_research_publication_closes_the_entire_public_http_workflow(
    api_client, session, user_focus
) -> None:
    workspace = _run_public_company_research_pipeline(
        api_client,
        session,
        fixed_model_clock=True,
        rejected_fact_key=REJECTED_PUBLICATION_FACT_KEY,
        user_focus=user_focus,
    )
    assert workspace["product_progress"]["user_focus"] == user_focus
    project_id = workspace["project_id"]
    memo = next(item for item in workspace["artifacts"] if item["kind"] == "memo")
    assert memo["payload"]["candidate_status"] == "machine_draft"
    assert "reviewer" not in memo["payload"]
    assert "markdown" not in memo["payload"]
    before = _publication_invariants(workspace)
    assert [decision for _key, decision in before["decisions"]].count("confirmed") == 6
    assert [decision for _key, decision in before["decisions"]].count("rejected") == 1

    confirmation = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": workspace["draft"]["lock_version"],
            "expected_memo_id": memo["id"],
            "expected_memo_content_hash": memo["content_hash"],
            "markdown": "Current formal evidence is insufficient.\n",
        },
    )
    assert confirmation.status_code == 200, confirmation.text
    confirmation_body = confirmation.json()
    CompanyResearchJudgmentConfirmationResponse.model_validate(confirmation_body)
    confirmed = confirmation.json()
    assert confirmed["project_id"] == project_id
    assert confirmed["preparation"]["status"] == "ready_to_freeze"
    assert confirmed["preparation"]["current_step"] == "memo"
    assert confirmed["preparation"]["progress"] == 95
    assert confirmed["confirmed_memo"]["id"] != memo["id"]

    ready_workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert ready_workspace.status_code == 200, ready_workspace.text
    confirmed_memo = next(
        item for item in ready_workspace.json()["artifacts"] if item["kind"] == "memo"
    )
    assert confirmed_memo["payload"]["candidate_status"] == "human_confirmed"
    assert confirmed_memo["payload"]["reviewer"] == "human:local-user"
    assert confirmed_memo["payload"]["markdown"] == (
        "Current formal evidence is insufficient."
    )

    preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
        },
    )
    assert preview.status_code == 200, preview.text
    candidate = preview.json()
    assert candidate["project_id"] == project_id
    assert candidate["assessment"] == {
        "schema_version": "underwriting.v1",
        "answerability": "not_answerable",
        "direction": None,
        "confidence": None,
        "content_hash": candidate["assessment"]["content_hash"],
    }
    assert candidate["value_range"] is None
    assert candidate["return_range"] is None
    assert set(item["kind"] for item in candidate["artifacts"]) == {
        "evidence_index",
        "research_gaps",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "judgment_context",
        "memo",
    }

    published = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "alphabet-live-freeze"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": candidate["manifest_hash"],
        },
    )
    assert published.status_code == 201, published.text
    frozen = published.json()
    revision_id = frozen["id"]
    assert frozen["project_id"] == project_id
    assert frozen["preparation_status"] == "completed"
    assert frozen["current_step"] is None
    assert frozen["progress"] == 100

    project_status = api_client.get(f"{BASE}/projects/{project_id}")
    assert project_status.status_code == 200, project_status.text
    assert project_status.json()["preparation"]["status"] == "completed"
    assert project_status.json()["preparation"]["current_step"] is None
    assert project_status.json()["preparation"]["progress"] == 100

    replay = api_client.get(f"{BASE}/projects/{project_id}/revisions/{revision_id}")
    assert replay.status_code == 200, replay.text
    assert replay.json() == frozen

    exported = api_client.get(
        f"{BASE}/projects/{project_id}/revisions/{revision_id}/export"
    )
    assert exported.status_code == 200, exported.text
    envelope = exported.json()
    assert envelope["media_type"] == "text/markdown"
    assert envelope["filename"] == f"alphabet-company-research-{revision_id}.md"
    assert (
        envelope["content_hash"]
        == sha256(envelope["content"].encode("utf-8")).hexdigest()
    )

    after_response = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert after_response.status_code == 200, after_response.text
    after = after_response.json()
    assert after["preparation"] == {
        "schema_version": "underwriting.v1",
        "id": workspace["preparation"]["id"],
        "status": "completed",
        "current_step": None,
        "progress": 100,
        "error": None,
    }
    assert _publication_invariants(after) == before

    repository = CompanyResearchRepository(session)
    session.expire_all()
    preparation = repository.preparation_for_project(UUID(project_id), fresh=True)
    assert preparation is not None
    preparation.status = "ready_to_freeze"
    preparation.current_step = "memo"
    preparation.progress = 95
    preparation.updated_at = datetime.now(UTC) + timedelta(seconds=1)
    session.flush([preparation])
    session.commit()

    continued = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert continued.status_code == 200, continued.text
    assert continued.json()["preparation"]["status"] == "ready_to_freeze"
    assert continued.json()["preparation"]["current_step"] == "memo"
    assert continued.json()["preparation"]["progress"] == 95

    session.expire_all()
    durable_memo = repository.current_artifact(UUID(project_id), "memo")
    assert durable_memo is not None
    repository.append_artifact(
        project_id=UUID(project_id),
        kind="memo",
        input_hash=durable_memo.input_hash,
        payload=durable_memo.payload,
        source_refs=durable_memo.source_refs,
        expected_parent_id=durable_memo.id,
        created_at=datetime.now(UTC) + timedelta(seconds=2),
    )
    session.commit()

    mixed = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert mixed.status_code == 422
    assert mixed.json()["error"]["code"] == "validation_failed"


def test_company_research_publication_http_errors_are_bounded_and_identity_bound(
    api_client, session
) -> None:
    def assert_bounded(response) -> None:
        body = response.text.lower()
        assert "provider" not in body
        assert "internal" not in body

    workspace = _run_public_company_research_pipeline(
        api_client,
        session,
        fixed_model_clock=True,
        rejected_fact_key=REJECTED_PUBLICATION_FACT_KEY,
    )
    project_id = workspace["project_id"]
    memo = next(item for item in workspace["artifacts"] if item["kind"] == "memo")
    unknown_project_id = "00000000-0000-4000-8000-000000000099"
    unknown_requests = (
        api_client.post(
            f"{BASE}/projects/{unknown_project_id}/judgment-confirmations",
            json={
                "schema_version": "underwriting.v1",
                "expected_lock_version": 1,
                "expected_memo_id": memo["id"],
                "expected_memo_content_hash": memo["content_hash"],
                "markdown": "Current formal evidence is insufficient.\n",
            },
        ),
        api_client.post(
            f"{BASE}/projects/{unknown_project_id}/publication-preview",
            json={
                "schema_version": "underwriting.v1",
                "expected_lock_version": 1,
            },
        ),
        api_client.post(
            f"{BASE}/projects/{unknown_project_id}/publish",
            headers={"Idempotency-Key": "unknown-project"},
            json={
                "schema_version": "underwriting.v1",
                "expected_lock_version": 1,
                "expected_manifest_hash": "a" * 64,
            },
        ),
    )
    assert [response.status_code for response in unknown_requests] == [404, 404, 404]
    assert all(
        response.json()["error"]["code"] == "not_found" for response in unknown_requests
    )
    for response in unknown_requests:
        assert_bounded(response)
    request = {
        "schema_version": "underwriting.v1",
        "expected_lock_version": workspace["draft"]["lock_version"],
        "expected_memo_id": memo["id"],
        "expected_memo_content_hash": memo["content_hash"],
        "markdown": "Current formal evidence is insufficient.\n",
    }

    for malformed in (
        {**request, "expected_memo_content_hash": memo["content_hash"].upper()},
        {**request, "unexpected": "must-not-be-accepted"},
        {**request, "expected_lock_version": True},
    ):
        response = api_client.post(
            f"{BASE}/projects/{project_id}/judgment-confirmations", json=malformed
        )
        assert response.status_code == 422
        assert_bounded(response)

    confirmation = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations", json=request
    )
    assert confirmation.status_code == 200, confirmation.text
    confirmed = confirmation.json()

    conflicting_confirmation = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations",
        json={**request, "markdown": "A different conclusion.\n"},
    )
    assert conflicting_confirmation.status_code == 409
    assert conflicting_confirmation.json()["error"]["code"] == "conflict"
    assert_bounded(conflicting_confirmation)

    stale_preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"] + 1,
        },
    )
    assert stale_preview.status_code == 409
    assert_bounded(stale_preview)

    preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
        },
    )
    assert preview.status_code == 200, preview.text
    candidate = preview.json()

    missing_key = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": candidate["manifest_hash"],
        },
    )
    assert missing_key.status_code == 422
    assert_bounded(missing_key)

    mismatch = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "bounded-errors"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": "f" * 64,
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "validation_failed"
    assert_bounded(mismatch)

    published = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "bounded-errors"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": candidate["manifest_hash"],
        },
    )
    assert published.status_code == 201, published.text
    frozen = published.json()
    revision_id = frozen["id"]

    replayed_publish = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "bounded-errors"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": candidate["manifest_hash"],
        },
    )
    assert replayed_publish.status_code == 201
    assert replayed_publish.json() == frozen

    reused_key = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "bounded-errors"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmed["draft"]["lock_version"],
            "expected_manifest_hash": "e" * 64,
        },
    )
    assert reused_key.status_code == 409
    assert reused_key.json()["error"]["code"] == "conflict"
    assert_bounded(reused_key)

    foreign_project_id = "00000000-0000-4000-8000-000000000001"
    for suffix in ("", "/export"):
        foreign = api_client.get(
            f"{BASE}/projects/{foreign_project_id}/revisions/{revision_id}{suffix}"
        )
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "not_found"
        assert_bounded(foreign)

    unknown = api_client.get(
        f"{BASE}/projects/{project_id}/revisions/00000000-0000-4000-8000-000000000099"
    )
    assert unknown.status_code == 404
    assert_bounded(unknown)

    manifest = session.get(UnderwritingRevisionManifest, UUID(frozen["manifest_id"]))
    assert manifest is not None
    _tamper_row(
        session,
        UnderwritingRevisionManifest,
        manifest.id,
        content_hash="0" * 64,
    )
    corrupted = api_client.get(f"{BASE}/projects/{project_id}/revisions/{revision_id}")
    assert corrupted.status_code == 422
    error = corrupted.json()["error"]
    assert error["code"] == "validation_failed"
    assert_bounded(corrupted)


def test_company_research_publication_reads_never_take_transaction_control(
    api_client, session, monkeypatch
) -> None:
    workspace = _run_public_company_research_pipeline(
        api_client,
        session,
        fixed_model_clock=True,
        rejected_fact_key=REJECTED_PUBLICATION_FACT_KEY,
    )
    project_id = workspace["project_id"]
    memo = next(item for item in workspace["artifacts"] if item["kind"] == "memo")
    confirmation = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": workspace["draft"]["lock_version"],
            "expected_memo_id": memo["id"],
            "expected_memo_content_hash": memo["content_hash"],
            "markdown": "Current formal evidence is insufficient.\n",
        },
    )
    assert confirmation.status_code == 200, confirmation.text
    lock_version = confirmation.json()["draft"]["lock_version"]
    calls: list[str] = []

    def forbidden(name: str):
        def operation(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"read route must not call {name}")

        return operation

    with monkeypatch.context() as context:
        context.setattr(session, "commit", forbidden("commit"))
        context.setattr(session, "rollback", forbidden("rollback"))
        preview = api_client.post(
            f"{BASE}/projects/{project_id}/publication-preview",
            json={
                "schema_version": "underwriting.v1",
                "expected_lock_version": lock_version,
            },
        )
        assert preview.status_code == 200, preview.text
    assert calls == []

    published = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "read-transaction-boundary"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": lock_version,
            "expected_manifest_hash": preview.json()["manifest_hash"],
        },
    )
    assert published.status_code == 201, published.text
    revision_id = published.json()["id"]

    with monkeypatch.context() as context:
        context.setattr(session, "commit", forbidden("commit"))
        context.setattr(session, "rollback", forbidden("rollback"))
        replay = api_client.get(f"{BASE}/projects/{project_id}/revisions/{revision_id}")
        exported = api_client.get(
            f"{BASE}/projects/{project_id}/revisions/{revision_id}/export"
        )
        assert replay.status_code == 200, replay.text
        assert exported.status_code == 200, exported.text
    assert calls == []


def test_company_research_publication_response_contract_is_closed_and_ordered(
    api_client, session
) -> None:
    workspace = _run_public_company_research_pipeline(
        api_client,
        session,
        fixed_model_clock=True,
        rejected_fact_key=REJECTED_PUBLICATION_FACT_KEY,
    )
    project_id = workspace["project_id"]
    memo = next(item for item in workspace["artifacts"] if item["kind"] == "memo")
    confirmation = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": workspace["draft"]["lock_version"],
            "expected_memo_id": memo["id"],
            "expected_memo_content_hash": memo["content_hash"],
            "markdown": "Current formal evidence is insufficient.\n",
        },
    )
    assert confirmation.status_code == 200, confirmation.text
    confirmation_body = confirmation.json()
    CompanyResearchJudgmentConfirmationResponse.model_validate(confirmation_body)
    preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmation.json()["draft"]["lock_version"],
        },
    )
    assert preview.status_code == 200, preview.text
    candidate = preview.json()
    CompanyResearchPublicationPreviewResponse.model_validate(candidate)

    unknown = {**candidate, "unexpected": True}
    uppercase_hash = {**candidate, "manifest_hash": candidate["manifest_hash"].upper()}
    open_investment_fields = deepcopy(candidate)
    open_investment_fields["assessment"]["direction"] = "provisional_bullish"
    reordered = deepcopy(candidate)
    reordered["artifacts"] = list(reversed(reordered["artifacts"]))

    for invalid in (
        unknown,
        uppercase_hash,
        open_investment_fields,
        reordered,
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchPublicationPreviewResponse.model_validate(invalid)

    for invalid in (
        {**confirmation_body, "unexpected": True},
        {
            **confirmation_body,
            "confirmed_memo": {
                **confirmation_body["confirmed_memo"],
                "content_hash": "A" * 64,
            },
        },
        {
            **confirmation_body,
            "preparation": {
                **confirmation_body["preparation"],
                "status": "completed",
            },
        },
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchJudgmentConfirmationResponse.model_validate(invalid)

    published = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "closed-response-contract"},
        json={
            "schema_version": "underwriting.v1",
            "expected_lock_version": confirmation_body["draft"]["lock_version"],
            "expected_manifest_hash": candidate["manifest_hash"],
        },
    )
    assert published.status_code == 201, published.text
    frozen = published.json()
    CompanyResearchFrozenRevisionResponse.model_validate(frozen)
    exported = api_client.get(
        f"{BASE}/projects/{project_id}/revisions/{frozen['id']}/export"
    )
    assert exported.status_code == 200, exported.text
    export_body = exported.json()
    CompanyResearchMarkdownExportResponse.model_validate(export_body)

    foreign_security = deepcopy(frozen)
    foreign_security["securities"][0]["company_id"] = str(ARTIFACT_ID)
    for invalid in (
        {**frozen, "unexpected": True},
        {**frozen, "manifest_hash": "A" * 64},
        {**frozen, "preparation_status": "ready_to_freeze"},
        foreign_security,
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchFrozenRevisionResponse.model_validate(invalid)

    for invalid in (
        {**export_body, "unexpected": True},
        {**export_body, "content_hash": "A" * 64},
        {**export_body, "media_type": "text/html"},
        {**export_body, "filename": "unsafe.md"},
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchMarkdownExportResponse.model_validate(invalid)


def _rewrite_head_decision_as_malformed(session, head_id: UUID) -> None:
    repository = CompanyResearchRepository(session)
    head = session.get(CompanyResearchArtifactVersion, head_id)
    assert head is not None and head.supersedes_id is not None
    parent = session.get(CompanyResearchArtifactVersion, head.supersedes_id)
    assert parent is not None
    payload = deepcopy(head.payload)
    changes = [
        after
        for before, after in zip(parent.payload["facts"], payload["facts"], strict=True)
        if before != after
    ]
    assert len(changes) == 1
    changes[0]["review_decision"] = []
    input_hash = canonical_hash(
        {
            "parent": parent.content_hash,
            "fact_key": changes[0]["fact_key"],
            "decision": [],
        }
    )
    content_hash = repository.artifact_content_hash(
        project_id=head.project_id,
        kind=head.kind,
        version=head.version,
        supersedes_id=head.supersedes_id,
        parent_content_hash=head.parent_content_hash,
        input_hash=input_hash,
        payload=payload,
        source_refs=head.source_refs,
    )
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET payload = :payload, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    assert (
        session.execute(
            statement,
            {
                "payload": payload,
                "input_hash": input_hash,
                "content_hash": content_hash,
                "id": head.id,
            },
        ).rowcount
        == 1
    )
    session.expire_all()


def _rewrite_first_review_decision_chain(
    session, head_id: UUID, *, decision: str
) -> None:
    """Rehash a valid review chain while leaving its audit events untouched."""
    repository = CompanyResearchRepository(session)
    chain = repository.artifact_chain(head_id)
    assert len(chain) >= 2
    changed = [
        after
        for before, after in zip(
            chain[0].payload["facts"], chain[1].payload["facts"], strict=True
        )
        if before != after
    ]
    assert len(changed) == 1
    target_fact_key = changed[0]["fact_key"]
    rewritten_hashes = {chain[0].id: chain[0].content_hash}
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET "
        "parent_content_hash = :parent_content_hash, payload = :payload, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "parent_content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.parent_content_hash.type,
        ),
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    for parent, row in zip(chain, chain[1:], strict=False):
        original_changes = [
            after
            for before, after in zip(
                parent.payload["facts"], row.payload["facts"], strict=True
            )
            if before != after
        ]
        assert len(original_changes) == 1
        fact_key = original_changes[0]["fact_key"]
        transition_decision = original_changes[0]["review_decision"]
        payload = deepcopy(row.payload)
        for fact in payload["facts"]:
            if fact["fact_key"] == target_fact_key:
                fact["review_decision"] = decision
                if fact_key == target_fact_key:
                    transition_decision = decision
                break
        parent_content_hash = rewritten_hashes[parent.id]
        input_hash = canonical_hash(
            {
                "parent": parent_content_hash,
                "fact_key": fact_key,
                "decision": transition_decision,
            }
        )
        content_hash = repository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=parent_content_hash,
            input_hash=input_hash,
            payload=payload,
            source_refs=row.source_refs,
        )
        assert (
            session.execute(
                statement,
                {
                    "parent_content_hash": parent_content_hash,
                    "payload": payload,
                    "input_hash": input_hash,
                    "content_hash": content_hash,
                    "id": row.id,
                },
            ).rowcount
            == 1
        )
        rewritten_hashes[row.id] = content_hash
    session.expire_all()


def _rewrite_evidence_fact_field(
    session, head_id: UUID, *, field: str, value: object
) -> None:
    repository = CompanyResearchRepository(session)
    head = session.get(CompanyResearchArtifactVersion, head_id)
    assert head is not None and head.kind == "evidence_index"
    payload = deepcopy(head.payload)
    payload["facts"][0][field] = value
    content_hash = repository.artifact_content_hash(
        project_id=head.project_id,
        kind=head.kind,
        version=head.version,
        supersedes_id=head.supersedes_id,
        parent_content_hash=head.parent_content_hash,
        input_hash=head.input_hash,
        payload=payload,
        source_refs=head.source_refs,
    )
    statement = text(
        "UPDATE uw_company_research_artifact_versions "
        "SET payload = :payload, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    assert (
        session.execute(
            statement,
            {"payload": payload, "content_hash": content_hash, "id": head.id},
        ).rowcount
        == 1
    )
    session.expire_all()


def _rewrite_complete_evidence_chain_company_key(session, head_id: UUID) -> None:
    repository = CompanyResearchRepository(session)
    chain = repository.artifact_chain(head_id)
    rewritten_hashes: dict[UUID, str] = {}
    statement = text(
        "UPDATE uw_company_research_artifact_versions SET "
        "parent_content_hash = :parent_content_hash, payload = :payload, "
        "input_hash = :input_hash, content_hash = :content_hash WHERE id = :id"
    ).bindparams(
        bindparam(
            "parent_content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.parent_content_hash.type,
        ),
        bindparam(
            "payload", type_=CompanyResearchArtifactVersion.__table__.c.payload.type
        ),
        bindparam(
            "input_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.input_hash.type,
        ),
        bindparam(
            "content_hash",
            type_=CompanyResearchArtifactVersion.__table__.c.content_hash.type,
        ),
        bindparam("id", type_=CompanyResearchArtifactVersion.__table__.c.id.type),
    )
    for index, row in enumerate(chain):
        payload = deepcopy(row.payload)
        payload["company_external_key"] = "US:SUBSTITUTED:COMPANY"
        for fact in payload["facts"]:
            fact["company_external_key"] = "US:SUBSTITUTED:COMPANY"
        parent_content_hash = rewritten_hashes[chain[index - 1].id] if index else None
        input_hash = row.input_hash
        if index:
            parent = chain[index - 1]
            changes = [
                after
                for before, after in zip(
                    parent.payload["facts"], row.payload["facts"], strict=True
                )
                if before != after
            ]
            assert len(changes) == 1
            input_hash = canonical_hash(
                {
                    "parent": parent_content_hash,
                    "fact_key": changes[0]["fact_key"],
                    "decision": changes[0]["review_decision"],
                }
            )
        content_hash = repository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=parent_content_hash,
            input_hash=input_hash,
            payload=payload,
            source_refs=row.source_refs,
        )
        assert (
            session.execute(
                statement,
                {
                    "parent_content_hash": parent_content_hash,
                    "payload": payload,
                    "input_hash": input_hash,
                    "content_hash": content_hash,
                    "id": row.id,
                },
            ).rowcount
            == 1
        )
        rewritten_hashes[row.id] = content_hash
    session.expire_all()


def test_workspace_returns_422_for_hash_consistent_malformed_evidence_timestamp(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201, initialized.text
    project_id = UUID(initialized.json()["project_id"])
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    evidence = CompanyResearchRepository(session).current_artifact(
        project_id, "evidence_index"
    )
    assert evidence is not None
    _rewrite_evidence_fact_field(session, evidence.id, field="published_at", value={})
    session.commit()

    response = api_client.get(f"{BASE}/projects/{project_id}/workspace")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == (
        "evidence fact published_at must be canonical non-empty text"
    )


def test_workspace_returns_422_when_review_chain_contradicts_audit_events(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201, initialized.text
    project_id = UUID(initialized.json()["project_id"])
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    evidence = next(
        artifact
        for artifact in workspace.json()["artifacts"]
        if artifact["kind"] == "evidence_index"
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
    reviewed_id = UUID(reviewed.json()["evidence_artifact"]["id"])
    _rewrite_first_review_decision_chain(session, reviewed_id, decision="rejected")
    session.commit()

    response = api_client.get(f"{BASE}/projects/{project_id}/workspace")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == (
        "company research evidence audit history is invalid"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("value", []),
        ("value", {}),
        ("currency", ""),
        ("period_start", "not-a-date"),
        ("value_kind", "unsupported"),
        ("published_at", "2030-01-01T00:00:00+00:00"),
    ),
)
def test_workspace_returns_422_for_hash_consistent_malformed_rejected_fact(
    api_client, session, field: str, value: object
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201, initialized.text
    project_id = UUID(initialized.json()["project_id"])
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    evidence = next(
        item
        for item in workspace.json()["artifacts"]
        if item["kind"] == "evidence_index"
    )
    reviewed = api_client.post(
        f"{BASE}/projects/{project_id}/evidence-reviews",
        json={
            "evidence_artifact_id": evidence["id"],
            "fact_key": evidence["payload"]["facts"][0]["fact_key"],
            "decision": "rejected",
            "expected_head_id": evidence["id"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_id = UUID(reviewed.json()["evidence_artifact"]["id"])
    _rewrite_evidence_fact_field(session, reviewed_id, field=field, value=value)
    session.commit()

    response = api_client.get(f"{BASE}/projects/{project_id}/workspace")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"]


def test_workspace_rejects_a_rehashed_substituted_evidence_review_chain(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201, initialized.text
    project_id = UUID(initialized.json()["project_id"])
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None
    assert worker.run_claim(claim) == "awaiting_evidence_review"
    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    evidence = next(
        item
        for item in workspace.json()["artifacts"]
        if item["kind"] == "evidence_index"
    )
    current = evidence
    for fact in evidence["payload"]["facts"][:2]:
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
    _rewrite_complete_evidence_chain_company_key(session, UUID(current["id"]))
    session.commit()

    response = api_client.get(f"{BASE}/projects/{project_id}/workspace")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == (
        "company research evidence lineage is invalid"
    )


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
    assert [ref["kind"] for ref in scenario_module["artifact_refs"]] == ["scenario_set"]
    assert scenario_module["state"] == "ready"
    assert scenario_module["valuation_state"] == "blocked"
    scenario = next(
        item for item in body["artifacts"] if item["kind"] == "scenario_set"
    )
    scenario_observations = [
        override["observation"]
        for item in scenario["payload"]["scenarios"]
        for override in item["driver_overrides"]
    ]
    assert len(scenario_observations) == 18
    assert {item["unit"] for item in scenario_observations} == {"multiplier"}
    assert {item["currency"] for item in scenario_observations} == {"N/A"}
    assert all(item["state"] == "assumption" for item in scenario_observations)
    for driver_key in ("revenue", "capex"):
        observations = [
            override["observation"]
            for item in scenario["payload"]["scenarios"]
            for override in item["driver_overrides"]
            if override["driver_key"] == driver_key
        ]
        assert observations
        assert all(item["unit"] == "multiplier" for item in observations)
        assert all(item["currency"] == "N/A" for item in observations)
    overview = next(module for module in body["modules"] if module["key"] == "overview")
    assert [ref["kind"] for ref in overview["artifact_refs"]] == ["judgment_context"]
    evidence = next(
        item for item in body["artifacts"] if item["kind"] == "evidence_index"
    )
    facts = {item["fact_key"]: item for item in evidence["payload"]["facts"]}

    def reported_observations(value):
        if isinstance(value, list):
            return [item for child in value for item in reported_observations(child)]
        if not isinstance(value, dict):
            return []
        if value.get("state") == "reported" and "source_ref" in value:
            return [value]
        return [
            item for child in value.values() for item in reported_observations(child)
        ]

    reported = reported_observations(body["artifacts"])
    assert len(reported) == 15
    for observation in reported:
        source = observation["source_ref"]
        fact = facts[source["fact_key"]]
        assert observation["unit"] == fact["observation"]["unit"]
        assert observation["currency"] == fact["observation"]["currency"]
        assert observation["period"] == fact["observation"]["period"]
        assert observation["value"] == fact["observation"]["value"]
        assert source == fact["observation"]["source_ref"]


def test_recoverable_workspace_exposes_typed_error_semantics(
    api_client, session
) -> None:
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
        "next_attempt_at": (NOW + timedelta(minutes=5))
        .isoformat()
        .replace("+00:00", "Z"),
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


def test_company_research_public_preparation_lifecycle_is_closed() -> None:
    project_base = {
        "id": ARTIFACT_ID,
        "project_id": PROJECT_ID,
        "request_hash": HASH,
        "strategy_version": "alphabet-five-year-v1",
        "attempt": 1,
        "next_attempt_at": None,
        "last_error_code": None,
    }
    workspace_base = {"id": ARTIFACT_ID, "error": None}

    for status, current_step, progress in (
        ("awaiting_evidence_review", "research_gaps", 25),
        ("awaiting_judgment_review", "judgment_context", 85),
        ("ready_to_freeze", "memo", 95),
        ("completed", None, 100),
    ):
        CompanyResearchPreparationResponse.model_validate(
            {
                **project_base,
                "status": status,
                "current_step": current_step,
                "progress": progress,
            }
        )
        CompanyResearchWorkspacePreparationResponse.model_validate(
            {
                **workspace_base,
                "status": status,
                "current_step": current_step,
                "progress": progress,
            }
        )

    for status, current_step, progress in (
        ("ready_to_freeze", "judgment_context", 85),
        ("ready_to_freeze", "memo", 85),
        ("completed", "memo", 95),
        ("completed", None, 95),
    ):
        with pytest.raises(PydanticValidationError):
            CompanyResearchPreparationResponse.model_validate(
                {
                    **project_base,
                    "status": status,
                    "current_step": current_step,
                    "progress": progress,
                }
            )
        with pytest.raises(PydanticValidationError):
            CompanyResearchWorkspacePreparationResponse.model_validate(
                {
                    **workspace_base,
                    "status": status,
                    "current_step": current_step,
                    "progress": progress,
                }
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
        "user_focus",
        "requested_cutoff_at",
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


@pytest.mark.parametrize("user_focus", [None, "资本配置与长期回报"])
def test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions(
    api_client, session, user_focus
) -> None:
    cutoff = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id, cutoff_at=cutoff, user_focus=user_focus)
    initialized = _initialize(
        api_client,
        company_id,
        preview["preview_hash"],
        cutoff_at=cutoff,
        user_focus=user_focus,
    )
    assert initialized.status_code == 201, initialized.text
    project_id = initialized.json()["project_id"]
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "awaiting_evidence_review"
    workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert workspace.status_code == 200, workspace.text
    evidence = next(
        item
        for item in workspace.json()["artifacts"]
        if item["kind"] == "evidence_index"
    )
    current = evidence
    for index, fact in enumerate(evidence["payload"]["facts"]):
        reviewed = api_client.post(
            f"{BASE}/projects/{project_id}/evidence-reviews",
            json={
                "evidence_artifact_id": current["id"],
                "fact_key": fact["fact_key"],
                "decision": "rejected" if index == 6 else "confirmed",
                "expected_head_id": current["id"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()["evidence_artifact"]
    assert [fact["review_decision"] for fact in current["payload"]["facts"]] == [
        "confirmed"
    ] * 6 + ["rejected"]
    drafts = WorkspaceDraftService(session, now=lambda: datetime.now(UTC))
    draft = drafts.read(UUID(project_id))
    assert draft is not None and draft.content.historical_basis_id is not None
    drafts.save(
        UUID(project_id),
        expected_lock_version=draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=None),
    )
    session.commit()
    model_claim = worker.claim_next()
    assert model_claim is not None and model_claim.step == "model_bundle"
    assert worker.run_claim(model_claim) == "discarded"
    session.commit()
    blocked_workspace = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert blocked_workspace.status_code == 200, blocked_workspace.text
    blocked_preparation = blocked_workspace.json()["preparation"]
    assert (
        blocked_preparation["status"],
        blocked_preparation["current_step"],
        blocked_preparation["progress"],
        blocked_preparation["error"]["code"],
    ) == ("blocked", "model_bundle", 30, "validation_failed")
    blocked_draft = drafts.read(UUID(project_id))
    assert blocked_draft is not None
    blocked_project_id = UUID(project_id)
    blocked_draft_identity = (
        blocked_draft.id,
        blocked_draft.project_id,
        blocked_draft.base_revision_id,
        blocked_draft.created_at,
    )
    blocked_draft_lock_version = blocked_draft.lock_version
    blocked_draft_content = blocked_draft.content.model_copy(deep=True)
    assert blocked_draft.project_id == blocked_project_id
    assert blocked_draft_content.historical_basis_id is None
    blocked_artifacts = blocked_workspace.json()["artifacts"]
    blocked_artifact_heads = {
        artifact["kind"]: (
            artifact["id"],
            artifact["version"],
            artifact["content_hash"],
        )
        for artifact in blocked_artifacts
    }
    blocked_source_artifacts = {
        artifact["kind"]: (
            artifact["id"],
            artifact["version"],
            artifact["content_hash"],
            deepcopy(artifact["payload"]),
        )
        for artifact in blocked_artifacts
        if artifact["kind"] in {"evidence_index", "research_gaps"}
    }
    assert set(blocked_source_artifacts) == {"evidence_index", "research_gaps"}
    ordered_decisions = tuple(
        fact["review_decision"]
        for fact in blocked_source_artifacts["evidence_index"][3]["facts"]
    )
    assert ordered_decisions == ("confirmed",) * 6 + ("rejected",)

    retried = api_client.post(f"{BASE}/projects/{project_id}/retry")

    assert retried.status_code == 202, retried.text
    retried_preparation = retried.json()["preparation"]
    assert (
        retried_preparation["status"],
        retried_preparation["current_step"],
        retried_preparation["progress"],
    ) == ("building_model", "model_bundle", 25)
    recovered = drafts.read(UUID(project_id))
    assert recovered is not None and recovered.content.historical_basis_id is not None
    assert (
        recovered.id,
        recovered.project_id,
        recovered.base_revision_id,
        recovered.created_at,
    ) == blocked_draft_identity
    assert recovered.project_id == blocked_project_id
    assert recovered.lock_version == blocked_draft_lock_version + 1
    assert (
        recovered.content.model_copy(update={"historical_basis_id": None}, deep=True)
        == blocked_draft_content
    )
    workspace_after = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert workspace_after.status_code == 200, workspace_after.text
    artifacts_after = workspace_after.json()["artifacts"]
    artifact_heads_after = {
        artifact["kind"]: (
            artifact["id"],
            artifact["version"],
            artifact["content_hash"],
        )
        for artifact in artifacts_after
    }
    source_artifacts_after = {
        artifact["kind"]: (
            artifact["id"],
            artifact["version"],
            artifact["content_hash"],
            deepcopy(artifact["payload"]),
        )
        for artifact in artifacts_after
        if artifact["kind"] in blocked_source_artifacts
    }
    assert artifact_heads_after == blocked_artifact_heads
    assert source_artifacts_after == blocked_source_artifacts
    retry_worker = CompanyResearchPreparationWorker(
        session, now=lambda: datetime.now(UTC)
    )
    retry_claim = retry_worker.claim_next()
    assert retry_claim is not None and retry_claim.step == "model_bundle"
    assert retry_worker.run_claim(retry_claim) == "awaiting_judgment_review"
    final = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert final.status_code == 200, final.text
    final_preparation = final.json()["preparation"]
    assert (
        final_preparation["status"],
        final_preparation["current_step"],
        final_preparation["progress"],
        final_preparation["error"],
    ) == ("awaiting_judgment_review", "judgment_context", 85, None)
    final_source_artifacts = {
        artifact["kind"]: (
            artifact["id"],
            artifact["version"],
            artifact["content_hash"],
            deepcopy(artifact["payload"]),
        )
        for artifact in final.json()["artifacts"]
        if artifact["kind"] in blocked_source_artifacts
    }
    assert final_source_artifacts == blocked_source_artifacts
    final_decisions = tuple(
        fact["review_decision"]
        for fact in final_source_artifacts["evidence_index"][3]["facts"]
    )
    assert final_decisions == ordered_decisions
    assert len(final_decisions) == 7
    assert final_decisions.count("confirmed") == 6
    assert final_decisions.count("rejected") == 1


def test_workspace_projects_internal_memo_gaps_to_the_existing_public_contract(
    api_client, session
) -> None:
    from tests.underwriting.test_company_research_workbench import _model_workspace

    initialized, _workbench, repository = _model_workspace(session)
    raw_memo = repository.current_artifact(initialized.project.id, "memo")
    assert raw_memo is not None
    assert isinstance(raw_memo.payload["research_gaps"], list)

    response = api_client.get(f"{BASE}/projects/{initialized.project.id}/workspace")

    assert response.status_code == 200, response.text
    body = response.json()
    memo = next(item for item in body["artifacts"] if item["kind"] == "memo")
    assert set(memo["payload"]) == {
        "assessment_status",
        "business_map_ref",
        "driver_map_ref",
        "financial_bridge_ref",
        "scenario_set_ref",
        "valuation_set_ref",
        "gap_keys",
        "strongest_counterevidence",
        "next_verification_events",
        "candidate_status",
        "_lineage",
    }
    assert "research_gaps" not in memo["payload"]
    assert body["gap_count"] == len(raw_memo.payload["research_gaps"])


def test_workspace_api_reads_a_true_legacy_model_gap_successor(
    api_client, session
) -> None:
    from tests.underwriting.test_company_research_workbench import (
        _model_workspace,
        _rewrite_as_legacy_model_gap_successor,
    )

    initialized, _workbench, repository = _model_workspace(session)
    _source_gaps, legacy_gaps = _rewrite_as_legacy_model_gap_successor(
        session, initialized, repository
    )

    response = api_client.get(f"{BASE}/projects/{initialized.project.id}/workspace")

    assert response.status_code == 200, response.text
    body = response.json()
    visible_gaps = next(
        item for item in body["artifacts"] if item["kind"] == "research_gaps"
    )
    memo = next(item for item in body["artifacts"] if item["kind"] == "memo")
    assert (visible_gaps["id"], visible_gaps["version"]) == (
        str(legacy_gaps.id),
        2,
    )
    assert "research_gaps" not in memo["payload"]
    assert body["gap_count"] == len(legacy_gaps.payload["gaps"])


def test_retry_returns_422_for_a_malformed_durable_review_decision(
    api_client, session
) -> None:
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
    project_id = UUID(initialized.json()["project_id"])
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "awaiting_evidence_review"
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
    drafts = WorkspaceDraftService(session, now=lambda: datetime.now(UTC))
    draft = drafts.read(project_id)
    assert draft is not None and draft.content.historical_basis_id is not None
    drafts.save(
        project_id,
        expected_lock_version=draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=None),
    )
    session.commit()
    model_claim = worker.claim_next()
    assert model_claim is not None and model_claim.step == "model_bundle"
    assert worker.run_claim(model_claim) == "discarded"
    session.commit()
    _rewrite_head_decision_as_malformed(session, UUID(current["id"]))
    session.commit()

    response = api_client.post(f"{BASE}/projects/{project_id}/retry")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == (
        "company research historical basis recovery evidence is invalid"
    )
    preparation = session.get(CompanyResearchPreparation, model_claim.preparation_id)
    draft_after = drafts.read(project_id)
    assert preparation is not None and preparation.status == "blocked"
    assert draft_after is not None
    assert draft_after.content.historical_basis_id is None


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
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
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
        "product_progress",
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


def test_rejected_evidence_counts_as_reviewed_but_cannot_feed_downstream(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id)
    initialized = _initialize(api_client, company_id, preview["preview_hash"])
    assert initialized.status_code == 201
    project_id = initialized.json()["project_id"]
    worker = CompanyResearchPreparationWorker(session, now=lambda: datetime.now(UTC))
    claim = worker.claim_next()
    assert claim is not None and worker.run_claim(claim) == "awaiting_evidence_review"
    before = api_client.get(f"{BASE}/projects/{project_id}/workspace").json()
    evidence = next(
        item for item in before["artifacts"] if item["kind"] == "evidence_index"
    )

    reviewed = api_client.post(
        f"{BASE}/projects/{project_id}/evidence-reviews",
        json={
            "evidence_artifact_id": evidence["id"],
            "fact_key": evidence["payload"]["facts"][0]["fact_key"],
            "decision": "rejected",
            "expected_head_id": evidence["id"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    after = api_client.get(f"{BASE}/projects/{project_id}/workspace")
    assert after.status_code == 200, after.text
    body = after.json()
    assert body["change_summary"]["reviewed_fact_count"] == 1
    reviewed_evidence = next(
        item for item in body["artifacts"] if item["kind"] == "evidence_index"
    )
    assert reviewed_evidence["payload"]["facts"][0]["review_decision"] == "rejected"
    assert {item["kind"] for item in body["artifacts"]} == {
        "evidence_index",
        "research_gaps",
    }
