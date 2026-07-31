"""Connected relationship graph assembly for the v1 API.

Assembles one connected evidence-to-fund canvas from the append-only ledger.
Historical replay (design 10): the cutoff basis filters every entity, not
just EvidenceLink. AI/human boundary (design 9.2/9.3): machine-generated
evidence edges are hidden by default and only revealed under an explicit
research mode.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.queries.basis import HistoricalBasis
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.common import CursorPage
from app.schemas.v1.graph import (
    GraphEdgeDTO,
    GraphNodeDTO,
    GraphPathDTO,
    GraphResponse,
)

_REVIEWED_STATES = frozenset({"reviewed"})
_RESEARCH_STATES = frozenset({"reviewed", "machine_generated"})


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _to_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class RelationshipGraphQueries:
    """Read-only connected graph assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)
        self._instruments = InstrumentRepository(session)

    def load(
        self,
        *,
        case_id: uuid.UUID,
        thesis_id: uuid.UUID | None,
        basis: HistoricalBasis,
        focus: str | None,
        depth: int,
        limit: int,
        research_mode: bool = False,
    ) -> GraphResponse:
        case = self._research.get_case(case_id, cutoff=basis.cutoff)
        if case is None:
            raise NotFoundError("research case not found")

        nodes: dict[str, GraphNodeDTO] = {}
        edges: list[GraphEdgeDTO] = []
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        seen_edges: set[str] = set()

        def add_node(obj_id, kind: str, label: str, **props) -> str:
            key = str(obj_id)
            if key not in nodes:
                nodes[key] = GraphNodeDTO(
                    id=key, kind=kind, label=label, properties=props
                )
            return key

        def add_edge(edge_id, semantic_kind: str, source, target, **extra) -> None:
            eid = str(edge_id)
            if eid in seen_edges:
                return
            seen_edges.add(eid)
            src, tgt = str(source), str(target)
            edge = GraphEdgeDTO(
                id=eid,
                semantic_kind=semantic_kind,
                source=src,
                target=tgt,
                **extra,
            )
            edges.append(edge)
            adjacency[src].append((edge.id, tgt))
            adjacency[tgt].append((edge.id, src))

        # 1. case + thesis (deterministic contains_thesis edge)
        case_key = add_node(
            case.id, "case", case.title, topic=case.industry_topic
        )
        if thesis_id is not None:
            thesis = self._research.thesis_by_id_for_case(
                case_id, thesis_id, cutoff=basis.cutoff
            )
            if thesis is None:
                raise NotFoundError("thesis not found in research case")
        else:
            thesis = self._research.latest_thesis_for_case(
                case_id, cutoff=basis.cutoff
            )
        if thesis is not None:
            add_node(thesis.id, "thesis", thesis.statement)
            add_edge(
                f"contains_thesis:{case.id}:{thesis.id}",
                "contains_thesis",
                case.id,
                thesis.id,
            )

        # 2-3. evidence links (research_mode-gated) and causal steps
        if thesis is not None:
            allowed_states = (
                _RESEARCH_STATES if research_mode else _REVIEWED_STATES
            )
            for link in self._research.visible_links(
                thesis_id=thesis.id, cutoff=basis.cutoff
            ):
                if link.review_state not in allowed_states:
                    continue
                statement = self._research.get_statement(link.source_statement_id)
                if statement is None:
                    continue
                add_node(
                    statement.id,
                    "statement",
                    statement.normalized_text,
                    statement_kind=statement.kind,
                )
                add_edge(
                    link.id,
                    "evidence",
                    thesis.id,
                    statement.id,
                    review_state=link.review_state,
                    available_at=_iso(link.available_at),
                )

            steps = self._research.causal_steps_for_thesis(
                thesis.id, cutoff=basis.cutoff
            )
            step_ids = [s.id for s in steps]
            for step in steps:
                add_node(step.id, "step", step.description, sequence=step.sequence)
                # Deterministic thesis->step containment attaches the causal
                # chain to the main graph (derived from CausalStep.thesis_id,
                # NOT a fabricated causal relation).
                add_edge(
                    f"contains_step:{thesis.id}:{step.id}",
                    "contains_step",
                    thesis.id,
                    step.id,
                )
            # Real causal edges only, with ledger ID, direction, rationale and
            # review state. Never infer a relation from step sequence.
            for ce in self._research.causal_edges_for_steps(step_ids):
                if _to_aware(ce.created_at) > basis.cutoff:
                    continue
                if ce.review_state not in allowed_states:
                    continue
                add_edge(
                    ce.id,
                    "causal",
                    ce.source_step_id,
                    ce.target_step_id,
                    review_state=ce.review_state,
                    properties={"rationale": ce.rationale},
                )

        # 4-5. theme roles -> company -> stock (company_stock edges)
        cutoff_date = basis.cutoff.date()
        theme_roles = [
            tr
            for tr in self._instruments.theme_roles_for_case(case.id)
            if (tr.applicable_from is None or tr.applicable_from <= cutoff_date)
            and (tr.applicable_to is None or tr.applicable_to >= cutoff_date)
            and _to_aware(tr.created_at) <= basis.cutoff
        ]
        company_ids = {tr.company_id for tr in theme_roles}
        companies = {
            c.id: c for c in self._instruments.companies_by_ids(list(company_ids))
        }
        for tr in theme_roles:
            company = companies.get(tr.company_id)
            if company is None:
                continue
            add_node(company.id, "company", company.name, code=company.code)
            add_edge(
                f"theme_role:{tr.id}",
                "theme_role",
                company.id,
                case.id,
                properties={"role": tr.role},
            )
            for stock in self._instruments.stocks_for_companies([tr.company_id]):
                add_node(
                    stock.id,
                    "stock",
                    stock.name,
                    code=stock.code,
                    market=stock.market,
                )
                add_edge(
                    f"company_stock:{company.id}:{stock.id}",
                    "company_stock",
                    company.id,
                    stock.id,
                )
                # 6. holdings (published_at <= cutoff)
                for disclosure in self._instruments.holding_disclosures_for_stocks(
                    [stock.id]
                ):
                    if (
                        _to_aware(disclosure.published_at) > basis.cutoff
                        or _to_aware(disclosure.acquired_at) > basis.cutoff
                        or _to_aware(disclosure.created_at) > basis.cutoff
                    ):
                        continue
                    fund = self._instruments.fund_by_id(disclosure.fund_id)
                    if fund is None:
                        continue
                    add_node(fund.id, "fund", fund.name, code=fund.code)
                    add_edge(
                        f"holding:{disclosure.id}",
                        "holding",
                        fund.id,
                        stock.id,
                        available_at=_iso(disclosure.published_at),
                        properties={
                            "weight": str(disclosure.weight),
                            "report_period": _iso(disclosure.report_period),
                        },
                    )
                # 7. valuations (as_of_date <= cutoff.date())
                for snap in self._instruments.valuation_snapshots_for_stocks(
                    [stock.id]
                ):
                    if (
                        snap.as_of_date > cutoff_date
                        or _to_aware(snap.created_at) > basis.cutoff
                    ):
                        continue
                    add_node(
                        snap.id,
                        "valuation",
                        snap.metric_name,
                        as_of=_iso(snap.as_of_date),
                    )
                    add_edge(
                        f"valuation:{snap.id}", "valuation", stock.id, snap.id
                    )

        # 8. paths: depth-limited BFS from the focus node (or the case)
        start = focus if (focus and focus in nodes) else case_key
        paths = self._paths(start, adjacency, depth)

        # 9. truncate to node limit
        node_list = list(nodes.values())
        has_more = False
        if len(node_list) > limit:
            has_more = True
            kept = {n.id for n in node_list[:limit]}
            node_list = node_list[:limit]
            edges = [e for e in edges if e.source in kept and e.target in kept]
            paths = [
                p for p in paths if all(nid in kept for nid in p.node_ids)
            ]

        return GraphResponse(
            basis=basis.to_dto(),
            nodes=node_list,
            edges=edges,
            paths=paths,
            page=CursorPage(has_more=has_more),
        )

    @staticmethod
    def _paths(
        start: str,
        adjacency: dict[str, list[tuple[str, str]]],
        depth: int,
    ) -> list[GraphPathDTO]:
        # One shortest path per reachable node; never fabricates edges.
        results: list[GraphPathDTO] = []
        seen: set[str] = {start}
        queue: deque[tuple[str, list[str], list[str]]] = deque(
            [(start, [start], [])]
        )
        while queue:
            node, node_ids, edge_ids = queue.popleft()
            if len(edge_ids) >= depth:
                continue
            for edge_id, neighbor in adjacency.get(node, []):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                new_node_ids = node_ids + [neighbor]
                new_edge_ids = edge_ids + [edge_id]
                results.append(
                    GraphPathDTO(
                        node_ids=new_node_ids,
                        edge_ids=new_edge_ids,
                        label=f"{new_node_ids[0]}->{neighbor}",
                    )
                )
                queue.append((neighbor, new_node_ids, new_edge_ids))
        return results
