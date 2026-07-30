"""Idempotent Neo4j graph projection rebuilt from the append-only ledger.

The projection is a *derived* read model: it is fully rebuildable from the
ledger and carries no information that is not present there.  All writes use
``MERGE`` keyed by the ledger UUID, so re-running ``rebuild_all`` is safe.

Only this application's labelled nodes (tagged ``EvidenceLedger``) are ever
touched: ``clear_projection`` deletes ``(:EvidenceLedger)`` nodes and their
relationships, leaving any unrelated Neo4j data intact.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository

# Every node written by this application carries this label, so that
# clear_projection can target only our data and never delete unrelated nodes.
APP_LABEL = "EvidenceLedger"

# Fixed label whitelist; labels are never user-supplied, guarding against
# accidental Cypher injection through string interpolation.
_NODE_LABELS = frozenset(
    {
        "ResearchCase",
        "Thesis",
        "CausalStep",
        "CausalEdge",
        "SourceStatement",
        "SourceSpan",
        "DocumentVersion",
        "EvidenceLink",
        "EvidenceSnapshot",
        "AIAssessment",
        "ReviewDecision",
        "Company",
        "Stock",
        "FundCompany",
        "Fund",
        "ValuationSnapshot",
        "HoldingDisclosure",
        "ThemeRole",
    }
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dec(value: Any) -> str | None:
    return str(value) if value is not None else None


class ProjectionService:
    """Rebuilds the Neo4j projection from ledger rows using MERGE."""

    def __init__(self, driver: Any, session: Session) -> None:
        self._driver = driver
        self._session = session
        self._research = ResearchRepository(session)
        self._instruments = InstrumentRepository(session)

    @classmethod
    def from_env(cls, session: Session | None = None) -> "ProjectionService":
        """Build a projector from NEO4J_URL / NEO4J_USER / NEO4J_PASSWORD.

        Falls back to a local default URI and the docker-compose password.
        """
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URL", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "evidence-graph")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        if session is None:
            from app.db import SessionLocal

            session = SessionLocal()
        return cls(driver, session)

    # ------------------------------------------------------------------ maintenance

    def clear_projection(self) -> None:
        """Delete only this application's labelled nodes and their edges."""
        with self._driver.session() as neo:
            neo.run(f"MATCH (n:{APP_LABEL}) DETACH DELETE n")

    def node_count(self, label: str) -> int:
        """Count nodes carrying *label* (e.g. ``EvidenceLink``)."""
        if label not in _NODE_LABELS and label != APP_LABEL:
            raise ValueError(f"unknown node label: {label}")
        with self._driver.session() as neo:
            record = neo.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()
            return record["c"] if record is not None else 0

    def rebuild_all(self) -> None:
        """Rebuild the projection from the ledger only.

        Clears the application projection first, then MERGES every entity as a
        node (keyed by ledger UUID) and the typed relationships between them.
        """
        self.clear_projection()
        self._project_research_cases()
        self._project_theses()
        self._project_documents_and_spans()
        self._project_statements()
        self._project_evidence_links()
        self._project_causal()
        self._project_snapshots()
        self._project_assessments()
        self._project_reviews()
        self._project_companies()
        self._project_stocks()
        self._project_fund_companies()
        self._project_funds()
        self._project_valuation_snapshots()
        self._project_holding_disclosures()
        self._project_theme_roles()
        self._project_relationships()

    # ------------------------------------------------------------------ node writers

    def _merge_node(self, label: str, node_id: uuid.UUID, **props: Any) -> None:
        if label not in _NODE_LABELS:
            raise ValueError(f"unknown node label: {label}")
        cypher = (
            f"MERGE (n:{APP_LABEL}:{label} {{uuid: $uuid}}) "
            "SET n += $props"
        )
        with self._driver.session() as neo:
            neo.run(cypher, uuid=str(node_id), props=props)

    def _project_research_cases(self) -> None:
        for case in self._research.all_cases():
            self._merge_node(
                "ResearchCase",
                case.id,
                title=case.title,
                industry_topic=case.industry_topic,
            )

    def _project_theses(self) -> None:
        for thesis in self._research.all_theses():
            self._merge_node(
                "Thesis",
                thesis.id,
                statement=thesis.statement,
                research_case_id=str(thesis.research_case_id),
            )

    def _project_documents_and_spans(self) -> None:
        for span in self._research.all_spans():
            self._merge_node(
                "SourceSpan",
                span.id,
                verbatim_text=span.verbatim_text,
                document_version_id=str(span.document_version_id),
            )
        for version in self._research.all_document_versions():
            self._merge_node(
                "DocumentVersion",
                version.id,
                source_url=version.source_url,
                content_sha256=version.content_sha256,
            )

    def _project_statements(self) -> None:
        for statement in self._research.all_statements():
            self._merge_node(
                "SourceStatement",
                statement.id,
                normalized_text=statement.normalized_text,
                kind=statement.kind,
                observed_period=_iso(statement.observed_period),
                source_span_id=str(statement.source_span_id),
            )

    def _project_evidence_links(self) -> None:
        for link in self._research.all_evidence_links():
            self._merge_node(
                "EvidenceLink",
                link.id,
                thesis_id=str(link.thesis_id),
                source_statement_id=str(link.source_statement_id),
                role=link.role,
                reason=link.reason,
                review_state=link.review_state,
                available_at=_iso(link.available_at),
            )

    def _project_causal(self) -> None:
        for step in self._research.all_causal_steps():
            self._merge_node(
                "CausalStep",
                step.id,
                description=step.description,
                sequence=step.sequence,
                thesis_id=str(step.thesis_id),
            )
        for edge in self._research.all_causal_edges():
            self._merge_node(
                "CausalEdge",
                edge.id,
                source_step_id=str(edge.source_step_id),
                target_step_id=str(edge.target_step_id),
                rationale=edge.rationale,
                review_state=edge.review_state,
            )

    def _project_snapshots(self) -> None:
        for snapshot in self._research.all_snapshots():
            self._merge_node(
                "EvidenceSnapshot",
                snapshot.id,
                thesis_id=str(snapshot.thesis_id),
                cutoff=_iso(snapshot.cutoff),
                evidence_link_ids=[str(x) for x in snapshot.evidence_link_ids],
            )

    def _project_assessments(self) -> None:
        for assessment in self._research.all_ai_assessments():
            self._merge_node(
                "AIAssessment",
                assessment.id,
                snapshot_id=str(assessment.snapshot_id),
                conclusion=assessment.conclusion,
                rationale=assessment.rationale,
                gaps=list(assessment.gaps),
                provisional=bool(assessment.displayed_as_provisional),
            )

    def _project_reviews(self) -> None:
        for review in self._research.all_reviews():
            self._merge_node(
                "ReviewDecision",
                review.id,
                ai_assessment_id=str(review.ai_assessment_id),
                outcome=review.outcome,
                conclusion=review.conclusion,
                reason=review.reason,
            )

    def _project_companies(self) -> None:
        for company in self._instruments.all_companies():
            self._merge_node(
                "Company",
                company.id,
                code=company.code,
                name=company.name,
                type=company.type,
            )

    def _project_stocks(self) -> None:
        for stock in self._instruments.all_stocks():
            self._merge_node(
                "Stock",
                stock.id,
                code=stock.code,
                name=stock.name,
                market=stock.market,
                company_id=str(stock.company_id),
            )

    def _project_fund_companies(self) -> None:
        for fc in self._instruments.all_fund_companies():
            self._merge_node(
                "FundCompany",
                fc.id,
                code=fc.code,
                name=fc.name,
            )

    def _project_funds(self) -> None:
        for fund in self._instruments.all_funds():
            self._merge_node(
                "Fund",
                fund.id,
                code=fund.code,
                name=fund.name,
                fund_type=fund.fund_type,
                scale=_dec(fund.scale),
                management_company_id=str(fund.management_company_id)
                if fund.management_company_id
                else None,
            )

    def _project_valuation_snapshots(self) -> None:
        for snap in self._instruments.all_valuation_snapshots():
            self._merge_node(
                "ValuationSnapshot",
                snap.id,
                stock_id=str(snap.stock_id),
                as_of_date=_iso(snap.as_of_date),
                metric_name=snap.metric_name,
                metric_value=_dec(snap.metric_value),
                source=snap.source,
            )

    def _project_holding_disclosures(self) -> None:
        for disclosure in self._instruments.all_holding_disclosures():
            self._merge_node(
                "HoldingDisclosure",
                disclosure.id,
                fund_id=str(disclosure.fund_id),
                stock_id=str(disclosure.stock_id),
                weight=_dec(disclosure.weight),
                report_period=_iso(disclosure.report_period),
                published_at=_iso(disclosure.published_at),
            )

    def _project_theme_roles(self) -> None:
        for role in self._instruments.all_theme_roles():
            self._merge_node(
                "ThemeRole",
                role.id,
                company_id=str(role.company_id),
                research_case_id=str(role.research_case_id)
                if role.research_case_id
                else None,
                role=role.role,
            )

    # ------------------------------------------------------------------ relationships

    def _merge_relationship(
        self,
        rel_type: str,
        source_label: str,
        source_id: uuid.UUID,
        target_label: str,
        target_id: uuid.UUID,
        key_prop: str,
        **props: Any,
    ) -> None:
        cypher = (
            f"MATCH (s:{APP_LABEL}:{source_label} {{uuid: $source_uuid}}), "
            f"(t:{APP_LABEL}:{target_label} {{uuid: $target_uuid}}) "
            f"MERGE (s)-[r:{rel_type} {{{key_prop}: $key_value}}]->(t) "
            "SET r += $props"
        )
        with self._driver.session() as neo:
            neo.run(
                cypher,
                source_uuid=str(source_id),
                target_uuid=str(target_id),
                key_value=str(props.get(key_prop) or ""),
                props=props,
            )

    def _project_relationships(self) -> None:
        # evidence: Thesis -[:EVIDENCE]-> SourceStatement (one per EvidenceLink)
        for link in self._research.all_evidence_links():
            statement = self._research.get_statement(link.source_statement_id)
            if statement is None:
                continue
            self._merge_relationship(
                "EVIDENCE",
                "Thesis",
                link.thesis_id,
                "SourceStatement",
                statement.id,
                "link_uuid",
                link_uuid=str(link.id),
                role=link.role,
                reason=link.reason,
                review_state=link.review_state,
            )

        # causal: CausalStep -[:CAUSAL]-> CausalStep
        step_by_id = {s.id: s for s in self._research.all_causal_steps()}
        for edge in self._research.all_causal_edges():
            if edge.source_step_id not in step_by_id:
                continue
            self._merge_relationship(
                "CAUSAL",
                "CausalStep",
                edge.source_step_id,
                "CausalStep",
                edge.target_step_id,
                "edge_uuid",
                edge_uuid=str(edge.id),
                rationale=edge.rationale,
                review_state=edge.review_state,
            )

        # theme_role: Company -[:THEME_ROLE]-> ResearchCase
        for role in self._instruments.all_theme_roles():
            if role.research_case_id is None:
                continue
            self._merge_relationship(
                "THEME_ROLE",
                "Company",
                role.company_id,
                "ResearchCase",
                role.research_case_id,
                "role_uuid",
                role_uuid=str(role.id),
                role=role.role,
            )

        # holding: Fund -[:HOLDING]-> Stock
        for disclosure in self._instruments.all_holding_disclosures():
            self._merge_relationship(
                "HOLDING",
                "Fund",
                disclosure.fund_id,
                "Stock",
                disclosure.stock_id,
                "disclosure_uuid",
                disclosure_uuid=str(disclosure.id),
                weight=_dec(disclosure.weight),
                report_period=_iso(disclosure.report_period),
            )
