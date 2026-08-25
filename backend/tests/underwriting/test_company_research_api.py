"""Closed HTTP contract for the high-level company research flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchPreparation,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)

BASE = "/api/underwriting/v1/product/company-research"
NOW = datetime(2026, 8, 25, 9, tzinfo=UTC)


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
    assert preparation is not None
    preparation.status = "recoverable_failure"
    preparation.current_step = "evidence_index"
    preparation.progress = 35
    preparation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    preparation.last_error_code = "source_unavailable"
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
