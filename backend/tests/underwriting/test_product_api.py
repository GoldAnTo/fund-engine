"""HTTP contract for the independent investment-research product foundation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from app.underwriting.api.product_schemas import (
    AgendaGeneratorResponse,
    PublicationPreviewResponse,
)
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAssessmentVersion,
    UnderwritingResearchProject,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.services.product_project import ResearchProjectService


BASE = "/api/underwriting/v1/product"
NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
MARKET = datetime(2026, 8, 20, 7, 30, tzinfo=UTC)
EFFECTIVE = datetime(2020, 1, 1, tzinfo=UTC)
A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64
D64 = "d" * 64


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_agenda_generator_response_rejects_invalid_input_summary_hash() -> None:
    with pytest.raises(PydanticValidationError, match="input_summary_hash"):
        AgendaGeneratorResponse(
            method="ai_generated",
            template_key=None,
            template_version=None,
            model_name="research-model",
            prompt_template_version="v1",
            input_summary_hash="not-a-hash",
            output_hash=A64,
        )


def _seed_object(session, kind: str, key: str, name: str):
    row = UnderwritingResearchObject(
        kind=kind,
        external_key=f"{key}:{uuid4()}",
        canonical_name=name,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _seed_catalog(session, *, suffix: str = "main") -> dict[str, object]:
    company = _seed_object(session, "company", f"company-{suffix}", f"CATL {suffix}")
    security = _seed_object(session, "security", f"security-{suffix}", "300750.SZ")
    industry = _seed_object(session, "industry", f"industry-{suffix}", "Batteries")
    session.add_all(
        (
            UnderwritingObjectRelation(
                parent_id=company.id,
                child_id=security.id,
                relation_type="company_has_security",
                created_at=NOW,
            ),
            UnderwritingObjectRelation(
                parent_id=industry.id,
                child_id=company.id,
                relation_type="industry_exposes_company",
                created_at=NOW,
            ),
        )
    )
    session.flush()
    service = ResearchProjectService(session, now=lambda: NOW)
    company_identity = service.append_identity_version(
        object_id=company.id,
        canonical_name=company.canonical_name,
        symbol=None,
        exchange=None,
        share_class=None,
        trading_currency=None,
        effective_from=EFFECTIVE,
        effective_to=None,
        expected_parent_id=None,
    )
    security_identity = service.append_identity_version(
        object_id=security.id,
        canonical_name=security.canonical_name,
        symbol="300750",
        exchange="SZSE",
        share_class="ordinary",
        trading_currency="CNY",
        effective_from=EFFECTIVE,
        effective_to=None,
        expected_parent_id=None,
    )
    industry_identity = service.append_identity_version(
        object_id=industry.id,
        canonical_name=industry.canonical_name,
        symbol=None,
        exchange=None,
        share_class=None,
        trading_currency=None,
        effective_from=EFFECTIVE,
        effective_to=None,
        expected_parent_id=None,
    )
    return locals()


def _create_project(api_client, catalog: dict[str, object]) -> dict:
    response = api_client.post(
        f"{BASE}/projects",
        json={
            "primary_company_id": str(catalog["company"].id),
            "target_security_ids": [str(catalog["security"].id)],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _write_foundation(api_client, project: dict, catalog: dict[str, object]) -> dict:
    project_id = project["id"]
    mandate = api_client.post(
        f"{BASE}/projects/{project_id}/mandates",
        json={
            "horizon_years": 3,
            "base_currency": "CNY",
            "required_return": "0.10000000",
            "permanent_loss_limit": "0.30000000",
            "comparison_set": ["cash"],
            "benchmark_key": None,
            "required_excess_return": None,
            "effective_at": EFFECTIVE.isoformat(),
            "expires_at": None,
            "expected_parent_id": None,
        },
    )
    assert mandate.status_code == 201, mandate.text
    scope = api_client.post(
        f"{BASE}/projects/{project_id}/scopes",
        json={
            "primary_company_id": str(catalog["company"].id),
            "target_security_ids": [str(catalog["security"].id)],
            "industry_ids": [str(catalog["industry"].id)],
            "covered_segments": ["core"],
            "user_focus": "durable cash returns",
            "exclusions": [],
            "expected_parent_id": None,
        },
    )
    assert scope.status_code == 201, scope.text
    items = ["business baseline", "valuation gaps"]
    from app.underwriting.domain.product_contracts import agenda_items_hash

    agenda = api_client.post(
        f"{BASE}/projects/{project_id}/agendas",
        json={
            "scope_id": scope.json()["id"],
            "items": items,
            "generator": {
                "method": "deterministic_template",
                "template_key": "foundation",
                "template_version": "v1",
                "model_name": None,
                "prompt_template_version": None,
                "input_summary_hash": None,
                "output_hash": agenda_items_hash(tuple(items)),
            },
            "expected_parent_id": None,
        },
    )
    assert agenda.status_code == 201, agenda.text
    basis = api_client.post(
        f"{BASE}/historical-bases",
        json={
            "cutoff_at": datetime(2026, 8, 19, tzinfo=UTC).isoformat(),
            "source_manifest_hash": A64,
            "definition_bundle_hash": B64,
            "parser_bundle_hash": C64,
        },
    )
    assert basis.status_code == 201, basis.text
    price = api_client.post(
        f"{BASE}/market/price-snapshots",
        json={
            "security_identity_id": str(catalog["security"].id),
            "price": "100.0000000000",
            "currency": "CNY",
            "price_type": "close",
            "adjustment_basis": "unadjusted",
            "market_at": MARKET.isoformat(),
            "available_at": (MARKET + timedelta(minutes=5)).isoformat(),
            "source_id": "exchange",
            "raw_hash": A64,
        },
    )
    assert price.status_code == 201, price.text
    fx = api_client.post(
        f"{BASE}/market/fx-snapshots",
        json={
            "base_currency": "USD",
            "quote_currency": "CNY",
            "rate": "7.100000000000",
            "quote_direction": "quote_per_base",
            "market_at": MARKET.isoformat(),
            "available_at": (MARKET + timedelta(minutes=5)).isoformat(),
            "source_id": "central-bank",
            "raw_hash": B64,
        },
    )
    assert fx.status_code == 201, fx.text
    capital = api_client.post(
        f"{BASE}/market/capital-structure-snapshots",
        json={
            "company_id": str(catalog["company"].id),
            "currency": "CNY",
            "cash": "10.0000000000",
            "debt": "2.0000000000",
            "minority_interest": "0",
            "investments": "0",
            "pension_liabilities": "0",
            "other_adjustments": "0",
            "basic_shares": "100.0000000000",
            "diluted_shares": "100.0000000000",
            "potential_dilution_descriptors": [],
            "report_period_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "report_period_end": datetime(2026, 6, 30, tzinfo=UTC).isoformat(),
            "market_at": MARKET.isoformat(),
            "available_at": (MARKET + timedelta(minutes=5)).isoformat(),
            "source_id": "filing",
            "raw_hash": C64,
        },
    )
    assert capital.status_code == 201, capital.text
    rights = api_client.post(
        f"{BASE}/market/security-rights",
        json={
            "security_identity_id": str(catalog["security"].id),
            "economic_units": "1.0000000000",
            "votes_per_unit": "1.0000000000",
            "conversion_ratio": "1.0000000000",
            "adr_ratio": "1.0000000000",
            "dividend_rights_per_unit": "1.0000000000",
            "effective_from": EFFECTIVE.isoformat(),
            "effective_to": None,
            "source_id": "listing-rules",
            "raw_hash": D64,
            "expected_parent_id": None,
        },
    )
    assert rights.status_code == 201, rights.text
    return {
        "mandate": mandate.json(),
        "scope": scope.json(),
        "agenda": agenda.json(),
        "basis": basis.json(),
        "price": price.json(),
        "fx": fx.json(),
        "capital": capital.json(),
        "rights": rights.json(),
    }


def test_product_http_foundation_round_trip_is_exact_and_idempotent(
    api_client, session
) -> None:
    catalog = _seed_catalog(session)
    as_of = NOW.isoformat()

    search = api_client.get(
        f"{BASE}/objects", params={"query": "300750", "as_of": as_of}
    )
    assert search.status_code == 200, search.text
    assert search.json()["items"] == [
        {
            "schema_version": "underwriting.v1",
            "object_id": str(catalog["security"].id),
            "identity_version_id": str(catalog["security_identity"].id),
            "kind": "security",
            "external_key": catalog["security"].external_key,
            "canonical_name": "300750.SZ",
            "symbol": "300750",
            "exchange": "SZSE",
            "share_class": "ordinary",
            "trading_currency": "CNY",
        }
    ]

    project = _create_project(api_client, catalog)
    assert project["primary_company_id"] == str(catalog["company"].id)
    assert project["target_security_ids"] == [str(catalog["security"].id)]
    project_id = project["id"]

    listed = api_client.get(f"{BASE}/projects", params={"limit": 20})
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [project_id]
    detail = api_client.get(f"{BASE}/projects/{project_id}")
    assert detail.status_code == 200 and detail.json() == project

    draft = api_client.get(f"{BASE}/projects/{project_id}/draft")
    assert draft.status_code == 200, draft.text
    assert draft.json()["lock_version"] == 1
    assert draft.json()["content"]["publication_status"] == "draft"

    foundation = _write_foundation(api_client, project, catalog)
    assert foundation["basis"]["price_as_of"] is None
    assert foundation["scope"]["payload"]["primary_company_id"] == str(
        catalog["company"].id
    )
    assert foundation["agenda"]["generator_provenance"]["method"] == (
        "deterministic_template"
    )
    assert foundation["fx"]["quote_direction"] == "quote_per_base"

    patch = api_client.patch(
        f"{BASE}/projects/{project_id}/draft",
        json={
            "expected_lock_version": draft.json()["lock_version"],
            "mandate_id": foundation["mandate"]["id"],
            "scope_id": foundation["scope"]["id"],
            "agenda_id": foundation["agenda"]["id"],
            "historical_basis_id": foundation["basis"]["id"],
            "price_snapshot_ids": [foundation["price"]["id"]],
            "fx_snapshot_ids": [],
            "capital_structure_snapshot_id": foundation["capital"]["id"],
            "security_rights_ids": [foundation["rights"]["id"]],
            "user_focus": "durable cash returns",
        },
    )
    assert patch.status_code == 200, patch.text
    patched = patch.json()
    assert patched["lock_version"] == 2

    preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert preview.status_code == 200, preview.text
    assessment = preview.json()["assessment"]
    assert assessment["answerability"] == "not_answerable"
    assert assessment["direction"] is None
    assert assessment["confidence"] is None
    assert assessment["blockers"]
    assert "target_price" not in preview.text.lower()
    assert "action" not in preview.text.lower()

    publish = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "publish-api-1"},
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert publish.status_code == 201, publish.text
    revision = publish.json()
    assert revision["price_snapshot_ids"] == [foundation["price"]["id"]]
    assert revision["fx_snapshot_ids"] == []
    assert revision["capital_structure_snapshot_id"] == foundation["capital"]["id"]
    assert revision["security_rights_ids"] == [foundation["rights"]["id"]]

    retry = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "publish-api-1"},
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert retry.status_code == 201
    assert retry.json() == revision

    stale_other_key = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "publish-api-other"},
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert stale_other_key.status_code == 409
    assert _error_code(stale_other_key) == "conflict"

    selected = api_client.get(f"{BASE}/revisions/{revision['id']}")
    assert selected.status_code == 200, selected.text
    selected_body = selected.json()
    assert selected_body["id"] == revision["id"]
    assert selected_body["project_id"] == project_id
    assert selected_body["market_snapshot_ids"] == [
        foundation["price"]["id"],
        foundation["capital"]["id"],
        foundation["rights"]["id"],
    ]
    assert selected_body["cutoff"] == datetime(
        2026, 8, 19, tzinfo=UTC
    ).isoformat().replace("+00:00", "Z")


def test_publication_preview_never_commits_or_rolls_back_caller_transaction(
    api_client, session, monkeypatch
) -> None:
    catalog = _seed_catalog(session, suffix="preview-transaction")
    project = _create_project(api_client, catalog)
    foundation = _write_foundation(api_client, project, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    patched = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "mandate_id": foundation["mandate"]["id"],
            "scope_id": foundation["scope"]["id"],
            "agenda_id": foundation["agenda"]["id"],
            "historical_basis_id": foundation["basis"]["id"],
            "price_snapshot_ids": [foundation["price"]["id"]],
            "capital_structure_snapshot_id": foundation["capital"]["id"],
            "security_rights_ids": [foundation["rights"]["id"]],
        },
    ).json()
    formal_models = (
        UnderwritingResearchAssessmentVersion,
        UnderwritingRevisionBoundary,
        UnderwritingRevisionManifest,
        UnderwritingResearchVersion,
    )
    before = tuple(
        session.scalar(select(func.count()).select_from(model))
        for model in formal_models
    )
    transaction_calls = {"commit": 0, "rollback": 0}

    def commit_spy() -> None:
        transaction_calls["commit"] += 1

    def rollback_spy() -> None:
        transaction_calls["rollback"] += 1

    monkeypatch.setattr(session, "commit", commit_spy)
    monkeypatch.setattr(session, "rollback", rollback_spy)

    preview = api_client.post(
        f"{BASE}/projects/{project['id']}/publication-preview",
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert preview.status_code == 200, preview.text
    stale = api_client.post(
        f"{BASE}/projects/{project['id']}/publication-preview",
        json={"expected_lock_version": patched["lock_version"] - 1},
    )
    assert stale.status_code == 409, stale.text
    assert _error_code(stale) == "conflict"
    assert transaction_calls == {"commit": 0, "rollback": 0}
    assert (
        tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in formal_models
        )
        == before
        == (0, 0, 0, 0)
    )


@pytest.mark.parametrize("location", ("top", "nested"))
def test_publication_preview_manifest_rejects_unknown_decision_fields(
    api_client, session, location
) -> None:
    catalog = _seed_catalog(session, suffix=f"closed-manifest-{location}")
    project = _create_project(api_client, catalog)
    foundation = _write_foundation(api_client, project, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    patched = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "mandate_id": foundation["mandate"]["id"],
            "scope_id": foundation["scope"]["id"],
            "agenda_id": foundation["agenda"]["id"],
            "historical_basis_id": foundation["basis"]["id"],
            "price_snapshot_ids": [foundation["price"]["id"]],
            "capital_structure_snapshot_id": foundation["capital"]["id"],
            "security_rights_ids": [foundation["rights"]["id"]],
        },
    ).json()
    preview = api_client.post(
        f"{BASE}/projects/{project['id']}/publication-preview",
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    PublicationPreviewResponse.model_validate(payload)
    if location == "top":
        payload["manifest"]["target_price"] = "999"
    else:
        payload["manifest"]["project_ref"]["action"] = "buy"

    with pytest.raises(PydanticValidationError):
        PublicationPreviewResponse.model_validate(payload)


def test_product_api_uses_404_409_and_validation_envelopes(api_client, session) -> None:
    catalog = _seed_catalog(session)
    missing = uuid4()
    for path in (
        f"{BASE}/projects/{missing}",
        f"{BASE}/projects/{missing}/draft",
        f"{BASE}/revisions/{missing}",
    ):
        response = api_client.get(path)
        assert response.status_code == 404, response.text
        assert _error_code(response) == "not_found"

    project = _create_project(api_client, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    first = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "user_focus": "first",
        },
    )
    assert first.status_code == 200
    stale = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "user_focus": "stale",
        },
    )
    assert stale.status_code == 409
    assert _error_code(stale) == "conflict"

    missing_header = api_client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json={"expected_lock_version": first.json()["lock_version"]},
    )
    assert missing_header.status_code == 422
    assert _error_code(missing_header) == "validation_failed"

    overlong_header = api_client.post(
        f"{BASE}/projects/{project['id']}/publish",
        headers={"Idempotency-Key": "x" * 121},
        json={"expected_lock_version": first.json()["lock_version"]},
    )
    assert overlong_header.status_code == 422
    assert _error_code(overlong_header) == "validation_failed"

    industry_project = api_client.post(
        f"{BASE}/projects",
        json={
            "primary_company_id": str(catalog["industry"].id),
            "target_security_ids": [str(catalog["security"].id)],
        },
    )
    assert industry_project.status_code == 422
    assert _error_code(industry_project) == "validation_failed"


def test_historical_product_revision_corruption_is_an_internal_error(
    api_client, session
) -> None:
    catalog = _seed_catalog(session, suffix="corrupt-revision")
    project = _create_project(api_client, catalog)
    foundation = _write_foundation(api_client, project, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    patched = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "mandate_id": foundation["mandate"]["id"],
            "scope_id": foundation["scope"]["id"],
            "agenda_id": foundation["agenda"]["id"],
            "historical_basis_id": foundation["basis"]["id"],
            "price_snapshot_ids": [foundation["price"]["id"]],
            "capital_structure_snapshot_id": foundation["capital"]["id"],
            "security_rights_ids": [foundation["rights"]["id"]],
        },
    ).json()
    published = api_client.post(
        f"{BASE}/projects/{project['id']}/publish",
        headers={"Idempotency-Key": "corrupt-revision"},
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert published.status_code == 201, published.text
    revision = published.json()
    manifest = session.get(UnderwritingRevisionManifest, UUID(revision["manifest_id"]))
    assert manifest is not None
    session.connection().exec_driver_sql(
        "UPDATE uw_revision_manifests SET content_hash = ? WHERE id = ?",
        (B64, manifest.id.hex),
    )
    session.expire_all()

    selected = api_client.get(f"{BASE}/revisions/{revision['id']}")

    assert selected.status_code == 500, selected.text
    assert selected.json()["error"] == {
        "code": "internal_error",
        "message": "internal error",
        "request_id": selected.json()["error"]["request_id"],
        "details": {},
    }


def test_project_list_returns_most_recent_projects_without_per_project_reads(
    api_client, session, monkeypatch
) -> None:
    catalog = _seed_catalog(session, suffix="recent-list")
    timestamps = (
        NOW - timedelta(days=2),
        NOW - timedelta(days=2),
        NOW - timedelta(days=1),
        NOW - timedelta(days=1),
        NOW,
        NOW,
    )
    values = iter(timestamps)
    monkeypatch.setattr(
        "app.underwriting.api.product_router._now", lambda: next(values)
    )
    projects = tuple(_create_project(api_client, catalog) for _ in range(3))

    response = api_client.get(f"{BASE}/projects", params={"limit": 2})

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [
        projects[2]["id"],
        projects[1]["id"],
    ]


def test_product_preview_rejects_cross_project_references(api_client, session) -> None:
    first_catalog = _seed_catalog(session, suffix="first")
    second_catalog = _seed_catalog(session, suffix="second")
    first_project = _create_project(api_client, first_catalog)
    second_project = _create_project(api_client, second_catalog)
    first = _write_foundation(api_client, first_project, first_catalog)
    second = _write_foundation(api_client, second_project, second_catalog)
    draft = api_client.get(f"{BASE}/projects/{first_project['id']}/draft").json()

    crossed = api_client.patch(
        f"{BASE}/projects/{first_project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "mandate_id": first["mandate"]["id"],
            "scope_id": second["scope"]["id"],
            "agenda_id": first["agenda"]["id"],
            "historical_basis_id": first["basis"]["id"],
            "price_snapshot_ids": [first["price"]["id"]],
            "capital_structure_snapshot_id": first["capital"]["id"],
            "security_rights_ids": [first["rights"]["id"]],
        },
    )
    assert crossed.status_code == 200
    preview = api_client.post(
        f"{BASE}/projects/{first_project['id']}/publication-preview",
        json={"expected_lock_version": crossed.json()["lock_version"]},
    )
    assert preview.status_code == 422
    assert _error_code(preview) == "validation_failed"


def test_product_agenda_nested_unknown_and_hash_mismatch_use_422(
    api_client, session
) -> None:
    catalog = _seed_catalog(session)
    project = _create_project(api_client, catalog)
    scope = api_client.post(
        f"{BASE}/projects/{project['id']}/scopes",
        json={
            "primary_company_id": str(catalog["company"].id),
            "target_security_ids": [str(catalog["security"].id)],
            "industry_ids": [],
            "covered_segments": [],
            "user_focus": None,
            "exclusions": [],
            "expected_parent_id": None,
        },
    )
    assert scope.status_code == 201
    payload = {
        "scope_id": scope.json()["id"],
        "items": ["baseline"],
        "generator": {
            "method": "deterministic_template",
            "template_key": "foundation",
            "template_version": "v1",
            "model_name": None,
            "prompt_template_version": None,
            "input_summary_hash": None,
            "output_hash": A64,
        },
        "expected_parent_id": None,
    }
    mismatch = api_client.post(f"{BASE}/projects/{project['id']}/agendas", json=payload)
    assert mismatch.status_code == 422
    assert _error_code(mismatch) == "validation_failed"
    payload["generator"]["unexpected"] = True
    nested_unknown = api_client.post(
        f"{BASE}/projects/{project['id']}/agendas", json=payload
    )
    assert nested_unknown.status_code == 422
    assert _error_code(nested_unknown) == "validation_failed"


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        (
            "/projects",
            {
                "primary_company_id": str(UUID(int=1)),
                "target_security_ids": [str(UUID(int=2))],
                "unexpected": True,
            },
        ),
        (
            "/historical-bases",
            {
                "cutoff_at": "2026-08-19T00:00:00",
                "source_manifest_hash": A64,
                "definition_bundle_hash": B64,
                "parser_bundle_hash": C64,
            },
        ),
        (
            "/market/price-snapshots",
            {
                "security_identity_id": str(UUID(int=2)),
                "price": True,
                "currency": "CNY",
                "price_type": "close",
                "adjustment_basis": "raw",
                "market_at": MARKET.isoformat(),
                "available_at": MARKET.isoformat(),
                "source_id": "x",
                "raw_hash": A64,
            },
        ),
        (
            "/market/fx-snapshots",
            {
                "base_currency": "USD",
                "quote_currency": "CNY",
                "rate": "NaN",
                "quote_direction": "quote_per_base",
                "market_at": MARKET.isoformat(),
                "available_at": MARKET.isoformat(),
                "source_id": "x",
                "raw_hash": A64,
            },
        ),
    ),
)
def test_product_api_rejects_unknown_naive_bool_and_nonfinite_values(
    api_client, path, payload
) -> None:
    response = api_client.post(f"{BASE}{path}", json=payload)
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


@pytest.mark.parametrize(
    "forbidden",
    ("target_price", "action", "position", "recommendation", "publication_status"),
)
def test_product_draft_rejects_decision_and_formal_status_fields(
    api_client, session, forbidden
) -> None:
    catalog = _seed_catalog(session, suffix=forbidden)
    project = _create_project(api_client, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    response = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={"expected_lock_version": draft["lock_version"], forbidden: "forbidden"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_project_and_draft_creation_roll_back_together(
    api_client, session, monkeypatch
) -> None:
    catalog = _seed_catalog(session)

    def fail_create(*_args, **_kwargs):
        raise RuntimeError("forced draft failure")

    monkeypatch.setattr(
        "app.underwriting.api.product_router.WorkspaceDraftService.create",
        fail_create,
    )
    response = api_client.post(
        f"{BASE}/projects",
        json={
            "primary_company_id": str(catalog["company"].id),
            "target_security_ids": [str(catalog["security"].id)],
        },
    )
    assert response.status_code == 500
    assert _error_code(response) == "internal_error"
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingWorkspaceDraft))
        == 0
    )


def test_publish_failure_rolls_back_formal_graph_and_keeps_draft(
    api_client, session, monkeypatch
) -> None:
    catalog = _seed_catalog(session)
    project = _create_project(api_client, catalog)
    foundation = _write_foundation(api_client, project, catalog)
    draft = api_client.get(f"{BASE}/projects/{project['id']}/draft").json()
    patched = api_client.patch(
        f"{BASE}/projects/{project['id']}/draft",
        json={
            "expected_lock_version": draft["lock_version"],
            "mandate_id": foundation["mandate"]["id"],
            "scope_id": foundation["scope"]["id"],
            "agenda_id": foundation["agenda"]["id"],
            "historical_basis_id": foundation["basis"]["id"],
            "price_snapshot_ids": [foundation["price"]["id"]],
            "capital_structure_snapshot_id": foundation["capital"]["id"],
            "security_rights_ids": [foundation["rights"]["id"]],
        },
    ).json()

    def fail_manifest(*_args, **_kwargs):
        raise RuntimeError("forced manifest failure")

    monkeypatch.setattr(
        "app.underwriting.persistence.product_repository.ProductRepository.append_manifest",
        fail_manifest,
    )
    response = api_client.post(
        f"{BASE}/projects/{project['id']}/publish",
        headers={"Idempotency-Key": "rollback-api"},
        json={"expected_lock_version": patched["lock_version"]},
    )
    assert response.status_code == 500
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingRevisionBoundary))
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingRevisionManifest))
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchVersion))
        == 0
    )
    current = api_client.get(f"{BASE}/projects/{project['id']}/draft")
    assert current.status_code == 200
    assert current.json()["lock_version"] == patched["lock_version"]
