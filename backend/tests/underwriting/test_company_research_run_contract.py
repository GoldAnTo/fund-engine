"""The product run is a projection of a durable company preparation."""
import pytest
from tests.underwriting.test_company_research_api import BASE, NOW, _alphabet_id


def _preview(client, company_id, **extra):
    return client.post(f"{BASE}/preview", json={
        "company_id": str(company_id), "cutoff_at": NOW.isoformat(), **extra,
    })


def test_focus_normalization_preserves_legacy_preview_hash(api_client, session):
    company_id = _alphabet_id(session)
    old = _preview(api_client, company_id)
    assert old.json()["requested_cutoff_at"] == NOW.isoformat().replace("+00:00", "Z")
    blank = _preview(api_client, company_id, user_focus=" \n\t ")
    assert blank.status_code == 200, blank.text
    assert blank.json()["user_focus"] is None
    assert blank.json()["preview_hash"] == old.json()["preview_hash"]
    first = _preview(api_client, company_id, user_focus="  Cafe\u0301 的现金流  ")
    second = _preview(api_client, company_id, user_focus="Café 的现金流")
    assert first.status_code == 200, first.text
    assert first.json()["user_focus"] == "Café 的现金流"
    assert first.json()["preview_hash"] == second.json()["preview_hash"]
    assert first.json()["preview_hash"] != old.json()["preview_hash"]


@pytest.mark.parametrize("focus", ["x" * 2001, 123, ["question"]])
def test_focus_rejects_invalid_values(api_client, session, focus):
    assert _preview(api_client, _alphabet_id(session), user_focus=focus).status_code == 422


def test_initialized_run_persists_focus_and_actual_cutoff_and_binds_idempotency(api_client, session):
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id, user_focus="  AI 资本开支回报  ")
    assert preview.status_code == 200, preview.text
    body = {"company_id": str(company_id), "cutoff_at": NOW.isoformat(),
            "preview_hash": preview.json()["preview_hash"], "user_focus": "AI 资本开支回报"}
    created = api_client.post(f"{BASE}/initializations", json=body, headers={"Idempotency-Key": "focus-run"})
    assert created.status_code == 201, created.text
    session.expire_all()
    result = api_client.get(f"{BASE}/projects/{created.json()['project_id']}/workspace")
    assert result.status_code == 200, result.text
    progress = result.json()["product_progress"]
    assert progress == {
        "schema_version": "underwriting.v1", "run_id": created.json()["preparation"]["id"],
        "project_id": created.json()["project_id"], "company_id": str(company_id),
        "status": "queued", "current_step": "evidence_index", "progress_percent": 0,
        "user_focus": "AI 资本开支回报", "cutoff_at": preview.json()["cutoff_at"],
        "retryable": False, "error_code": None,
    }
    status = api_client.get(f"{BASE}/projects/{created.json()['project_id']}")
    assert status.json()["product_progress"] == progress
    assert created.json()["product_progress"] == progress
    replay = api_client.post(f"{BASE}/initializations", json={**body, "user_focus": "  AI 资本开支回报 "}, headers={"Idempotency-Key": "focus-run"})
    assert replay.status_code == 201, replay.text
    assert replay.json() == created.json()
    changed = {**body, "user_focus": "广告增长"}
    assert api_client.post(f"{BASE}/initializations", json=changed, headers={"Idempotency-Key": "focus-run-2"}).status_code == 422
    next_preview = _preview(api_client, company_id, user_focus="广告增长").json()
    assert api_client.post(f"{BASE}/initializations", json={**changed, "preview_hash": next_preview["preview_hash"]}, headers={"Idempotency-Key": "focus-run"}).status_code == 409


@pytest.mark.parametrize("status,step,expected", [
    ("queued", "evidence_index", "queued"),
    ("preparing_sources", "evidence_index", "collecting_sources"),
    ("awaiting_evidence_review", "research_gaps", "needs_input"),
    ("building_model", "business_map", "analyzing_company"),
    ("building_model", "driver_map", "analyzing_company"),
    ("building_model", "model_bundle", "analyzing_company"),
    ("building_model", "financial_bridge", "building_forecast"),
    ("building_model", "scenario_set", "building_forecast"),
    ("building_model", "valuation_set", "building_forecast"),
    ("building_model", "judgment_context", "generating_report"),
    ("building_model", "memo", "generating_report"),
    ("awaiting_judgment_review", "judgment_context", "needs_input"),
    ("ready_to_freeze", "memo", "completed"),
    ("completed", None, "completed"),
    ("recoverable_failure", "model_bundle", "failed"),
    ("blocked", "evidence_index", "failed"),
])
def test_product_status_tracks_real_preparation_stage(status, step, expected):
    from app.underwriting.domain.company_research import company_research_product_status
    assert company_research_product_status(status, step) == expected


@pytest.mark.parametrize("changed_focus", ["被替换的关注问题", 42, "x" * 2001, "__missing__"])
def test_publication_rejects_rehashed_scope_focus_substitution(api_client, session, changed_focus):
    from uuid import UUID
    from sqlalchemy import select
    from app.underwriting.persistence.product_models import UnderwritingResearchScopeVersion
    from app.underwriting.services.product_project import research_scope_content_hash
    from tests.underwriting.test_company_research_api import _run_public_company_research_pipeline
    from tests.underwriting.test_company_research_persistence import _tamper_row

    workspace = _run_public_company_research_pipeline(api_client, session, fixed_model_clock=True, user_focus="长期现金流")
    project_id = UUID(workspace["project_id"])
    scope = session.scalar(select(UnderwritingResearchScopeVersion).where(UnderwritingResearchScopeVersion.project_id == project_id))
    payload = {**scope.payload, "user_focus": changed_focus}
    if changed_focus == "__missing__":
        del payload["user_focus"]
    _tamper_row(session, UnderwritingResearchScopeVersion, scope.id, payload=payload,
                content_hash=research_scope_content_hash(project_id=project_id, payload=payload))
    session.commit()
    memo = next(item for item in workspace["artifacts"] if item["kind"] == "memo")
    confirmation = api_client.post(f"{BASE}/projects/{project_id}/judgment-confirmations", json={
        "expected_lock_version": workspace["draft"]["lock_version"],
        "expected_memo_id": memo["id"], "expected_memo_content_hash": memo["content_hash"],
        "markdown": "证据不足。",
    })
    assert confirmation.status_code == 422, confirmation.text
    assert confirmation.json()["error"]["code"] == "validation_failed"


def test_generic_draft_edit_cannot_redefine_the_initialized_run_focus(api_client, session):
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id, user_focus="原始重点").json()
    created = api_client.post(f"{BASE}/initializations", json={
        "company_id": str(company_id), "cutoff_at": NOW.isoformat(),
        "preview_hash": preview["preview_hash"], "user_focus": "原始重点",
    }, headers={"Idempotency-Key": "scope-substitution"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    product = "/api/underwriting/v1/product"
    draft = api_client.get(f"{product}/projects/{project_id}/draft").json()
    scope = api_client.post(f"{product}/projects/{project_id}/scopes", json={
        "primary_company_id": str(company_id),
        "target_security_ids": [security["object_id"] for security in preview["securities"]],
        "user_focus": "替换重点", "expected_parent_id": draft["content"]["scope_id"],
    })
    assert scope.status_code == 201, scope.text
    edited = api_client.patch(f"{product}/projects/{project_id}/draft", json={
        "expected_lock_version": draft["lock_version"], "scope_id": scope.json()["id"],
    })
    assert edited.status_code == 200, edited.text
    for path in [f"{BASE}/projects/{project_id}", f"{BASE}/projects/{project_id}/workspace"]:
        response = api_client.get(path)
        assert response.status_code == 422, response.text


def test_direct_basis_recovery_rejects_rehashed_focus_substitution(session):
    from app.models.ledger import ValidationError
    from app.underwriting.persistence.product_models import UnderwritingResearchScopeVersion
    from app.underwriting.services.company_research_basis_recovery import CompanyResearchHistoricalBasisRecovery
    from app.underwriting.services.product_project import research_scope_content_hash
    from tests.underwriting.test_company_research_persistence import _tamper_row
    from tests.underwriting.test_company_research_worker import _legacy_blocked_missing_basis

    initialized, _, _, _ = _legacy_blocked_missing_basis(session)
    scope = initialized.scope
    payload = {**scope.payload, "user_focus": "替换重点"}
    _tamper_row(session, UnderwritingResearchScopeVersion, scope.id, payload=payload,
                content_hash=research_scope_content_hash(project_id=initialized.project.id, payload=payload))
    with pytest.raises(ValidationError):
        CompanyResearchHistoricalBasisRecovery(session, now=lambda: NOW).recover(
            initialized.preparation.id, retry_at=NOW,
        )
