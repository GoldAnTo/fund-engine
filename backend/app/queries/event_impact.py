"""Bulk current-scope read model for event impact traces."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyImpactRelationReview,
    EventImpactHypothesis,
    EventImpactHypothesisAssessment,
)
from app.models.event_research import EventResearchBrief, EventResearchScopeVersion
from app.models.ledger import Company, Stock
from app.models.ledger import Fund, HoldingDisclosure
from app.schemas.v1.event_impact import EventImpactTraceDTO
from app.services.china_market_data import CHINA_A_SHARE_MARKETS
from app.services.china_market_data import is_china_public_fund


class EventImpactQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    def trace(self, case_id: uuid.UUID) -> EventImpactTraceDTO:
        if self._session.scalar(select(EventResearchBrief.id).where(EventResearchBrief.research_case_id == case_id)) is None:
            raise NotFoundError("event research case not found")
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if scope is None:
            raise NotFoundError("event research scope not found")
        hypotheses = list(self._session.scalars(
            select(EventImpactHypothesis)
            .where(EventImpactHypothesis.research_case_id == case_id)
            .where(EventImpactHypothesis.scope_version_id == scope.id)
            .order_by(EventImpactHypothesis.rank, EventImpactHypothesis.created_at)
        ))
        hypothesis_ids = [row.id for row in hypotheses]
        relations = list(self._session.scalars(
            select(CompanyImpactRelation).where(CompanyImpactRelation.hypothesis_id.in_(hypothesis_ids))
        )) if hypothesis_ids else []
        relation_ids = [row.id for row in relations]
        companies = {row.id: row for row in self._session.scalars(
            select(Company).where(Company.id.in_({relation.affected_company_id for relation in relations}))
        )} if relations else {}
        stocks_by_company: dict[uuid.UUID, list[Stock]] = defaultdict(list)
        if companies:
            for stock in self._session.scalars(
                select(Stock)
                .where(Stock.company_id.in_(companies))
                .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            ):
                stocks_by_company[stock.company_id].append(stock)
        observations_by_relation: dict[uuid.UUID, list[CompanyImpactObservation]] = defaultdict(list)
        if relation_ids:
            for row in self._session.scalars(select(CompanyImpactObservation).where(CompanyImpactObservation.relation_id.in_(relation_ids))):
                observations_by_relation[row.relation_id].append(row)
        latest_reviews: dict[uuid.UUID, CompanyImpactRelationReview] = {}
        if relation_ids:
            for review in self._session.scalars(
                select(CompanyImpactRelationReview)
                .where(CompanyImpactRelationReview.relation_id.in_(relation_ids))
                .order_by(CompanyImpactRelationReview.created_at.desc())
            ):
                latest_reviews.setdefault(review.relation_id, review)
        stock_by_id = {
            stock.id: stock for stocks in stocks_by_company.values() for stock in stocks
        }
        holdings_by_stock: dict[uuid.UUID, list[HoldingDisclosure]] = defaultdict(list)
        funds: dict[uuid.UUID, Fund] = {}
        if stock_by_id:
            holding_rows = list(self._session.execute(
                select(HoldingDisclosure, Fund)
                .join(Fund, Fund.id == HoldingDisclosure.fund_id)
                .where(HoldingDisclosure.stock_id.in_(stock_by_id))
            ))
            for holding, fund in holding_rows:
                if is_china_public_fund(fund.code):
                    holdings_by_stock[holding.stock_id].append(holding)
                    funds[fund.id] = fund
        latest_assessment: dict[uuid.UUID, EventImpactHypothesisAssessment] = {}
        if hypothesis_ids:
            for assessment in self._session.scalars(
                select(EventImpactHypothesisAssessment)
                .where(EventImpactHypothesisAssessment.hypothesis_id.in_(hypothesis_ids))
                .order_by(EventImpactHypothesisAssessment.created_at.desc())
            ):
                latest_assessment.setdefault(assessment.hypothesis_id, assessment)
        relations_by_hypothesis: dict[uuid.UUID, list[dict]] = defaultdict(list)
        all_dates: list[date] = []
        for relation in relations:
            company = companies[relation.affected_company_id]
            review = latest_reviews.get(relation.id)
            effective_status = (
                "verified" if review and review.outcome == "accepted"
                else "rejected" if review and review.outcome == "rejected"
                else relation.status
            )
            observations = []
            for observation in observations_by_relation[relation.id]:
                if observation.as_of_date:
                    all_dates.append(observation.as_of_date)
                observations.append({
                    "kind": observation.kind, "status": observation.status,
                    "source_statement_id": str(observation.source_statement_id) if observation.source_statement_id else None,
                    "valuation_snapshot_id": str(observation.valuation_snapshot_id) if observation.valuation_snapshot_id else None,
                    "summary": observation.summary,
                    "as_of_date": observation.as_of_date.isoformat() if observation.as_of_date else None,
                })
            relation_stocks = stocks_by_company[company.id] if company.type == "listed" else []
            as_of = max(
                (obs.as_of_date for obs in observations_by_relation[relation.id] if obs.as_of_date),
                default=None,
            )
            cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc) if as_of else None
            by_fund: dict[uuid.UUID, list[HoldingDisclosure]] = defaultdict(list)
            latest_visible: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
            for stock in relation_stocks:
                for holding in holdings_by_stock.get(stock.id, []):
                    published_at = holding.published_at
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    else:
                        published_at = published_at.astimezone(timezone.utc)
                    if cutoff is not None and published_at > cutoff:
                        continue
                    key = (holding.fund_id, holding.stock_id)
                    current = latest_visible.get(key)
                    if current is None or (holding.report_period, published_at) > (
                        current.report_period,
                        current.published_at.replace(tzinfo=timezone.utc) if current.published_at.tzinfo is None else current.published_at.astimezone(timezone.utc),
                    ):
                        latest_visible[key] = holding
            for holding in latest_visible.values():
                by_fund[holding.fund_id].append(holding)
            fund_rows = []
            for fund_id, holdings in by_fund.items():
                fund = funds[fund_id]
                covered = {holding.stock_id for holding in holdings}
                ratio = len(covered) / len(relation_stocks) if relation_stocks else 0
                latest = max(holdings, key=lambda row: (row.report_period, row.published_at))
                stale = bool(as_of and (as_of - latest.report_period).days > 180)
                coverage_status = "stale" if stale else ("complete" if ratio >= 0.80 else "partial")
                computable = coverage_status == "complete"
                fund_rows.append({"fund_id": str(fund.id), "fund_code": fund.code, "fund_name": fund.name,
                                  "report_period": latest.report_period.isoformat(), "published_at": latest.published_at.isoformat(),
                                  "source": latest.source, "coverage_ratio": ratio, "coverage_status": coverage_status,
                                  "computable": computable, "exposure": str(sum((row.weight for row in holdings), 0)) if computable else None})
            relations_by_hypothesis[relation.hypothesis_id].append({
                "relation_id": str(relation.id), "company_id": str(company.id), "company_name": company.name,
                "company_type": company.type, "relation_kind": relation.relation_kind,
                "direction": relation.direction, "mechanism": relation.mechanism,
                "status": relation.status, "effective_status": effective_status,
                "source_statement_id": str(relation.source_statement_id) if relation.source_statement_id else None,
                "review": None if review is None else {"outcome": review.outcome, "reason": review.reason, "reviewer": review.reviewer, "created_at": review.created_at.isoformat()},
                "stocks": [{"stock_id": str(stock.id), "code": stock.code, "name": stock.name, "market": stock.market} for stock in relation_stocks],
                "observations": observations,
                "fund_exposure": fund_rows,
            })
        factors, alternatives = [], []
        for hypothesis in hypotheses:
            assessment = latest_assessment.get(hypothesis.id)
            item = {"hypothesis_id": str(hypothesis.id), "statement": hypothesis.statement, "rank": hypothesis.rank,
                    "classification": assessment.classification if assessment else hypothesis.classification,
                    "score_components": assessment.score_components if assessment else hypothesis.score_components,
                    "explanation": assessment.explanation if assessment else hypothesis.explanation,
                    "relations": relations_by_hypothesis[hypothesis.id],
                    "funds": [fund for relation in relations_by_hypothesis[hypothesis.id] for fund in relation["fund_exposure"]]}
            (alternatives if item["classification"] == "alternative" else factors).append(item)
        return EventImpactTraceDTO(scope_version=scope.version, as_of=max(all_dates).isoformat() if all_dates else None,
                                   factors=factors, alternatives=alternatives,
                                   progress={"hypotheses": len(hypotheses), "relations": len(relations)})
