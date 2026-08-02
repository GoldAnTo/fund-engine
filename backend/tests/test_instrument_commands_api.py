"""Command-side v1 API tests for instrument write paths.

Covers POST /api/v1/funds, POST /api/v1/funds/{id}/holding-disclosures and
POST /api/v1/companies/{id}/theme-roles. Uses the private-engine ``cmd_*``
fixtures: command endpoints COMMIT, so they never share the session engine.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _seed_company(cmd_session, code: str = "688256") -> object:
    from app.models.ledger import Company

    company = Company(
        code=code, name="寒武纪", type="listed", created_at=datetime.now(UTC)
    )
    cmd_session.add(company)
    cmd_session.flush()
    return company


def _seed_stock(cmd_session, company) -> object:
    from app.models.ledger import Stock

    stock = Stock(
        company_id=company.id,
        code="688256.SH",
        name="寒武纪-U",
        market="SSE",
        created_at=datetime.now(UTC),
    )
    cmd_session.add(stock)
    cmd_session.flush()
    return stock


def _create_fund(cmd_client, code: str = "005827") -> dict:
    response = cmd_client.post(
        "/api/v1/funds",
        json={"code": code, "name": "易方达蓝筹精选", "fund_type": "混合型"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# POST /api/v1/companies & /api/v1/companies/{company_id}/stocks
# ---------------------------------------------------------------------------


def test_create_company_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import Company

    response = cmd_client.post(
        "/api/v1/companies",
        json={"code": "688256.SH", "name": "寒武纪", "type": "listed"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "688256.SH"

    row = cmd_session.scalar(select(Company).where(Company.code == "688256.SH"))
    assert row is not None
    assert str(row.id) == body["id"]


def test_create_company_duplicate_code_is_422(cmd_client, cmd_session):
    from app.models.ledger import Company

    _seed_company(cmd_session, code="688256.SH")

    response = cmd_client.post(
        "/api/v1/companies",
        json={"code": "688256.SH", "name": "另一家", "type": "listed"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"

    count = cmd_session.scalar(
        select(func.count()).select_from(Company).where(Company.code == "688256.SH")
    )
    assert count == 1


def test_create_company_blank_type_is_422(cmd_client):
    response = cmd_client.post(
        "/api/v1/companies",
        json={"code": "688256.SH", "name": "寒武纪", "type": " "},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_create_stock_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import Stock

    company = _seed_company(cmd_session)
    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/stocks",
        json={"code": "688256.SH", "name": "寒武纪-U", "market": "SSE"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["company_id"] == str(company.id)

    row = cmd_session.scalar(select(Stock))
    assert row is not None
    assert row.market == "SSE"


def test_create_stock_missing_company_is_404(cmd_client):
    response = cmd_client.post(
        "/api/v1/companies/00000000-0000-0000-0000-000000000000/stocks",
        json={"code": "688256.SH", "name": "寒武纪-U", "market": "SSE"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_create_stock_duplicate_code_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    _seed_stock(cmd_session, company)

    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/stocks",
        json={"code": "688256.SH", "name": "重复", "market": "SSE"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


# ---------------------------------------------------------------------------
# POST /api/v1/funds
# ---------------------------------------------------------------------------


def test_create_fund_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import Fund

    body = _create_fund(cmd_client)
    assert body["code"] == "005827"
    assert body["name"] == "易方达蓝筹精选"
    assert body["fund_type"] == "混合型"
    assert body["id"]

    fund = cmd_session.scalar(select(Fund).where(Fund.code == "005827"))
    assert fund is not None
    assert str(fund.id) == body["id"]


def test_create_fund_duplicate_code_is_422(cmd_client, cmd_session):
    _create_fund(cmd_client, code="110022")

    response = cmd_client.post(
        "/api/v1/funds",
        json={"code": "110022", "name": "另一只基金", "fund_type": "股票型"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"
    from app.models.ledger import Fund

    count = cmd_session.scalar(
        select(func.count()).select_from(Fund).where(Fund.code == "110022")
    )
    assert count == 1


def test_create_fund_blank_name_is_422(cmd_client):
    response = cmd_client.post(
        "/api/v1/funds",
        json={"code": "000001", "name": "   ", "fund_type": "混合型"},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_create_fund_with_management_company(cmd_client, cmd_session):
    from app.models.ledger import FundCompany

    mgmt = FundCompany(code="E-FUND", name="易方达基金", created_at=datetime.now(UTC))
    cmd_session.add(mgmt)
    cmd_session.flush()

    response = cmd_client.post(
        "/api/v1/funds",
        json={
            "code": "005827",
            "name": "易方达蓝筹精选",
            "fund_type": "混合型",
            "scale": "500.5",
            "establish_date": "2018-09-05",
            "management_company_id": str(mgmt.id),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["management_company_id"] == str(mgmt.id)
    assert body["establish_date"] == "2018-09-05"


def test_create_fund_unknown_management_company_is_422(cmd_client):
    response = cmd_client.post(
        "/api/v1/funds",
        json={
            "code": "005827",
            "name": "易方达蓝筹精选",
            "fund_type": "混合型",
            "management_company_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


# ---------------------------------------------------------------------------
# POST /api/v1/funds/{fund_id}/holding-disclosures
# ---------------------------------------------------------------------------


def _disclosure_payload(stock_id) -> dict:
    return {
        "stock_id": str(stock_id),
        "weight": "4.25",
        "report_period": "2026-06-30",
        "published_at": "2026-07-21T08:00:00+08:00",
        "source": "基金2026年二季报",
    }


def test_create_holding_disclosure_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import HoldingDisclosure

    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)
    fund_id = _create_fund(cmd_client)["id"]

    response = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures",
        json=_disclosure_payload(stock.id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["fund_id"] == fund_id
    assert body["stock_id"] == str(stock.id)
    assert body["report_period"] == "2026-06-30"
    assert float(body["weight"]) == pytest.approx(4.25)

    row = cmd_session.scalar(select(HoldingDisclosure))
    assert row is not None
    assert row.report_period == date(2026, 6, 30)
    assert row.source == "基金2026年二季报"
    assert row.published_at.tzinfo is not None or row.published_at is not None


def test_holding_disclosure_missing_fund_is_404(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)

    response = cmd_client.post(
        "/api/v1/funds/00000000-0000-0000-0000-000000000000/holding-disclosures",
        json=_disclosure_payload(stock.id),
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_holding_disclosure_missing_stock_is_404(cmd_client):
    fund_id = _create_fund(cmd_client)["id"]
    response = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures",
        json=_disclosure_payload("00000000-0000-0000-0000-000000000000"),
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


@pytest.mark.parametrize("weight", ["0", "-1.5", "100.01"])
def test_holding_disclosure_invalid_weight_is_422(cmd_client, cmd_session, weight):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)
    fund_id = _create_fund(cmd_client)["id"]

    payload = _disclosure_payload(stock.id)
    payload["weight"] = weight
    response = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures", json=payload
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_holding_disclosure_published_before_period_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)
    fund_id = _create_fund(cmd_client)["id"]

    payload = _disclosure_payload(stock.id)
    payload["published_at"] = "2026-06-01T00:00:00+08:00"
    response = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures", json=payload
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_holding_disclosure_duplicate_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)
    fund_id = _create_fund(cmd_client)["id"]

    first = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures",
        json=_disclosure_payload(stock.id),
    )
    assert first.status_code == 201, first.text

    second = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures",
        json=_disclosure_payload(stock.id),
    )
    assert second.status_code == 422
    assert _error_code(second) == "validation_failed"

    from app.models.ledger import HoldingDisclosure

    count = cmd_session.scalar(select(func.count()).select_from(HoldingDisclosure))
    assert count == 1


def test_holding_disclosure_naive_published_at_normalized_to_utc(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)
    fund_id = _create_fund(cmd_client)["id"]

    payload = _disclosure_payload(stock.id)
    payload["published_at"] = "2026-07-21T08:00:00"
    response = cmd_client.post(
        f"/api/v1/funds/{fund_id}/holding-disclosures", json=payload
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# POST /api/v1/companies/{company_id}/theme-roles
# ---------------------------------------------------------------------------


def test_create_theme_role_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import ThemeRole

    company = _seed_company(cmd_session)

    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/theme-roles",
        json={
            "role": "算力芯片设计",
            "scope": {"chain": "AI 算力", "segment": "上游"},
            "applicable_from": "2026-01-01",
            "applicable_to": "2026-12-31",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["company_id"] == str(company.id)
    assert body["role"] == "算力芯片设计"
    assert body["scope"] == {"chain": "AI 算力", "segment": "上游"}

    row = cmd_session.scalar(select(ThemeRole))
    assert row is not None
    assert row.applicable_from == date(2026, 1, 1)
    assert row.applicable_to == date(2026, 12, 31)


def test_theme_role_missing_company_is_404(cmd_client):
    response = cmd_client.post(
        "/api/v1/companies/00000000-0000-0000-0000-000000000000/theme-roles",
        json={"role": "算力芯片设计"},
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_theme_role_missing_case_is_404(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/theme-roles",
        json={
            "role": "算力芯片设计",
            "research_case_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


def test_theme_role_blank_role_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/theme-roles",
        json={"role": "  "},
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_theme_role_inverted_applicability_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    response = cmd_client.post(
        f"/api/v1/companies/{company.id}/theme-roles",
        json={
            "role": "算力芯片设计",
            "applicable_from": "2027-01-01",
            "applicable_to": "2026-01-01",
        },
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


# ---------------------------------------------------------------------------
# POST /api/v1/stocks/{stock_id}/valuation-snapshots
# ---------------------------------------------------------------------------


def _valuation_payload() -> dict:
    return {
        "as_of_date": "2026-06-30",
        "metric_name": "PE_TTM",
        "metric_value": "45.2",
        "source": "wind",
        "definition": "总市值/近四月归母净利润",
    }


def test_create_valuation_snapshot_persists_ledger_row(cmd_client, cmd_session):
    from app.models.ledger import ValuationSnapshot

    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)

    response = cmd_client.post(
        f"/api/v1/stocks/{stock.id}/valuation-snapshots",
        json=_valuation_payload(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stock_id"] == str(stock.id)
    assert body["metric_name"] == "PE_TTM"
    assert float(body["metric_value"]) == pytest.approx(45.2)
    assert body["as_of_date"] == "2026-06-30"

    row = cmd_session.scalar(select(ValuationSnapshot))
    assert row is not None
    assert row.as_of_date == date(2026, 6, 30)
    assert row.definition == "总市值/近四月归母净利润"


def test_valuation_snapshot_missing_stock_is_404(cmd_client):
    response = cmd_client.post(
        "/api/v1/stocks/00000000-0000-0000-0000-000000000000/valuation-snapshots",
        json=_valuation_payload(),
    )
    assert response.status_code == 404
    assert _error_code(response) == "not_found"


@pytest.mark.parametrize("field", ["metric_name", "source", "definition"])
def test_valuation_snapshot_blank_field_is_422(cmd_client, cmd_session, field):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)

    payload = _valuation_payload()
    payload[field] = "  "
    response = cmd_client.post(
        f"/api/v1/stocks/{stock.id}/valuation-snapshots", json=payload
    )
    assert response.status_code == 422
    assert _error_code(response) == "validation_failed"


def test_valuation_snapshot_nan_value_is_422(cmd_client, cmd_session):
    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)

    payload = _valuation_payload()
    payload["metric_value"] = "NaN"
    response = cmd_client.post(
        f"/api/v1/stocks/{stock.id}/valuation-snapshots", json=payload
    )
    assert response.status_code == 422


def test_valuation_snapshot_duplicate_is_422(cmd_client, cmd_session):
    from app.models.ledger import ValuationSnapshot

    company = _seed_company(cmd_session)
    stock = _seed_stock(cmd_session, company)

    first = cmd_client.post(
        f"/api/v1/stocks/{stock.id}/valuation-snapshots",
        json=_valuation_payload(),
    )
    assert first.status_code == 201, first.text

    second = cmd_client.post(
        f"/api/v1/stocks/{stock.id}/valuation-snapshots",
        json=_valuation_payload(),
    )
    assert second.status_code == 422
    assert _error_code(second) == "validation_failed"

    count = cmd_session.scalar(select(func.count()).select_from(ValuationSnapshot))
    assert count == 1
