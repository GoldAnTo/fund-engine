"""Connected relationship graph v1 read contract."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ledger import (
    Company,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    Stock,
    ThemeRole,
)


def edge_pairs(payload):
    return {
        (edge["semantic_kind"], edge["source"], edge["target"])
        for edge in payload["edges"]
    }


def test_graph_is_connected_from_evidence_to_fund(api_client, workbench_case):
    # research_mode=true so machine-generated evidence is visible (design 9.3);
    # future cutoff so the now-timestamped fixture data is visible.
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/graph",
        params={
            "thesis_id": str(workbench_case.thesis.id),
            "cutoff": "2026-12-31T00:00:00Z",
            "research_mode": "true",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "graph/v1"
    pairs = edge_pairs(payload)
    assert (
        "contains_thesis",
        str(workbench_case.case.id),
        str(workbench_case.thesis.id),
    ) in pairs
    assert any(edge["semantic_kind"] == "company_stock" for edge in payload["edges"])
    assert any(edge["semantic_kind"] == "holding" for edge in payload["edges"])
    assert any(edge["semantic_kind"] == "evidence" for edge in payload["edges"])
    assert payload["paths"]
    # every edge endpoint must reference an assembled node (no dangling edges)
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


def test_graph_excludes_future_disclosure(api_client, session):
    # Case created in the past; a holding disclosure published in the future
    # must not appear at the cutoff (design 10: same basis for every entity).
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)

    case = ResearchCase(
        title="c", industry_topic="ai_compute", created_by="t", created_at=past
    )
    session.add(case)
    session.flush()
    company = Company(code="C", name="Co", type="listed", created_at=past)
    session.add(company)
    session.flush()
    session.add(
        ThemeRole(
            company_id=company.id,
            research_case_id=case.id,
            role="beneficiary",
            scope={"s": "d"},
            applicable_from=date(2025, 1, 1),
            applicable_to=None,
            source_statement_id=None,
            created_at=past,
        )
    )
    session.flush()
    stock = Stock(
        company_id=company.id, code="C.SH", name="Co", market="SSE", created_at=past
    )
    session.add(stock)
    session.flush()
    fund = Fund(
        code="F",
        name="Fund",
        fund_type="equity",
        management_company_id=None,
        scale=None,
        establish_date=None,
        created_at=past,
    )
    session.add(fund)
    session.flush()
    session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=Decimal("0.1"),
            report_period=date(2026, 3, 31),
            published_at=future,
            acquired_at=past,
            source="s",
            created_at=past,
        )
    )
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    assert not any(
        edge["semantic_kind"] == "holding" for edge in response.json()["edges"]
    )


def test_graph_hides_machine_generated_evidence_by_default(
    api_client, workbench_case
):
    base = {
        "thesis_id": str(workbench_case.thesis.id),
        "cutoff": "2026-12-31T00:00:00Z",
    }
    default = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/graph", params=base
    )
    assert default.status_code == 200
    assert not any(
        edge["semantic_kind"] == "evidence" for edge in default.json()["edges"]
    )

    research = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/graph",
        params={**base, "research_mode": "true"},
    )
    assert research.status_code == 200
    assert any(
        edge["semantic_kind"] == "evidence" for edge in research.json()["edges"]
    )


def test_graph_hides_case_created_after_cutoff(api_client, session):
    future = datetime(2026, 12, 31, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    case = ResearchCase(
        title="future case", industry_topic="t", created_by="u", created_at=future
    )
    session.add(case)
    session.flush()
    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
