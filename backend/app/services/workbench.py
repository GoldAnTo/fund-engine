"""Workbench read-model assembly.

Builds the focused ``WorkbenchResponse`` directly from the append-only ledger
(no Neo4j dependency).  The graph is assembled from ledger rows with typed
edges (``evidence``, ``causal``, ``theme_role``, ``holding``) so the frontend
can render one connected evidence-to-fund canvas.

The response never exposes a recommendation, target price, or buy/sell signal.
The AI/human boundary is made explicit via ``assessment.provisional`` and the
optional ``review`` block.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.ledger import ResearchCase
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _dec(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class WorkbenchService:
    """Reads the ledger and assembles the focused workbench response."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)
        self._instruments = InstrumentRepository(session)

    def load_workbench(
        self,
        *,
        case_id: uuid.UUID,
        cutoff: datetime | None = None,
    ) -> dict | None:
        case = self._session.get(ResearchCase, case_id)
        if case is None:
            return None

        if cutoff is None:
            cutoff = _utcnow()

        thesis = self._research.latest_thesis_for_case(case_id)

        links: list = []
        assessment = None
        review = None
        major_gap: str | None = None

        if thesis is not None:
            links = self._research.visible_links(
                thesis_id=thesis.id, cutoff=cutoff
            )
            assessment = self._research.latest_assessment_for_thesis(
                thesis.id, cutoff=cutoff
            )
            if assessment is not None:
                review = self._research.latest_review_for_assessment(
                    assessment.id
                )
                if assessment.gaps:
                    major_gap = assessment.gaps[0]

        graph = self._build_graph(
            case=case, thesis=thesis, links=links
        )
        evidence_drawer = self._build_evidence_drawer(links)
        valuation_rows, disclosure_rows, fund_nodes, holding_edges = (
            self._build_exposure(case_id=case_id, cutoff=cutoff)
        )
        # Merge fund nodes and holding edges into the graph so fund->stock
        # links render on the same connected canvas.
        for fund_node in fund_nodes:
            if fund_node["id"] not in {n["id"] for n in graph["nodes"]}:
                graph["nodes"].append(fund_node)
        graph["edges"].extend(holding_edges)

        return {
            "case": {
                "id": str(case.id),
                "title": case.title,
                "industry_topic": case.industry_topic,
            },
            "focus_thesis": (
                {
                    "id": str(thesis.id),
                    "statement": thesis.statement,
                }
                if thesis is not None
                else None
            ),
            "assessment": (
                {
                    "id": str(assessment.id),
                    "conclusion": assessment.conclusion,
                    "rationale": assessment.rationale,
                    "gaps": list(assessment.gaps),
                    "provisional": bool(assessment.displayed_as_provisional),
                }
                if assessment is not None
                else None
            ),
            "review": (
                {
                    "outcome": review.outcome,
                    "conclusion": review.conclusion,
                    "reason": review.reason,
                }
                if review is not None
                else None
            ),
            "major_gap": major_gap,
            "graph": graph,
            "evidence_drawer_records": evidence_drawer,
            "stock_valuation_snapshots": valuation_rows,
            "fund_holding_disclosures": disclosure_rows,
        }

    # ------------------------------------------------------------------ graph

    def _build_graph(
        self,
        *,
        case: ResearchCase,
        thesis,
        links: list,
    ) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def add_node(node_id: uuid.UUID, kind: str, label: str, **extra) -> None:
            key = str(node_id)
            if key not in nodes:
                node = {"id": key, "kind": kind, "label": label}
                node.update(extra)
                nodes[key] = node

        add_node(case.id, "case", case.title, industry_topic=case.industry_topic)

        if thesis is not None:
            add_node(thesis.id, "thesis", thesis.statement)

        # evidence edges: thesis --evidence--> source_statement
        for link in links:
            statement = self._research.get_statement(link.source_statement_id)
            if statement is not None:
                add_node(
                    statement.id,
                    "statement",
                    statement.normalized_text,
                    statement_kind=statement.kind,
                )
                edges.append(
                    {
                        "id": str(link.id),
                        "kind": "evidence",
                        "source": str(thesis.id) if thesis else None,
                        "target": str(statement.id),
                        "role": link.role,
                        "reason": link.reason,
                        "review_state": link.review_state,
                    }
                )

        # causal edges: step --causal--> step (within the thesis)
        if thesis is not None:
            steps = self._research.causal_steps_for_thesis(thesis.id)
            for step in steps:
                add_node(step.id, "step", step.description, sequence=step.sequence)
            step_ids = [s.id for s in steps]
            for edge in self._research.causal_edges_for_steps(step_ids):
                edges.append(
                    {
                        "id": str(edge.id),
                        "kind": "causal",
                        "source": str(edge.source_step_id),
                        "target": str(edge.target_step_id),
                        "rationale": edge.rationale,
                        "review_state": edge.review_state,
                    }
                )

        # theme_role edges: company --theme_role--> case
        theme_roles = self._instruments.theme_roles_for_case(case.id)
        company_ids = {tr.company_id for tr in theme_roles}
        companies = {
            c.id: c for c in self._instruments.companies_by_ids(list(company_ids))
        }
        for tr in theme_roles:
            company = companies.get(tr.company_id)
            if company is not None:
                add_node(
                    company.id,
                    "company",
                    company.name,
                    code=company.code,
                )
                edges.append(
                    {
                        "id": str(tr.id),
                        "kind": "theme_role",
                        "source": str(company.id),
                        "target": str(case.id),
                        "role": tr.role,
                        "scope": tr.scope,
                    }
                )
            # expose theme stocks as nodes too
            stocks = self._instruments.stocks_for_companies([tr.company_id])
            for stock in stocks:
                add_node(
                    stock.id,
                    "stock",
                    stock.name,
                    code=stock.code,
                    market=stock.market,
                )

        return {"nodes": list(nodes.values()), "edges": edges}

    # ------------------------------------------------------------------ drawer

    def _build_evidence_drawer(self, links: list) -> list[dict]:
        records: list[dict] = []
        for link in links:
            statement = self._research.get_statement(link.source_statement_id)
            span = self._research.span_for_statement(link.source_statement_id)
            records.append(
                {
                    "link_id": str(link.id),
                    "statement_id": str(link.source_statement_id),
                    "statement_text": (
                        statement.normalized_text if statement is not None else None
                    ),
                    "statement_kind": (
                        statement.kind if statement is not None else None
                    ),
                    "span_id": str(span.id) if span is not None else None,
                    "verbatim_text": span.verbatim_text if span is not None else None,
                    "locator": span.locator if span is not None else None,
                    "reason": link.reason,
                    "role": link.role,
                    "scope": link.scope,
                    "period": _iso(statement.observed_period) if statement else None,
                    "review_state": link.review_state,
                }
            )
        return records

    # ------------------------------------------------------------------ exposure

    def _build_exposure(
        self,
        *,
        case_id: uuid.UUID,
        cutoff: datetime,
    ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        """Return (valuation_rows, disclosure_rows, fund_nodes, holding_edges)."""
        theme_roles = self._instruments.theme_roles_for_case(case_id)
        company_ids = [tr.company_id for tr in theme_roles]
        stocks = self._instruments.stocks_for_companies(company_ids)
        stock_ids = [s.id for s in stocks]
        stocks_by_id = {s.id: s for s in stocks}

        # valuation snapshots for the theme stocks
        valuation_rows: list[dict] = []
        for snap in self._instruments.valuation_snapshots_for_stocks(stock_ids):
            stock = stocks_by_id.get(snap.stock_id)
            valuation_rows.append(
                {
                    "stock_id": str(snap.stock_id),
                    "stock_code": stock.code if stock else None,
                    "stock_name": stock.name if stock else None,
                    "as_of_date": _iso(snap.as_of_date),
                    "metric_name": snap.metric_name,
                    "metric_value": _dec(snap.metric_value),
                    "source": snap.source,
                    "definition": snap.definition,
                }
            )

        # holding disclosures for the theme stocks (respecting cutoff)
        cutoff_dt = cutoff
        fund_nodes: list[dict] = []
        seen_fund_ids: set[uuid.UUID] = set()
        disclosure_rows: list[dict] = []
        holding_edges: list[dict] = []
        for disclosure in self._instruments.holding_disclosures_for_stocks(stock_ids):
            published = disclosure.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            if published > cutoff_dt:
                continue
            stock = stocks_by_id.get(disclosure.stock_id)
            fund = self._instruments.fund_by_id(disclosure.fund_id)
            if fund is not None and fund.id not in seen_fund_ids:
                seen_fund_ids.add(fund.id)
                fund_nodes.append(
                    {
                        "id": str(fund.id),
                        "kind": "fund",
                        "label": fund.name,
                        "code": fund.code,
                    }
                )
            disclosure_rows.append(
                {
                    "disclosure_id": str(disclosure.id),
                    "fund_id": str(disclosure.fund_id),
                    "fund_code": fund.code if fund else None,
                    "fund_name": fund.name if fund else None,
                    "stock_id": str(disclosure.stock_id),
                    "stock_code": stock.code if stock else None,
                    "stock_name": stock.name if stock else None,
                    "weight": _dec(disclosure.weight),
                    "report_period": _iso(disclosure.report_period),
                    "published_at": _iso(disclosure.published_at),
                    "source": disclosure.source,
                }
            )
            holding_edges.append(
                {
                    "id": str(disclosure.id),
                    "kind": "holding",
                    "source": str(disclosure.fund_id),
                    "target": str(disclosure.stock_id),
                    "weight": _dec(disclosure.weight),
                    "report_period": _iso(disclosure.report_period),
                }
            )

        return valuation_rows, disclosure_rows, fund_nodes, holding_edges
