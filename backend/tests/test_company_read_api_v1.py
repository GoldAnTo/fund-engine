"""Company-centric v1 read contract (公司研究 CompanyDossier)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ledger import Company, ThemeRole


def test_company_list_returns_rows_with_counts(api_client, workbench_case):
    response = api_client.get("/api/v1/companies")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    item = next(i for i in payload["items"] if i["id"] == str(workbench_case.company.id))
    assert item["code"] == "600519"
    assert item["stock_count"] == 1
    assert item["theme_role_count"] == 1
    assert item["latest_report_period"] == "2026-03-31"


def test_company_list_filters_by_query(api_client, workbench_case):
    hit = api_client.get("/api/v1/companies", params={"q": "Mapped"})
    assert {i["id"] for i in hit.json()["items"]} == {str(workbench_case.company.id)}

    miss = api_client.get("/api/v1/companies", params={"q": "不存在的公司"})
    assert miss.json()["items"] == []


def test_company_list_paginates_with_cursor(api_client, instrument_repository):
    for i in range(3):
        instrument_repository.add_company(
            code=f"C{i:04d}", name=f"Corp {i}", type="listed"
        )
    page1 = api_client.get("/api/v1/companies", params={"limit": 2})
    p1 = page1.json()
    assert len(p1["items"]) == 2
    assert p1["page"]["has_more"] is True

    page2 = api_client.get(
        "/api/v1/companies",
        params={"limit": 2, "cursor": p1["page"]["next_cursor"]},
    )
    p2 = page2.json()
    assert len(p2["items"]) == 1
    assert p2["page"]["has_more"] is False

    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids1.isdisjoint(ids2)


def test_company_list_malformed_cursor_is_422(api_client, workbench_case):
    response = api_client.get("/api/v1/companies", params={"cursor": "!!!bad"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_company_dossier_sections_complete(api_client, workbench_case):
    response = api_client.get(f"/api/v1/companies/{workbench_case.company.id}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["company"]["code"] == "600519"
    assert payload["basis"]["cutoff"]
    assert [s["code"] for s in payload["stocks"]] == ["600519.SH"]

    roles = payload["theme_roles"]
    assert len(roles) == 1
    assert roles[0]["role"] == "beneficiary"
    assert roles[0]["case_id"] == str(workbench_case.case.id)
    assert roles[0]["case_title"] == workbench_case.case.title

    theses = payload["related_theses"]
    assert len(theses) == 1
    assert theses[0]["thesis_id"] == str(workbench_case.thesis.id)
    assert theses[0]["case_title"] == workbench_case.case.title

    valuations = payload["valuations"]
    assert len(valuations) == 1
    assert valuations[0]["metric_name"] == "PE_TTM"
    assert valuations[0]["metric_value"] == 45.2
    assert valuations[0]["definition"] == "总市值/近四月归母净利润"

    holders = payload["fund_holders"]
    assert len(holders) == 1
    assert holders[0]["fund_code"] == "001001"
    assert holders[0]["report_period"] == "2026-03-31"
    assert holders[0]["published_at"] is not None


def test_company_dossier_keeps_ai_and_human_judgment_separate(
    api_client, workbench_case, research_repository
):
    research_repository.insert_review(
        ai_assessment_id=workbench_case.ai_assessment.id,
        outcome="modified",
        conclusion="contradicted",
        reason="人工修正：估值过高",
        reviewer="tester",
    )
    payload = api_client.get(
        f"/api/v1/companies/{workbench_case.company.id}"
    ).json()
    thesis = payload["related_theses"][0]
    # AI original stays visible and provisional; the human review is separate.
    assert thesis["ai_assessment"]["conclusion"] == "supported"
    assert thesis["ai_assessment"]["provisional"] is True
    assert thesis["review"]["outcome"] == "modified"
    assert thesis["review"]["conclusion"] == "contradicted"


def test_company_dossier_theme_role_statement_backlink(
    api_client, workbench_case, instrument_repository
):
    instrument_repository.add_theme_role(
        company_id=workbench_case.company.id,
        role="算力芯片受益方",
        scope={"segment": "AI compute"},
        research_case_id=workbench_case.case.id,
        applicable_from=date(2026, 1, 1),
        source_statement_id=workbench_case.statement.id,
    )
    payload = api_client.get(
        f"/api/v1/companies/{workbench_case.company.id}"
    ).json()
    backed = next(r for r in payload["theme_roles"] if r["statement_id"] is not None)
    assert backed["statement_text"] == "CapEx 同比增长 40%"
    assert backed["span_id"] is not None
    assert backed["document_version_id"] is not None


def test_company_dossier_expired_role_hidden(api_client, workbench_case, session):
    session.add(
        ThemeRole(
            company_id=workbench_case.company.id,
            research_case_id=workbench_case.case.id,
            role="历史角色",
            scope={},
            applicable_from=date(2020, 1, 1),
            applicable_to=date(2020, 12, 31),
            created_at=datetime.now(UTC),
        )
    )
    session.flush()
    payload = api_client.get(
        f"/api/v1/companies/{workbench_case.company.id}"
    ).json()
    assert {r["role"] for r in payload["theme_roles"]} == {"beneficiary"}


def test_company_dossier_future_valuation_and_disclosure_hidden(
    api_client, workbench_case, instrument_repository
):
    instrument_repository.add_valuation_snapshot(
        stock_id=workbench_case.stock.id,
        as_of_date=date(2099, 1, 1),
        metric_name="PB",
        metric_value=Decimal("9.9"),
        source="wind",
        definition="未来口径",
    )
    instrument_repository.add_holding_disclosure(
        fund_id=workbench_case.fund.id,
        stock_id=workbench_case.stock.id,
        weight=Decimal("0.5"),
        report_period=date(2099, 3, 31),
        published_at=datetime(2099, 4, 22, tzinfo=UTC),
        source="future-report",
    )
    payload = api_client.get(
        f"/api/v1/companies/{workbench_case.company.id}"
    ).json()
    assert {v["metric_name"] for v in payload["valuations"]} == {"PE_TTM"}
    assert len(payload["fund_holders"]) == 1
    assert payload["fund_holders"][0]["report_period"] == "2026-03-31"


def test_company_dossier_created_after_cutoff_is_404(api_client, workbench_case):
    response = api_client.get(
        f"/api/v1/companies/{workbench_case.company.id}",
        params={"cutoff": "2020-01-01T00:00:00Z"},
    )
    assert response.status_code == 404


def test_company_dossier_missing_company_is_404(api_client):
    response = api_client.get(
        "/api/v1/companies/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


def test_company_dossier_empty_company_returns_empty_sections(
    api_client, session
):
    company = Company(
        code="EMPTY1", name="Empty Corp", type="listed",
        created_at=datetime.now(UTC),
    )
    session.add(company)
    session.flush()

    response = api_client.get(f"/api/v1/companies/{company.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stocks"] == []
    assert payload["theme_roles"] == []
    assert payload["related_theses"] == []
    assert payload["valuations"] == []
    assert payload["fund_holders"] == []
