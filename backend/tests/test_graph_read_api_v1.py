"""Connected relationship graph v1 read contract."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ledger import (
    CausalEdge,
    CausalStep,
    Company,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    Stock,
    ThemeRole,
    Thesis,
    ValuationSnapshot,
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


def test_graph_loads_real_causal_edges_and_connects_to_thesis(
    api_client, session
):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="th", created_by="u", created_at=past
    )
    session.add(thesis)
    session.flush()
    step1 = CausalStep(
        thesis_id=thesis.id, description="s1", sequence=1, created_at=past
    )
    step2 = CausalStep(
        thesis_id=thesis.id, description="s2", sequence=2, created_at=past
    )
    session.add_all([step1, step2])
    session.flush()
    edge = CausalEdge(
        source_step_id=step1.id,
        target_step_id=step2.id,
        rationale="because",
        creator_type="ai",
        review_state="reviewed",
        created_at=past,
    )
    session.add(edge)
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"thesis_id": str(thesis.id), "cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    causal_edges = [e for e in payload["edges"] if e["semantic_kind"] == "causal"]
    assert len(causal_edges) == 1
    assert causal_edges[0]["id"] == str(edge.id)
    assert causal_edges[0]["source"] == str(step1.id)
    assert causal_edges[0]["target"] == str(step2.id)
    assert causal_edges[0]["properties"]["rationale"] == "because"

    # thesis->step containment connects the chain to the main graph
    contains_step = [
        e for e in payload["edges"] if e["semantic_kind"] == "contains_step"
    ]
    assert len(contains_step) == 2
    step_ids = {str(step1.id), str(step2.id)}
    assert any(
        set(p["node_ids"]) & step_ids for p in payload["paths"]
    )


def test_graph_does_not_fabricate_causal_edge_without_ledger_edge(
    api_client, session
):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="th", created_by="u", created_at=past
    )
    session.add(thesis)
    session.flush()
    session.add(
        CausalStep(thesis_id=thesis.id, description="s1", sequence=1, created_at=past)
    )
    session.add(
        CausalStep(thesis_id=thesis.id, description="s2", sequence=2, created_at=past)
    )
    session.flush()
    # No CausalEdge rows in the ledger.

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"thesis_id": str(thesis.id), "cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert not any(e["semantic_kind"] == "causal" for e in payload["edges"])
    # steps are still attached via contains_step
    assert any(e["semantic_kind"] == "contains_step" for e in payload["edges"])


def test_graph_hides_machine_generated_causal_edge_by_default(
    api_client, session
):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="th", created_by="u", created_at=past
    )
    session.add(thesis)
    session.flush()
    step1 = CausalStep(
        thesis_id=thesis.id, description="s1", sequence=1, created_at=past
    )
    step2 = CausalStep(
        thesis_id=thesis.id, description="s2", sequence=2, created_at=past
    )
    session.add_all([step1, step2])
    session.flush()
    session.add(
        CausalEdge(
            source_step_id=step1.id,
            target_step_id=step2.id,
            rationale="ai guess",
            creator_type="ai",
            review_state="machine_generated",
            created_at=past,
        )
    )
    session.flush()

    base = {"thesis_id": str(thesis.id), "cutoff": cutoff.isoformat()}
    default = api_client.get(f"/api/v1/research-cases/{case.id}/graph", params=base)
    assert default.status_code == 200
    assert not any(
        e["semantic_kind"] == "causal" for e in default.json()["edges"]
    )

    research = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={**base, "research_mode": "true"},
    )
    assert research.status_code == 200
    assert any(
        e["semantic_kind"] == "causal" for e in research.json()["edges"]
    )


def test_graph_excludes_theme_role_created_after_cutoff(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    company = Company(code="C", name="Co", type="listed", created_at=past)
    session.add(company)
    session.flush()
    # theme role CREATED after cutoff but applicable_from backfilled to the past
    session.add(
        ThemeRole(
            company_id=company.id,
            research_case_id=case.id,
            role="beneficiary",
            scope={"s": "d"},
            applicable_from=date(2025, 1, 1),
            applicable_to=None,
            source_statement_id=None,
            created_at=future,
        )
    )
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert not any(e["semantic_kind"] == "theme_role" for e in payload["edges"])
    assert not any(e["semantic_kind"] == "company_stock" for e in payload["edges"])


def test_graph_excludes_valuation_created_after_cutoff(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
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
    # valuation CREATED after cutoff but as_of_date backfilled to the past
    session.add(
        ValuationSnapshot(
            stock_id=stock.id,
            as_of_date=date(2025, 3, 31),
            metric_name="PE_TTM",
            metric_value=Decimal("30"),
            source="wind",
            definition="pe",
            created_at=future,
        )
    )
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    assert not any(
        e["semantic_kind"] == "valuation" for e in response.json()["edges"]
    )


def test_graph_dedups_edges_by_id(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    company = Company(code="C", name="Co", type="listed", created_at=past)
    session.add(company)
    session.flush()
    # two theme roles for the same company on the same case
    for role in ("beneficiary", "supplier"):
        session.add(
            ThemeRole(
                company_id=company.id,
                research_case_id=case.id,
                role=role,
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

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/graph",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    company_stock = [
        e for e in payload["edges"] if e["semantic_kind"] == "company_stock"
    ]
    assert len(company_stock) == 1
    theme_role_edges = [
        e for e in payload["edges"] if e["semantic_kind"] == "theme_role"
    ]
    assert len(theme_role_edges) == 2
    # no edge id appears more than once
    edge_ids = [e["id"] for e in payload["edges"]]
    assert len(edge_ids) == len(set(edge_ids))
