"""Source-safe, document-scoped read model for the report Wiki graph.

The report ledger is append-only.  This module therefore builds a projection
instead of persisting a mutable graph snapshot: every node and edge retains
the span, snapshot or disclosure that produced it.  A graph is always scoped
to exactly one attached report document so an updated report cannot silently
mix claims from an earlier version into the current view.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from unicodedata import normalize

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    Fund,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportFundExposure,
    ReportConfounderAssessment,
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportRelation,
    ReportResearchScopeVersion,
    ReportResearchScopeClaim,
    ReportResearchScopeRelation,
)
from app.schemas.v1.report_research import (
    ReportFactorDTO,
    ReportResearchScopeDTO,
    ReportEmbedFactorDTO,
    ReportEmbedWikiEdgeDTO,
    ReportEmbedWikiGraphDTO,
    ReportEmbedWikiNodeDTO,
    ReportWikiEdgeDTO,
    ReportWikiGraphDTO,
    ReportWikiNodeDTO,
)
from app.services.report_research import ReportFactorClassifier
from app.services.source_admission import classify_source


_OPERATING_TERMS = (
    "订单",
    "出货",
    "产能",
    "收入",
    "营收",
    "利润",
    "毛利",
    "成本",
    "销量",
    "需求",
    "经营",
)
_RELATION_TERMS = {
    "supplier": ("供应商", "供货"),
    "customer": ("客户", "采购"),
    "competitor": ("竞争", "竞品"),
}


@dataclass(frozen=True)
class _IndependentEvidence:
    statement: SourceStatement
    span: SourceSpan


@dataclass(frozen=True)
class _IndependentEvidenceIndex:
    """One-pass entity index for admitted case evidence."""

    by_entity: dict[str, tuple[_IndependentEvidence, ...]]
    scan_iterations: int
    match_iterations: int
    bucket_inserts: int


class ReportWikiQueries:
    """Build one current (or explicitly selected historical) report scope."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def graph(
        self,
        case_id: uuid.UUID,
        *,
        scope_version: int | None = None,
        relation_id: uuid.UUID | None = None,
    ) -> ReportWikiGraphDTO:
        self._require_case(case_id)
        scope = self._scope(case_id, scope_version)
        document = self._session.get(DocumentVersion, scope.document_version_id)
        if document is None:  # foreign key is the database boundary; fail closed for SQLite fixtures.
            raise NotFoundError("report scope document not found")
        current_scope_version = scope.version
        claims, claim_spans, claim_statements = self._claims_for_document(
            case_id, document.id
        )
        selected_claim_ids, selected_relation_ids = self._scope_paths(scope.id)
        if not selected_claim_ids:
            raise NotFoundError("report scope has no selected claim paths")
        claims = [claim for claim in claims if claim.id in selected_claim_ids]
        claim_spans = {
            claim_id: span
            for claim_id, span in claim_spans.items()
            if claim_id in selected_claim_ids
        }
        claim_statements = {
            claim_id: statement
            for claim_id, statement in claim_statements.items()
            if claim_id in selected_claim_ids
        }
        claim_ids = {claim.id for claim in claims}
        relations = [
            relation
            for relation in self._relations(claim_ids)
            if relation.id in selected_relation_ids
        ]
        if relation_id is not None and relation_id not in {row.id for row in relations}:
            raise NotFoundError("report relation is not in the selected scope")
        selected_claim_id = next(
            (
                row.claim_id
                for row in relations
                if relation_id is not None and row.id == relation_id
            ),
            None,
        )
        relations_by_claim: dict[uuid.UUID, list[ReportRelation]] = defaultdict(list)
        for relation in relations:
            relations_by_claim[relation.claim_id].append(relation)
        companies = self._companies(relations)
        observations_by_claim = self._observations(claim_ids, selected_relation_ids)
        confounders_by_claim = self._confounders(claim_ids)
        confounder_sources = self._confounder_sources(
            case_id,
            document.id,
            scope.visibility_cutoff_at,
            {
                row.source_statement_id
                for rows in confounders_by_claim.values()
                for row in rows
            },
        )
        confounders_by_claim = {
            claim_id: [
                row
                for row in rows
                if row.source_statement_id in confounder_sources
            ]
            for claim_id, rows in confounders_by_claim.items()
        }
        confounder_assessments = self._confounder_assessments(claim_ids)
        exposures_by_claim = self._fund_exposures(claim_ids, selected_relation_ids)
        funds = self._funds(exposures_by_claim)
        # ``visibility_cutoff_at`` is immutable for this scope.
        # Later disclosures may be examined in a successor scope, but cannot
        # retroactively promote this historical scope to a causal key factor.
        independent_evidence = self._independent_case_evidence(
            case_id, document.id, scope.visibility_cutoff_at
        )
        # Index every entity label in the selected graph once.  Rebuilding
        # this per claim makes dense reports O(claims * evidence), even though
        # the evidence set and entity vocabulary are scope-wide.
        evidence_index = self._independent_evidence_index(
            independent_evidence, relations, companies
        )

        nodes: dict[str, ReportWikiNodeDTO] = {}
        edges: dict[str, ReportWikiEdgeDTO] = {}

        def add_node(node: ReportWikiNodeDTO) -> None:
            nodes.setdefault(node.id, node)

        def add_edge(edge: ReportWikiEdgeDTO) -> None:
            edges.setdefault(edge.id, edge)

        factors: list[ReportFactorDTO] = []
        for claim in claims:
            span = claim_spans[claim.id]
            statement = claim_statements[claim.id]
            locator = self._locator(span)
            claim_node_id = f"report_claim:{claim.id}"
            evidence_node_id = f"evidence:{statement.id}"
            add_node(
                ReportWikiNodeDTO(
                    id=claim_node_id,
                    kind="report_claim",
                    label=claim.statement,
                    status="report_claim",
                    source_locator=locator,
                    scope_version=current_scope_version,
                )
            )
            add_node(
                ReportWikiNodeDTO(
                    id=evidence_node_id,
                    kind="evidence",
                    label=statement.normalized_text,
                    status="report_claim",
                    source_locator=locator,
                    scope_version=current_scope_version,
                )
            )
            add_edge(
                self._edge(
                    claim_node_id,
                    evidence_node_id,
                    "reported_by",
                    "report_claim",
                    locator,
                    current_scope_version,
                )
            )

            claim_relations = relations_by_claim[claim.id]
            relation_evidence: dict[uuid.UUID, _IndependentEvidence] = {}
            operating_evidence: dict[uuid.UUID, _IndependentEvidence] = {}
            path_evidence: dict[
                uuid.UUID, tuple[_IndependentEvidence | None, tuple[_IndependentEvidence, ...]]
            ] = {}
            for relation in claim_relations:
                subject_id, subject_label = self._company_node(
                    relation.subject_company_id,
                    relation.subject_name,
                    companies,
                )
                object_id, object_label = self._company_node(
                    relation.object_company_id,
                    relation.object_name,
                    companies,
                )
                add_node(
                    ReportWikiNodeDTO(
                        id=subject_id,
                        kind="company",
                        label=subject_label,
                        status="report_claim",
                        source_locator=locator,
                        scope_version=current_scope_version,
                    )
                )
                add_node(
                    ReportWikiNodeDTO(
                        id=object_id,
                        kind="company",
                        label=object_label,
                        status="report_claim",
                        source_locator=locator,
                        scope_version=current_scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        subject_id,
                        object_id,
                        relation.relation_kind,
                        "report_claim",
                        locator,
                        current_scope_version,
                        suffix=str(relation.id),
                        relation_id=relation.id,
                    )
                )
                verified, operating_rows = self._independent_relation_evidence(
                    relation=relation,
                    companies=companies,
                    evidence_index=evidence_index,
                )
                path_evidence[relation.id] = (verified, operating_rows)
                if verified is not None:
                    relation_evidence[verified.statement.id] = verified
                for row in operating_rows:
                    operating_evidence[row.statement.id] = row

            for row in (*relation_evidence.values(), *operating_evidence.values()):
                independent_node_id = f"evidence:{row.statement.id}"
                independent_locator = self._locator(row.span)
                add_node(
                    ReportWikiNodeDTO(
                        id=independent_node_id,
                        kind="evidence",
                        label=row.statement.normalized_text,
                        status="verified",
                        source_locator=independent_locator,
                        scope_version=current_scope_version,
                    )
                )
            for relation in claim_relations:
                relation_proof, operating_rows = path_evidence[relation.id]
                relation_rows = [
                    row
                    for row in operating_rows
                    if row.statement.id in operating_evidence
                ]
                if relation_proof is not None:
                    relation_rows.append(relation_proof)
                for row in {item.statement.id: item for item in relation_rows}.values():
                    independent_node_id = f"evidence:{row.statement.id}"
                    add_edge(
                        self._edge(
                            claim_node_id,
                            independent_node_id,
                            "independent_evidence",
                            "verified",
                            self._locator(row.span),
                            current_scope_version,
                            suffix=f"{relation.id}:{row.statement.id}",
                            relation_id=relation.id,
                        )
                    )

            observations = tuple(
                row
                for row in observations_by_claim.get(claim.id, ())
                if row.report_relation_id is None
                or row.report_relation_id in selected_relation_ids
            )
            for observation in observations:
                market_node_id = f"market_window:{observation.id}"
                market_locator = self._market_locator(observation)
                add_node(
                    ReportWikiNodeDTO(
                        id=market_node_id,
                        kind="market_window",
                        label=f"发布后 {observation.window}：{observation.summary}",
                        status="market_observation",
                        source_locator=market_locator,
                        scope_version=current_scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        market_node_id,
                        observation.kind,
                        "market_observation",
                        market_locator,
                        current_scope_version,
                        relation_id=observation.report_relation_id,
                    )
                )

            confounders = confounders_by_claim.get(claim.id, ())
            for confounder in confounders:
                # Its Statement was collected only through a case-document
                # join in the collection service.  The read query performs
                # that same safe lookup before exposing a locator.
                confounder_row = confounder_sources.get(confounder.source_statement_id)
                if confounder_row is None:
                    continue
                confounder_statement, confounder_span = confounder_row
                confounder_node_id = f"evidence:{confounder_statement.id}"
                confounder_locator = self._locator(confounder_span)
                add_node(
                    ReportWikiNodeDTO(
                        id=confounder_node_id,
                        kind="evidence",
                        label=confounder.summary,
                        status="candidate",
                        source_locator=confounder_locator,
                        scope_version=current_scope_version,
                    )
                )
                for relation in claim_relations:
                    outcome = confounder_assessments.get((relation.id, confounder.id))
                    confounder_status = (
                        "verified"
                        if outcome is not None and outcome.outcome == "not_material"
                        else "rejected"
                        if outcome is not None and outcome.outcome == "material"
                        else "candidate"
                    )
                    add_edge(
                        self._edge(
                            claim_node_id,
                            confounder_node_id,
                            f"confounder:{confounder.kind}",
                            confounder_status,
                            confounder_locator,
                            current_scope_version,
                            suffix=f"{relation.id}:{confounder.id}",
                            relation_id=relation.id,
                        )
                    )

            for exposure in (
                row
                for row in exposures_by_claim.get(claim.id, ())
                if row.report_relation_id in selected_relation_ids
            ):
                fund = funds.get(exposure.fund_id) if exposure.fund_id else None
                fund_node_id = (
                    f"fund:{fund.id}" if fund is not None else f"fund_gap:{exposure.id}"
                )
                fund_label = (
                    f"{fund.code} {fund.name}"
                    if fund is not None
                    else exposure.summary
                )
                fund_locator = (
                    f"holding_disclosure:{exposure.holding_disclosure_id}"
                    if exposure.holding_disclosure_id is not None
                    else f"report_fund_exposure:{exposure.id}"
                )
                add_node(
                    ReportWikiNodeDTO(
                        id=fund_node_id,
                        kind="fund",
                        label=fund_label,
                        status="verified" if exposure.status == "verified" else "candidate",
                        source_locator=fund_locator,
                        scope_version=current_scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        fund_node_id,
                        "fund_exposure",
                        "verified" if exposure.status == "verified" else "candidate",
                        fund_locator,
                        current_scope_version,
                        suffix=str(exposure.id),
                        relation_id=exposure.report_relation_id,
                    )
                )

            # Every factor is assessed per relationship path.  A peer window
            # for company A and a target window for company B must never be
            # unioned into a fabricated "key" explanation.
            factor_paths: list[ReportRelation | None] = claim_relations or [None]
            for relation in factor_paths:
                path_relation_id = relation.id if relation is not None else None
                relation_proof, operating_rows = (
                    path_evidence.get(path_relation_id, (None, ()))
                    if path_relation_id is not None
                    else (None, ())
                )
                path_observations = [
                    row
                    for row in observations
                    if row.report_relation_id == path_relation_id
                ]
                path_market = any(
                    row.status == "verified" and row.kind == "target_market"
                    for row in path_observations
                )
                path_peer = any(
                    row.status == "verified"
                    and row.kind in {"peer_control", "industry_control"}
                    for row in path_observations
                )
                path_confounders = confounders if path_relation_id is not None else ()
                latest_outcomes = {
                    confounder.id: confounder_assessments.get(
                        (path_relation_id, confounder.id)
                    )
                    for confounder in path_confounders
                }
                confounder_assessed = bool(path_confounders) and all(
                    outcome is not None and outcome.outcome != "unresolved"
                    for outcome in latest_outcomes.values()
                )
                competing_explanation = any(
                    outcome is not None and outcome.outcome == "material"
                    for outcome in latest_outcomes.values()
                )
                assessment = ReportFactorClassifier.classify(
                    report_source=True,
                    company_relation=relation_proof is not None,
                    operating=bool(operating_rows),
                    market=path_market,
                    peer=path_peer,
                    confounder_assessed=confounder_assessed,
                    competing_explanation=competing_explanation,
                )
                factors.append(
                    ReportFactorDTO(
                        claim_id=claim.id,
                        relation_id=path_relation_id,
                        statement=claim.statement,
                        classification=assessment.classification,
                        components=assessment.components,
                        explanation=assessment.explanation,
                    )
                )

        selected_edges = list(edges.values())
        selected_factors = factors
        if relation_id is not None:
            selected_edges = [
                edge
                for edge in selected_edges
                if edge.relation_id in {None, relation_id}
                and (
                    edge.relation_id == relation_id
                    or (
                        edge.kind == "reported_by"
                        and edge.source_id == f"report_claim:{selected_claim_id}"
                    )
                )
            ]
            visible_ids = {
                node_id
                for edge in selected_edges
                for node_id in (edge.source_id, edge.target_id)
            }
            nodes = {node_id: node for node_id, node in nodes.items() if node_id in visible_ids}
            selected_factors = [
                factor for factor in factors if factor.relation_id == relation_id
            ]
        return ReportWikiGraphDTO(
            research_case_id=case_id,
            scope_version=current_scope_version,
            scope=ReportResearchScopeDTO(
                version=scope.version,
                document_id=scope.document_version_id,
                visibility_cutoff_at=scope.visibility_cutoff_at,
                research_question=scope.research_question,
                factor_selection=list(scope.factor_selection),
                evidence_plan=list(scope.evidence_plan),
                selected_claim_ids=sorted(selected_claim_ids),
                selected_relation_ids=sorted(selected_relation_ids),
            ),
            document_id=document.id,
            nodes=list(nodes.values()),
            edges=selected_edges,
            factors=selected_factors,
        )

    def embed_graph(self, case_id: uuid.UUID) -> ReportEmbedWikiGraphDTO:
        """Return a deliberately small, source-safe projection for embeds.

        The normal Wiki graph is an authorized-researcher read model.  An
        external embed has a narrower contract: it must never receive report
        prose, an original upload locator, reviewer identity, or an internal
        record locator that could be used to pivot into the main workspace.
        """
        graph = self.graph(case_id)
        node_ids = {
            node.id: f"n{position}"
            for position, node in enumerate(graph.nodes, start=1)
        }
        embed_nodes = [
            ReportEmbedWikiNodeDTO(
                id=node_ids[node.id],
                kind=node.kind,
                label=self._embed_node_label(node),
                status=node.status,
            )
            for node in graph.nodes
        ]
        embed_edges = [
            ReportEmbedWikiEdgeDTO(
                id=f"e{position}",
                source_id=node_ids[edge.source_id],
                target_id=node_ids[edge.target_id],
                kind=edge.kind,
                status=edge.status,
            )
            for position, edge in enumerate(graph.edges, start=1)
            if edge.source_id in node_ids and edge.target_id in node_ids
        ]
        embed_factors = [
            ReportEmbedFactorDTO(
                classification=factor.classification,
                components=factor.components,
                explanation=self._embed_factor_explanation(factor.classification),
            )
            for factor in graph.factors
        ]
        return ReportEmbedWikiGraphDTO(
            nodes=embed_nodes,
            edges=embed_edges,
            factors=embed_factors,
        )

    def _require_case(self, case_id: uuid.UUID) -> None:
        if self._session.get(ResearchCase, case_id) is None:
            raise NotFoundError("report research case not found")

    def _scope(
        self, case_id: uuid.UUID, scope_version: int | None
    ) -> ReportResearchScopeVersion:
        statement = (
            select(ReportResearchScopeVersion)
            .where(ReportResearchScopeVersion.research_case_id == case_id)
        )
        if scope_version is not None:
            statement = statement.where(ReportResearchScopeVersion.version == scope_version)
        else:
            statement = statement.order_by(ReportResearchScopeVersion.version.desc()).limit(1)
        selected = self._session.scalars(statement).first()
        if selected is None:
            raise NotFoundError("report research scope not found")
        return selected

    def _scope_paths(
        self, scope_version_id: uuid.UUID
    ) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        """Read one immutable scope's selected claim and relation IDs in bulk."""
        claim_ids = set(
            self._session.scalars(
                select(ReportResearchScopeClaim.report_claim_id).where(
                    ReportResearchScopeClaim.scope_version_id == scope_version_id
                )
            )
        )
        relation_ids = set(
            self._session.scalars(
                select(ReportResearchScopeRelation.report_relation_id).where(
                    ReportResearchScopeRelation.scope_version_id == scope_version_id
                )
            )
        )
        return claim_ids, relation_ids

    def _claims_for_document(
        self, case_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[list[ReportClaim], dict[uuid.UUID, SourceSpan], dict[uuid.UUID, SourceStatement]]:
        rows = self._session.execute(
            select(ReportClaim, SourceSpan, SourceStatement)
            .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
            .join(
                SourceStatement,
                and_(
                    SourceStatement.id == ReportClaim.source_statement_id,
                    SourceStatement.source_span_id == SourceSpan.id,
                ),
            )
            .join(
                ReportCaseSourceSpan,
                and_(
                    ReportCaseSourceSpan.source_span_id == SourceSpan.id,
                    ReportCaseSourceSpan.document_version_id == document_id,
                    ReportCaseSourceSpan.research_case_id == case_id,
                ),
            )
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == document_id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(ReportClaim.research_case_id == case_id)
            .where(SourceSpan.document_version_id == document_id)
            .order_by(ReportClaim.created_at, ReportClaim.id)
        )
        claims: list[ReportClaim] = []
        spans: dict[uuid.UUID, SourceSpan] = {}
        statements: dict[uuid.UUID, SourceStatement] = {}
        for claim, span, source_statement in rows:
            claims.append(claim)
            spans[claim.id] = span
            statements[claim.id] = source_statement
        return claims, spans, statements

    def _relations(self, claim_ids: set[uuid.UUID]) -> list[ReportRelation]:
        if not claim_ids:
            return []
        return list(
            self._session.scalars(
                select(ReportRelation)
                .where(ReportRelation.claim_id.in_(claim_ids))
                .order_by(ReportRelation.created_at, ReportRelation.id)
            )
        )

    def _companies(self, relations: list[ReportRelation]) -> dict[uuid.UUID, Company]:
        ids = {
            value
            for relation in relations
            for value in (relation.subject_company_id, relation.object_company_id)
            if value is not None
        }
        if not ids:
            return {}
        return {
            company.id: company
            for company in self._session.scalars(select(Company).where(Company.id.in_(ids)))
        }

    def _observations(
        self, claim_ids: set[uuid.UUID], selected_relation_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[ReportMarketObservation]]:
        grouped: dict[uuid.UUID, list[ReportMarketObservation]] = defaultdict(list)
        if claim_ids:
            allowed_paths = (
                ReportMarketObservation.report_relation_id.in_(selected_relation_ids)
                if selected_relation_ids
                else ReportMarketObservation.report_relation_id.is_(None)
            )
            for row in self._session.scalars(
                select(ReportMarketObservation)
                .where(ReportMarketObservation.report_claim_id.in_(claim_ids))
                .where(
                    or_(
                        ReportMarketObservation.report_relation_id.is_(None),
                        allowed_paths,
                    )
                )
                .order_by(ReportMarketObservation.created_at, ReportMarketObservation.id)
            ):
                grouped[row.report_claim_id].append(row)
        return grouped

    def _confounders(
        self, claim_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[ReportMarketConfounder]]:
        grouped: dict[uuid.UUID, list[ReportMarketConfounder]] = defaultdict(list)
        if claim_ids:
            for row in self._session.scalars(
                select(ReportMarketConfounder)
                .where(ReportMarketConfounder.report_claim_id.in_(claim_ids))
                .order_by(ReportMarketConfounder.created_at, ReportMarketConfounder.id)
            ):
                grouped[row.report_claim_id].append(row)
        return grouped

    def _confounder_assessments(
        self, claim_ids: set[uuid.UUID]
    ) -> dict[tuple[uuid.UUID, uuid.UUID], ReportConfounderAssessment]:
        """Read only the latest append-only outcome for each relation path."""
        latest: dict[tuple[uuid.UUID, uuid.UUID], ReportConfounderAssessment] = {}
        if not claim_ids:
            return latest
        for row in self._session.scalars(
            select(ReportConfounderAssessment)
            .where(ReportConfounderAssessment.report_claim_id.in_(claim_ids))
            .order_by(
                ReportConfounderAssessment.created_at.desc(),
                ReportConfounderAssessment.id.desc(),
            )
        ):
            latest.setdefault((row.report_relation_id, row.report_confounder_id), row)
        return latest

    def _fund_exposures(
        self, claim_ids: set[uuid.UUID], selected_relation_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[ReportFundExposure]]:
        grouped: dict[uuid.UUID, list[ReportFundExposure]] = defaultdict(list)
        if claim_ids and selected_relation_ids:
            for row in self._session.scalars(
                select(ReportFundExposure)
                .where(ReportFundExposure.report_claim_id.in_(claim_ids))
                .where(ReportFundExposure.report_relation_id.in_(selected_relation_ids))
                .order_by(ReportFundExposure.created_at, ReportFundExposure.id)
            ):
                grouped[row.report_claim_id].append(row)
        return grouped

    def _funds(
        self, exposures_by_claim: dict[uuid.UUID, list[ReportFundExposure]]
    ) -> dict[uuid.UUID, Fund]:
        fund_ids = {
            exposure.fund_id
            for exposures in exposures_by_claim.values()
            for exposure in exposures
            if exposure.fund_id is not None
        }
        if not fund_ids:
            return {}
        return {
            fund.id: fund
            for fund in self._session.scalars(select(Fund).where(Fund.id.in_(fund_ids)))
        }

    def _independent_case_evidence(
        self,
        case_id: uuid.UUID,
        document_id: uuid.UUID,
        visibility_cutoff: datetime,
    ) -> tuple[_IndependentEvidence, ...]:
        rows = self._session.execute(
            select(SourceStatement, SourceSpan, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == SourceSpan.document_version_id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(SourceSpan.document_version_id != document_id)
            .where(SourceStatement.kind.in_(("disclosed_fact", "management_attribution")))
            .where(DocumentVersion.available_at <= visibility_cutoff)
            .where(CaseDocumentVersion.linked_at <= visibility_cutoff)
        )
        return tuple(
            _IndependentEvidence(statement, span)
            for statement, span, source_document in rows
            if classify_source(
                source_document.source_url,
                source_document.parser_version,
                source_document.parse_state in {"success", "parsed"},
            ).can_accept
        )

    def _confounder_sources(
        self,
        case_id: uuid.UUID,
        report_document_id: uuid.UUID,
        visibility_cutoff: datetime,
        statement_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[SourceStatement, SourceSpan]]:
        """Load only source-admitted, cutoff-visible confounder locators."""
        if not statement_ids:
            return {}
        rows = self._session.execute(
            select(SourceStatement, SourceSpan, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == SourceSpan.document_version_id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(SourceStatement.id.in_(statement_ids))
            .where(SourceSpan.document_version_id != report_document_id)
            .where(SourceStatement.kind.in_(("disclosed_fact", "management_attribution")))
            .where(DocumentVersion.available_at <= visibility_cutoff)
            .where(CaseDocumentVersion.linked_at <= visibility_cutoff)
        )
        return {
            statement.id: (statement, span)
            for statement, span, source_document in rows
            if classify_source(
                source_document.source_url,
                source_document.parser_version,
                source_document.parse_state in {"success", "parsed"},
            ).can_accept
        }

    def _independent_relation_evidence(
        self,
        *,
        relation: ReportRelation,
        companies: dict[uuid.UUID, Company],
        evidence_index: _IndependentEvidenceIndex,
    ) -> tuple[_IndependentEvidence | None, tuple[_IndependentEvidence, ...]]:
        subject = self._relation_label(
            relation.subject_company_id, relation.subject_name, companies
        )
        obj = self._relation_label(
            relation.object_company_id, relation.object_name, companies
        )
        if not subject or not obj:
            return None, ()
        relation_terms = _RELATION_TERMS.get(relation.relation_kind, (relation.relation_kind,))
        relation_row: _IndependentEvidence | None = None
        operating_rows: list[_IndependentEvidence] = []
        candidates = {
            row.statement.id: row
            for label in (subject, obj)
            for row in evidence_index.by_entity.get(label, ())
        }
        for row in candidates.values():
            text = row.statement.normalized_text
            if subject in text or obj in text:
                if any(term in text for term in _OPERATING_TERMS):
                    operating_rows.append(row)
            if (
                relation_row is None
                and subject in text
                and obj in text
                and any(term in text for term in relation_terms)
            ):
                relation_row = row
        return relation_row, tuple(operating_rows)

    def _independent_evidence_index(
        self,
        evidence: tuple[_IndependentEvidence, ...],
        relations: list[ReportRelation],
        companies: dict[uuid.UUID, Company],
    ) -> _IndependentEvidenceIndex:
        labels = {
            label
            for relation in relations
            for label in (
                self._relation_label(relation.subject_company_id, relation.subject_name, companies),
                self._relation_label(relation.object_company_id, relation.object_name, companies),
            )
            if label
        }
        if not labels:
            return _IndependentEvidenceIndex({}, len(evidence), 0, 0)
        matcher = re.compile("|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True)))
        buckets: dict[str, list[_IndependentEvidence]] = defaultdict(list)
        match_iterations = 0
        bucket_inserts = 0
        for row in evidence:
            for match in set(matcher.findall(row.statement.normalized_text)):
                match_iterations += 1
                buckets[match].append(row)
                bucket_inserts += 1
        return _IndependentEvidenceIndex(
            {label: tuple(rows) for label, rows in buckets.items()},
            len(evidence),
            match_iterations,
            bucket_inserts,
        )

    @staticmethod
    def _relation_label(
        company_id: uuid.UUID | None,
        name: str | None,
        companies: dict[uuid.UUID, Company],
    ) -> str | None:
        company = companies.get(company_id) if company_id is not None else None
        return company.name if company is not None else name

    def _company_node(
        self,
        company_id: uuid.UUID | None,
        name: str | None,
        companies: dict[uuid.UUID, Company],
    ) -> tuple[str, str]:
        company = companies.get(company_id) if company_id is not None else None
        if company is not None:
            return f"company:{company.id}", company.name
        label = name or "未解析公司"
        digest = hashlib.sha256(self._identity(label).encode("utf-8")).hexdigest()[:24]
        return f"company:unresolved:{digest}", label

    @staticmethod
    def _identity(value: str) -> str:
        return " ".join(normalize("NFKC", value).split()).casefold()

    @staticmethod
    def _locator(span: SourceSpan) -> str:
        return json.dumps(span.locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _embed_node_label(node: ReportWikiNodeDTO) -> str:
        if node.kind == "report_claim":
            return "研报主张"
        if node.kind == "evidence":
            return "已核验的外部证据" if node.status == "verified" else "研报来源证据"
        if node.kind == "market_window":
            return "市场观察"
        if node.kind == "fund":
            # A fund position can be sensitive even when its exact weight is
            # absent.  The external graph exposes a mapping without naming a
            # product or revealing its disclosure record.
            return "关联基金"
        return node.label

    @staticmethod
    def _embed_factor_explanation(classification: str) -> str:
        if classification == "key":
            return "证据链已满足关键因素的审阅门槛。"
        if classification == "alternative":
            return "存在需要并列评估的替代解释。"
        return "当前证据链仍有待补充的验证缺口。"

    @staticmethod
    def _market_locator(observation: ReportMarketObservation) -> str:
        if observation.valuation_snapshot_id is not None:
            return f"valuation_snapshot:{observation.valuation_snapshot_id}"
        if observation.industry_index_snapshot_id is not None:
            return f"industry_index_snapshot:{observation.industry_index_snapshot_id}"
        return f"report_market_observation:{observation.id}"

    @staticmethod
    def _edge(
        source_id: str,
        target_id: str,
        kind: str,
        status: str,
        source_locator: str | None,
        scope_version: int,
        *,
        suffix: str = "",
        relation_id: uuid.UUID | None = None,
    ) -> ReportWikiEdgeDTO:
        material = "|".join(
            (source_id, target_id, kind, status, source_locator or "", suffix, str(relation_id or ""))
        )
        return ReportWikiEdgeDTO(
            id="edge:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            status=status,
            relation_id=relation_id,
            source_locator=source_locator,
            scope_version=scope_version,
        )
