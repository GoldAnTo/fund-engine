"""Workbench read-API tests (SQLite, no Neo4j required).

Verifies the focused workbench response contract: provisional AI-assessment
visibility, typed graph edges assembled from the ledger, evidence-drawer
verbatim drill-down, and dated fund-holding disclosure rows.  No
recommendation field is exposed.
"""


def test_workbench_marks_ai_assessment_as_unreviewed(api_client, workbench_case):
    response = api_client.get(
        f"/api/research-cases/{workbench_case.case.id}/workbench"
    )
    assert response.status_code == 200
    payload = response.json()

    assessment = payload["assessment"]
    assert assessment["provisional"] is True
    assert assessment["conclusion"] == "supported"
    assert assessment["rationale"] == "evidence supports"

    assert payload["case"]["industry_topic"] == "ai_compute"
    assert payload["focus_thesis"]["statement"] == "GPU demand will grow"

    assert payload["graph"]["edges"][0]["kind"] in {
        "evidence",
        "causal",
        "theme_role",
        "holding",
    }
    # No recommendation field is ever exposed.
    assert "recommendation" not in payload


def test_workbench_evidence_drawer_contains_verbatim_text(
    api_client, workbench_case
):
    response = api_client.get(
        f"/api/research-cases/{workbench_case.case.id}/workbench"
    )
    assert response.status_code == 200
    payload = response.json()

    records = payload["evidence_drawer_records"]
    assert len(records) >= 1
    record = records[0]
    assert record["verbatim_text"] == "财报第 32 页，表格第 4 行：CapEx 同比增长 40%"
    assert record["reason"] == "orders rose"
    assert record["role"] == "supports"
    assert record["scope"] == {"segment": "DC"}
    assert record["period"] == "2026-03-31"
    assert record["review_state"] == "machine_generated"


def test_workbench_includes_fund_holding_disclosure(api_client, workbench_case):
    response = api_client.get(
        f"/api/research-cases/{workbench_case.case.id}/workbench"
    )
    assert response.status_code == 200
    payload = response.json()

    rows = payload["fund_holding_disclosures"]
    assert len(rows) >= 1
    row = rows[0]
    assert row["weight"] == "0.082"
    assert row["report_period"] == "2026-03-31"
    assert row["published_at"].startswith("2026-04-22")
    assert row["fund_code"] == "001001"
    assert row["stock_code"] == "600519.SH"

    # Stock valuation snapshots are also exposed alongside holdings.
    snaps = payload["stock_valuation_snapshots"]
    assert len(snaps) >= 1
    assert snaps[0]["metric_name"] == "PE_TTM"
    assert snaps[0]["metric_value"] == "45.2"
