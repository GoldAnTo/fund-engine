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
from app.models.ledger import DocumentVersion, SourceSpan
from app.queries.basis import HistoricalBasis
from app.queries.effective_state import (
    effective_review_state,
    latest_review_outcomes,
)
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
            visible = list(
                self._research.visible_links(thesis_id=thesis.id, cutoff=basis.cutoff)
            )
            # Append-only ledger: human review outcomes live in
            # evidence_reviews, so visibility/edge state use the effective state.
            outcomes = latest_review_outcomes(
                self._session, [link.id for link in visible], cutoff=basis.cutoff
            )
            # 原文层（P2 缺陷 9 修复）：把 statement 链向上游的 span 与
            # document。多个 statement 共享同一 span / document 时避免重复
            # 添加，缓存以 (span_id, document_id) 维度判重。
            span_cache: dict[uuid.UUID, SourceSpan] = {}
            doc_cache: dict[uuid.UUID, DocumentVersion] = {}

            def _span(span_id: uuid.UUID) -> SourceSpan | None:
                if span_id in span_cache:
                    return span_cache[span_id]
                span = self._session.get(SourceSpan, span_id)
                span_cache[span_id] = span  # 缓存 None 也算命中
                return span

            def _document(doc_id: uuid.UUID) -> DocumentVersion | None:
                if doc_id in doc_cache:
                    return doc_cache[doc_id]
                doc = self._session.get(DocumentVersion, doc_id)
                doc_cache[doc_id] = doc
                return doc

            def _span_label(span: SourceSpan) -> str:
                locator = span.locator or {}
                parts: list[str] = []
                if "page" in locator:
                    parts.append(f"p.{locator['page']}")
                if "paragraph" in locator:
                    parts.append(f"¶{locator['paragraph']}")
                if "table_row" in locator:
                    parts.append(f"row{locator['table_row']}")
                if "char_start" in locator and "char_end" in locator:
                    parts.append(
                        f"chars {locator['char_start']}-{locator['char_end']}"
                    )
                prefix = " · ".join(parts) if parts else "span"
                text = (span.verbatim_text or "").strip()
                if not text:
                    return prefix
                snippet = text[:30].replace("\n", " ")
                if len(text) > 30:
                    snippet += "…"
                return f"{prefix} · {snippet}"

            def _document_label(doc: DocumentVersion) -> str:
                # DocumentVersion 没有显式 title 字段；用 source_url 的
                # 最后一段作为人读标签，并补 published_at / parser_version
                # 提示文档冻结口径（design 7 不可变账本标识）。
                url = (doc.source_url or "").strip()
                tail = url.rsplit("/", 1)[-1] if url else ""
                if not tail:
                    tail = url or "document"
                if len(tail) > 60:
                    tail = tail[:57] + "…"
                bits: list[str] = [tail]
                if doc.published_at is not None:
                    bits.append(doc.published_at.strftime("%Y-%m-%d"))
                if doc.parser_version:
                    bits.append(doc.parser_version)
                return " · ".join(bits)

            for link in visible:
                state = effective_review_state(
                    link.review_state, outcomes.get(link.id)
                )
                if state not in allowed_states:
                    continue
                statement = self._research.get_statement(link.source_statement_id)
                if statement is None:
                    continue
                # 携带来源文档 id，前端"跳转原文"据此定位到资料库对应文档
                span = _span(statement.source_span_id)
                document_version = (
                    _document(span.document_version_id) if span is not None else None
                )
                # 链路节点都按 cutoff 可见性过滤：document 必须在 cutoff 之前
                # 已 available（available_at <= cutoff），否则其下的 span /
                # statement 都不应暴露（防后见之明）。
                if (
                    document_version is not None
                    and _to_aware(document_version.available_at) > basis.cutoff
                ):
                    continue
                if document_version is not None:
                    add_node(
                        document_version.id,
                        "document",
                        _document_label(document_version),
                        source_url=document_version.source_url,
                        parser_version=document_version.parser_version,
                        published_at=_iso(document_version.published_at),
                        available_at=_iso(document_version.available_at),
                    )
                if span is not None:
                    add_node(
                        span.id,
                        "span",
                        _span_label(span),
                        locator=span.locator,
                    )
                add_node(
                    statement.id,
                    "statement",
                    statement.normalized_text,
                    statement_kind=statement.kind,
                    document_id=(
                        str(span.document_version_id) if span is not None else None
                    ),
                    span_id=str(span.id) if span is not None else None,
                )
                # DocumentVersion -> SourceSpan（容器关系，事实是 span 由
                # document 包含并以 locator 定位）
                if document_version is not None and span is not None:
                    add_edge(
                        f"contains:{document_version.id}:{span.id}",
                        "contains",
                        document_version.id,
                        span.id,
                    )
                # SourceSpan -> SourceStatement（衍生关系：statement 是从
                # span 文本中抽取出来的原子陈述）
                if span is not None:
                    add_edge(
                        f"derived:{span.id}:{statement.id}",
                        "derived",
                        span.id,
                        statement.id,
                    )
                add_edge(
                    link.id,
                    "evidence",
                    thesis.id,
                    statement.id,
                    review_state=state,
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
