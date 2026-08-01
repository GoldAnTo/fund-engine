"""Read-extension v1 API tests: snapshot compare, penetration, metrics.

All endpoints here are read-only (no commits), so the shared seeded-session
fixtures are safe.
"""
from __future__ import annotations

from decimal import Decimal

BASE = "2020-01-01T00:00:00Z"
COMPARE = "2099-01-01T00:00:00Z"


def _seeded_case_id(seeded_session) -> str:
    from sqlalchemy import select

    from app.models.ledger import ResearchCase

    return str(seeded_session.scalar(select(ResearchCase)).id)


# ---------------------------------------------------------------------------
# 快照比较: GET /api/v1/research-cases/{id}/compare
# ---------------------------------------------------------------------------


def test_compare_shows_all_links_added_from_empty_base(
    api_client, seeded_session
):
    case_id = _seeded_case_id(seeded_session)
    response = api_client.get(
        f"/api/v1/research-cases/{case_id}/compare",
        params={"base": BASE, "compare": COMPARE},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["theses"]) == 3
    for thesis in body["theses"]:
        # Before any snapshot existed: no conclusion; after seeding: full set.
        assert thesis["conclusion_before"] is None
        assert thesis["conclusion_after"] in {
            "supported",
            "contradicted",
            "insufficient_evidence",
        }
        assert thesis["conclusion_changed"] is True
        assert len(thesis["added_links"]) == 5
        assert thesis["removed_links"] == []


def test_compare_same_window_has_no_changes(api_client, seeded_session):
    case_id = _seeded_case_id(seeded_session)
    response = api_client.get(
        f"/api/v1/research-cases/{case_id}/compare",
        params={"base": "2098-01-01T00:00:00Z", "compare": COMPARE},
    )
    assert response.status_code == 200
    for thesis in response.json()["theses"]:
        assert thesis["conclusion_changed"] is False
        assert thesis["added_links"] == []
        assert thesis["removed_links"] == []


def test_compare_rejects_inverted_window(api_client, seeded_session):
    case_id = _seeded_case_id(seeded_session)
    response = api_client.get(
        f"/api/v1/research-cases/{case_id}/compare",
        params={"base": COMPARE, "compare": BASE},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# 穿透: fund-exposure / composition
# ---------------------------------------------------------------------------


def test_case_fund_exposure_ranks_by_theme_weight(api_client, seeded_session):
    case_id = _seeded_case_id(seeded_session)
    response = api_client.get(
        f"/api/v1/research-cases/{case_id}/fund-exposure",
        params={"as_of": "2026-06-30"},
    )
    assert response.status_code == 200, response.text
    funds = response.json()["funds"]

    assert [f["fund_code"] for f in funds] == ["008888", "012345"]
    assert funds[0]["theme_exposure"] == 0.147  # 0.082 + 0.065
    assert funds[1]["theme_exposure"] == 0.111  # 0.071 + 0.040 (stale excluded)

    cambricon = funds[0]["positions"][0]
    assert cambricon["stock_code"] == "688256.SH"
    assert cambricon["pe_ttm"] == 380.5
    assert cambricon["pb"] == 12.3


def test_fund_composition_excludes_stale_disclosure(api_client, seeded_session):
    from sqlalchemy import select

    from app.models.ledger import Fund

    fund_b = seeded_session.scalar(select(Fund).where(Fund.code == "012345"))
    response = api_client.get(
        f"/api/v1/funds/{fund_b.id}/composition",
        params={"as_of": "2026-06-30"},
    )
    assert response.status_code == 200, response.text
    positions = response.json()["positions"]

    fii = next(p for p in positions if p["stock_code"] == "601138.SH")
    # Latest 2026Q1 report wins; the stale 2025H1 (0.055) never resurfaces.
    assert fii["weight"] == 0.071
    assert fii["report_period"] == "2026-03-31"
    assert fii["pe_ttm"] == 25.6
    assert any(h["role"] == "AI服务器代工方" for h in fii["theme_hits"])


def test_fund_composition_respects_published_visibility(
    api_client, seeded_session
):
    from sqlalchemy import select

    from app.models.ledger import Fund

    fund_a = seeded_session.scalar(select(Fund).where(Fund.code == "008888"))
    response = api_client.get(
        f"/api/v1/funds/{fund_a.id}/composition",
        params={"as_of": "2026-04-01"},  # before the 2026-04-22 publications
    )
    assert response.status_code == 200
    assert response.json()["positions"] == []


def test_penetration_404s(api_client, seeded_session):
    missing = "00000000-0000-0000-0000-000000000000"
    r1 = api_client.get(f"/api/v1/research-cases/{missing}/fund-exposure")
    r2 = api_client.get(f"/api/v1/funds/{missing}/composition")
    assert r1.status_code == 404
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# 点时数据: metrics catalog / series
# ---------------------------------------------------------------------------


def test_metric_catalog_lists_latest_per_stock_metric(api_client, seeded_session):
    response = api_client.get("/api/v1/metrics/catalog")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 5  # 2 cambricon + 2 fii + 1 hynix

    pe = next(
        e
        for e in entries
        if e["stock_code"] == "688256.SH" and e["metric_name"] == "PE_TTM"
    )
    assert pe["latest_value"] == 380.5
    assert pe["source"] == "wind"


def test_metric_series_and_404(api_client, seeded_session):
    from sqlalchemy import select

    from app.models.ledger import Stock

    stock = seeded_session.scalar(select(Stock).where(Stock.code == "601138.SH"))
    response = api_client.get(
        "/api/v1/metrics/series",
        params={"stock_id": str(stock.id), "metric_name": "PE_TTM"},
    )
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == 1
    assert points[0]["value"] == 25.6

    missing = api_client.get(
        "/api/v1/metrics/series",
        params={
            "stock_id": "00000000-0000-0000-0000-000000000000",
            "metric_name": "PE_TTM",
        },
    )
    assert missing.status_code == 404
