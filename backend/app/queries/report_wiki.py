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
import uuid
from collections import defaultdict
from dataclasses import dataclass
from unicodedata import normalize

from sqlalchemy import and_, select
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
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportRelation,
)
from app.schemas.v1.report_research import (
    ReportFactorDTO,
    ReportWikiEdgeDTO,
    ReportWikiGraphDTO,
    ReportWikiNodeDTO,
)
from app.services.report_research import ReportFactorClassifier


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


class ReportWikiQueries:
    """Build one current (or explicitly selected historical) report scope."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def graph(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
    ) -> ReportWikiGraphDTO:
        self._require_case(case_id)
        document = self._scope_document(case_id, scope_version_id)
        scope_version = str(document.id)
        claims, claim_spans, claim_statements = self._claims_for_document(
            case_id, document.id
        )
        claim_ids = {claim.id for claim in claims}
        relations = self._relations(claim_ids)
        relations_by_claim: dict[uuid.UUID, list[ReportRelation]] = defaultdict(list)
        for relation in relations:
            relations_by_claim[relation.claim_id].append(relation)
        companies = self._companies(relations)
        observations_by_claim = self._observations(claim_ids)
        confounders_by_claim = self._confounders(claim_ids)
        confounder_sources = self._confounder_sources(
            case_id,
            {
                row.source_statement_id
                for rows in confounders_by_claim.values()
                for row in rows
            },
        )
        exposures_by_claim = self._fund_exposures(claim_ids)
        funds = self._funds(exposures_by_claim)
        independent_evidence = self._independent_case_evidence(case_id, document.id)

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
                    scope_version=scope_version,
                )
            )
            add_node(
                ReportWikiNodeDTO(
                    id=evidence_node_id,
                    kind="evidence",
                    label=statement.normalized_text,
                    status="report_claim",
                    source_locator=locator,
                    scope_version=scope_version,
                )
            )
            add_edge(
                self._edge(
                    claim_node_id,
                    evidence_node_id,
                    "reported_by",
                    "report_claim",
                    locator,
                    scope_version,
                )
            )

            claim_relations = relations_by_claim[claim.id]
            relation_verified = False
            operating = False
            relation_evidence: dict[uuid.UUID, _IndependentEvidence] = {}
            operating_evidence: dict[uuid.UUID, _IndependentEvidence] = {}
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
                        scope_version=scope_version,
                    )
                )
                add_node(
                    ReportWikiNodeDTO(
                        id=object_id,
                        kind="company",
                        label=object_label,
                        status="report_claim",
                        source_locator=locator,
                        scope_version=scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        subject_id,
                        object_id,
                        relation.relation_kind,
                        "report_claim",
                        locator,
                        scope_version,
                        suffix=str(relation.id),
                    )
                )
                verified, operating_rows = self._independent_relation_evidence(
                    relation=relation,
                    companies=companies,
                    evidence=independent_evidence,
                )
                if verified is not None:
                    relation_verified = True
                    relation_evidence[verified.statement.id] = verified
                for row in operating_rows:
                    operating = True
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
                        scope_version=scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        independent_node_id,
                        "independent_evidence",
                        "verified",
                        independent_locator,
                        scope_version,
                    )
                )

            observations = observations_by_claim.get(claim.id, ())
            market = any(
                row.status == "verified" and row.kind == "target_market"
                for row in observations
            )
            peer = any(
                row.status == "verified"
                and row.kind in {"peer_control", "industry_control"}
                for row in observations
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
                        scope_version=scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        market_node_id,
                        observation.kind,
                        "market_observation",
                        market_locator,
                        scope_version,
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
                        scope_version=scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        confounder_node_id,
                        f"confounder:{confounder.kind}",
                        "candidate",
                        confounder_locator,
                        scope_version,
                    )
                )

            for exposure in exposures_by_claim.get(claim.id, ()):
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
                        scope_version=scope_version,
                    )
                )
                add_edge(
                    self._edge(
                        claim_node_id,
                        fund_node_id,
                        "fund_exposure",
                        "verified" if exposure.status == "verified" else "candidate",
                        fund_locator,
                        scope_version,
                        suffix=str(exposure.id),
                    )
                )

            assessment = ReportFactorClassifier.classify(
                report_source=True,
                company_relation=relation_verified,
                operating=operating,
                market=market,
                peer=peer,
                confounder=bool(confounders),
            )
            factors.append(
                ReportFactorDTO(
                    claim_id=claim.id,
                    statement=claim.statement,
                    classification=assessment.classification,
                    components=assessment.components,
                    explanation=assessment.explanation,
                )
            )

        return ReportWikiGraphDTO(
            research_case_id=case_id,
            scope_version=scope_version,
            document_id=document.id,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            factors=factors,
        )

    def _require_case(self, case_id: uuid.UUID) -> None:
        if self._session.get(ResearchCase, case_id) is None:
            raise NotFoundError("report research case not found")

    def _scope_document(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID | None
    ) -> DocumentVersion:
        statement = (
            select(DocumentVersion)
            .join(
                ReportCaseSourceSpan,
                ReportCaseSourceSpan.document_version_id == DocumentVersion.id,
            )
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == DocumentVersion.id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(ReportCaseSourceSpan.research_case_id == case_id)
        )
        if scope_version_id is not None:
            statement = statement.where(DocumentVersion.id == scope_version_id)
        else:
            statement = statement.order_by(
                CaseDocumentVersion.linked_at.desc(),
                DocumentVersion.available_at.desc(),
                DocumentVersion.id.desc(),
            ).limit(1)
        document = self._session.scalars(statement).first()
        if document is None:
            raise NotFoundError("report research scope not found")
        return document

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
        self, claim_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[ReportMarketObservation]]:
        grouped: dict[uuid.UUID, list[ReportMarketObservation]] = defaultdict(list)
        if claim_ids:
            for row in self._session.scalars(
                select(ReportMarketObservation)
                .where(ReportMarketObservation.report_claim_id.in_(claim_ids))
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

    def _fund_exposures(
        self, claim_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, list[ReportFundExposure]]:
        grouped: dict[uuid.UUID, list[ReportFundExposure]] = defaultdict(list)
        if claim_ids:
            for row in self._session.scalars(
                select(ReportFundExposure)
                .where(ReportFundExposure.report_claim_id.in_(claim_ids))
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
        self, case_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[_IndependentEvidence, ...]:
        rows = self._session.execute(
            select(SourceStatement, SourceSpan)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == SourceSpan.document_version_id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(SourceSpan.document_version_id != document_id)
        )
        return tuple(_IndependentEvidence(statement, span) for statement, span in rows)

    def _confounder_sources(
        self, case_id: uuid.UUID, statement_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[SourceStatement, SourceSpan]]:
        """Load every exposed confounder locator in one case-scoped query."""
        if not statement_ids:
            return {}
        rows = self._session.execute(
            select(SourceStatement, SourceSpan)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(
                CaseDocumentVersion,
                and_(
                    CaseDocumentVersion.document_version_id == SourceSpan.document_version_id,
                    CaseDocumentVersion.research_case_id == case_id,
                ),
            )
            .where(SourceStatement.id.in_(statement_ids))
        )
        return {statement.id: (statement, span) for statement, span in rows}

    def _independent_relation_evidence(
        self,
        *,
        relation: ReportRelation,
        companies: dict[uuid.UUID, Company],
        evidence: tuple[_IndependentEvidence, ...],
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
        for row in evidence:
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
        scope_version: str,
        *,
        suffix: str = "",
    ) -> ReportWikiEdgeDTO:
        material = "|".join((source_id, target_id, kind, status, source_locator or "", suffix))
        return ReportWikiEdgeDTO(
            id="edge:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24],
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            status=status,
            source_locator=source_locator,
            scope_version=scope_version,
        )
