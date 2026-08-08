"""Ledger-only 1d/5d China market verification for report claims.

The service records observations, not causal conclusions.  It never calls a
market provider: providers must append their normalized values to the ledger
before this service may consume them.  This prevents a report graph from
quietly mixing an auditable report with opaque live-data responses.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Sequence
from unicodedata import normalize

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    CaseDocumentVersion,
    ChinaIndustryIndex,
    ChinaIndustryIndexMembership,
    ChinaIndustryIndexSnapshot,
    Company,
    DocumentVersion,
    Fund,
    HoldingDisclosure,
    SourceSpan,
    SourceStatement,
    Stock,
    ValuationSnapshot,
)
from app.models.report_research import (
    ReportClaim,
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportFundExposure,
    ReportRelation,
)
from app.services.china_market_data import (
    CHINA_A_SHARE_MARKETS,
    ChinaMarketData,
    LedgerChinaMarketData,
    REPORT_PEER_METRICS_1D,
    REPORT_PEER_METRICS_5D,
    REPORT_TARGET_METRICS_1D,
    REPORT_TARGET_METRICS_5D,
    is_china_public_fund,
)


_WINDOWS: tuple[tuple[str, int], ...] = (("1d", 0), ("5d", 4))


def _before_report_market_unique_insert(_model, _key: str) -> None:
    """Test seam for the database unique-key collection race."""


@dataclass(frozen=True)
class MarketImpactResult:
    windows: dict[str, str]
    trading_days: dict[str, date]
    gaps: tuple[str, ...]
    observations: tuple[ReportMarketObservation, ...]
    confounders: tuple[ReportMarketConfounder, ...] = ()
    fund_exposures: tuple[ReportFundExposure, ...] = ()


@dataclass(frozen=True)
class _Target:
    relation: ReportRelation
    company: Company


@dataclass(frozen=True)
class _Peer:
    relation: ReportRelation
    company: Company


class ReportMarketImpactService:
    """Append point-in-time China A-share observations for one report claim."""

    def __init__(
        self, session: Session, *, market_data: ChinaMarketData | None = None,
        output_slot=None,
    ) -> None:
        self._session = session
        self._market_data = market_data or LedgerChinaMarketData(session)
        self._output_slot = output_slot
        self._existing_records: dict[type, dict[str, object]] = {}

    def collect(self, claim_id: uuid.UUID) -> MarketImpactResult:
        claim, document = self._claim_and_document(claim_id)
        self._prime_existing_records(claim.id)
        if document.published_at is None:
            return MarketImpactResult(
                windows={},
                trading_days={},
                gaps=("缺少公开时间，未计算市场反应",),
                observations=(),
            )

        trading_days = tuple(
            self._market_data.trading_days_after(
                after=document.published_at.date(), count=5
            )
        )
        targets, peers = self._mapped_china_entities(claim)
        stocks_by_company = self._china_stocks_by_company(
            [target.company for target in targets]
            + [peer.company for peer in peers]
        )
        industry_by_company = self._visible_industry_indexes(
            targets, available_at=document.published_at
        )
        if len(trading_days) < 5:
            observations = self._append_calendar_gap(claim, targets)
            return MarketImpactResult(
                windows={"1d": "insufficient", "5d": "insufficient"},
                trading_days={},
                gaps=("中国市场交易日账本不足，未计算 1 日/5 日市场反应",),
                observations=tuple(observations),
            )

        observations: list[ReportMarketObservation] = []
        confounders: list[ReportMarketConfounder] = []
        fund_exposures: list[ReportFundExposure] = []
        gaps: list[str] = []
        windows: dict[str, str] = {}
        window_days: dict[str, date] = {}
        target_stock_ids = {
            stock.id
            for target in targets
            for stock in stocks_by_company.get(target.company.id, ())
        }
        holdings_by_stock = self._visible_fund_holdings(
            target_stock_ids, as_of=document.published_at.date()
        )
        for window, index in _WINDOWS:
            as_of = trading_days[index]
            window_days[window] = as_of
            status, created, window_gaps = self._collect_window(
                claim=claim,
                targets=targets,
                peers=peers,
                industry_by_company=industry_by_company,
                stocks_by_company=stocks_by_company,
                window=window,
                as_of=as_of,
            )
            windows[window] = status
            observations.extend(created)
            gaps.extend(window_gaps)
            confounders.extend(
                self._collect_confounders(
                    claim=claim,
                    report_document_id=document.id,
                    window=window,
                    start=document.published_at.date(),
                    end=as_of,
                )
            )
            fund_exposures.extend(
                self._collect_fund_exposures(
                    claim=claim,
                    targets=targets,
                    window=window,
                    as_of=document.published_at.date(),
                    stocks_by_company=stocks_by_company,
                    holdings_by_stock=holdings_by_stock,
                )
            )
        return MarketImpactResult(
            windows=windows,
            trading_days=window_days,
            gaps=tuple(dict.fromkeys(gaps)),
            observations=tuple(observations),
            confounders=tuple(confounders),
            fund_exposures=tuple(fund_exposures),
        )

    def _collect_fund_exposures(
        self,
        *,
        claim: ReportClaim,
        targets: Sequence[_Target],
        window: str,
        as_of: date,
        stocks_by_company: dict[uuid.UUID, list[Stock]],
        holdings_by_stock: dict[uuid.UUID, list[HoldingDisclosure]],
    ) -> list[ReportFundExposure]:
        """Map only visible China public-fund holdings for listed A-shares."""
        rows: list[ReportFundExposure] = []
        for target in targets:
            if target.company.type != "listed":
                # An unlisted company is a transmission node, never a fake
                # fund exposure.  Its market gap above is the complete record.
                continue
            for stock in stocks_by_company.get(target.company.id, ()):
                holdings = holdings_by_stock.get(stock.id, ())
                if not holdings:
                    rows.append(
                        self._append_fund_insufficient(
                            claim=claim,
                            relation=target.relation,
                            stock=stock,
                            window=window,
                            as_of=as_of,
                            summary=(
                                f"{stock.code} 在 {as_of.isoformat()} 没有当时可见的"
                                "中国公募基金持仓披露；不计算基金暴露。"
                            ),
                        )
                    )
                    continue
                for holding in holdings:
                    rows.append(
                        self._append_fund_holding(
                            claim=claim,
                            relation=target.relation,
                            stock=stock,
                            holding=holding,
                            window=window,
                            as_of=as_of,
                        )
                    )
        return rows

    def _append_fund_holding(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation,
        stock: Stock,
        holding,
        window: str,
        as_of: date,
    ) -> ReportFundExposure:
        key = self._key(claim.id, relation.id, window, "fund", stock.id, holding.id)
        return self._persist_unique(
            ReportFundExposure,
            key,
            lambda: ReportFundExposure(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=stock.id,
                fund_id=holding.fund_id,
                holding_disclosure_id=holding.id,
                window=window,
                as_of_date=as_of,
                status="verified",
                weight=holding.weight,
                summary=(
                    f"可见持仓披露：报告期 {holding.report_period.isoformat()}，"
                    f"权重 {holding.weight}，来源 {holding.source}。"
                ),
                collection_key=key,
            ),
        )

    def _append_fund_insufficient(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation,
        stock: Stock,
        window: str,
        as_of: date,
        summary: str,
    ) -> ReportFundExposure:
        key = self._key(claim.id, relation.id, window, "fund-gap", stock.id)
        return self._persist_unique(
            ReportFundExposure,
            key,
            lambda: ReportFundExposure(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=stock.id,
                fund_id=None,
                holding_disclosure_id=None,
                window=window,
                as_of_date=as_of,
                status="insufficient",
                weight=None,
                summary=summary,
                collection_key=key,
            ),
        )

    def _append_calendar_gap(
        self, claim: ReportClaim, targets: Sequence[_Target]
    ) -> list[ReportMarketObservation]:
        """Persist the missing-calendar boundary instead of returning silence."""
        rows: list[ReportMarketObservation] = []
        bindings: Sequence[_Target | None] = targets or (None,)
        for window, _index in _WINDOWS:
            for target in bindings:
                rows.append(
                    self._append_insufficient(
                        claim=claim,
                        relation=target.relation if target is not None else None,
                        stock=None,
                        window=window,
                        kind="target_market",
                        as_of=None,
                        metric_name="trading_calendar",
                        summary=(
                            "中国市场交易日账本不足，无法对齐发布后 "
                            f"{window} 窗口；未合成日期或市场数值。"
                        ),
                    )
                )
        return rows

    def _claim_and_document(
        self, claim_id: uuid.UUID
    ) -> tuple[ReportClaim, DocumentVersion]:
        row = self._session.execute(
            select(ReportClaim, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id == DocumentVersion.id,
            )
            .where(ReportClaim.id == claim_id)
            .where(CaseDocumentVersion.research_case_id == ReportClaim.research_case_id)
        ).one_or_none()
        if row is None:
            raise NotFoundError(f"report claim {claim_id} not found")
        return row

    def _mapped_china_entities(
        self, claim: ReportClaim
    ) -> tuple[tuple[_Target, ...], tuple[_Peer, ...]]:
        relations = list(
            self._session.scalars(
                select(ReportRelation).where(ReportRelation.claim_id == claim.id)
            )
        )
        company_ids = {
            company_id
            for relation in relations
            for company_id in (relation.subject_company_id, relation.object_company_id)
            if company_id is not None
        }
        companies = {
            company.id: company
            for company in self._session.scalars(
                select(Company).where(Company.id.in_(company_ids))
            )
        } if company_ids else {}
        names = {
            identity
            for relation in relations
            for identity in (
                self._company_identity(relation.subject_name),
                self._company_identity(relation.object_name),
            )
            if identity is not None
        }
        companies_by_name = {
            company.canonical_identity: company
            for company in self._session.scalars(
                select(Company).where(Company.canonical_identity.in_(names))
            )
        } if names else {}
        targets: list[_Target] = []
        peers: list[_Peer] = []
        seen_targets: set[tuple[uuid.UUID, uuid.UUID]] = set()
        seen_peers: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for relation in relations:
            subject = companies.get(relation.subject_company_id) or companies_by_name.get(
                self._company_identity(relation.subject_name)
            )
            obj = companies.get(relation.object_company_id) or companies_by_name.get(
                self._company_identity(relation.object_name)
            )
            if subject is not None:
                key = (relation.id, subject.id)
                if key not in seen_targets:
                    targets.append(_Target(relation, subject))
                    seen_targets.add(key)
            if relation.relation_kind == "competitor" and obj is not None:
                key = (relation.id, obj.id)
                if key not in seen_peers:
                    peers.append(_Peer(relation, obj))
                    seen_peers.add(key)
            elif obj is not None:
                key = (relation.id, obj.id)
                if key not in seen_targets:
                    targets.append(_Target(relation, obj))
                    seen_targets.add(key)
        return tuple(targets), tuple(peers)

    @staticmethod
    def _company_identity(name: str | None) -> str | None:
        """Return an exact ledger identity; callers never create a company."""
        if not name:
            return None
        return " ".join(normalize("NFKC", name).split()).casefold()

    def _collect_window(
        self,
        *,
        claim: ReportClaim,
        targets: Sequence[_Target],
        peers: Sequence[_Peer],
        industry_by_company: dict[uuid.UUID, ChinaIndustryIndex],
        stocks_by_company: dict[uuid.UUID, list[Stock]],
        window: str,
        as_of: date,
    ) -> tuple[str, list[ReportMarketObservation], list[str]]:
        observations: list[ReportMarketObservation] = []
        gaps: list[str] = []
        target_complete = True
        target_return_seen = False
        if not targets:
            observation = self._append_insufficient(
                claim=claim,
                relation=None,
                stock=None,
                window=window,
                kind="target_market",
                as_of=as_of,
                metric_name=None,
                summary="研报关系没有已映射公司，无法计算中国 A 股市场反应。",
            )
            observations.append(observation)
            return "insufficient", observations, [
                f"{window}：没有已映射的公司，未计算中国 A 股市场反应"
            ]

        expected_target = (
            REPORT_TARGET_METRICS_1D if window == "1d" else REPORT_TARGET_METRICS_5D
        )
        return_metric = "EVENT_RETURN_1D" if window == "1d" else "EVENT_RETURN_5D"
        industry_metric = (
            "INDUSTRY_RETURN_1D" if window == "1d" else "INDUSTRY_RETURN_5D"
        )
        industry_snapshots = self._industry_snapshots(
            list({index.id for index in industry_by_company.values()}),
            metric_name=industry_metric,
            as_of=as_of,
        )
        market_snapshots = self._market_snapshots(
            [
                stock.id
                for company_stocks in stocks_by_company.values()
                for stock in company_stocks
            ],
            metric_names=expected_target | (
                REPORT_PEER_METRICS_1D if window == "1d" else REPORT_PEER_METRICS_5D
            ),
            as_of=as_of,
        )
        for target in targets:
            stocks = stocks_by_company.get(target.company.id, ())
            if target.company.type != "listed" or not stocks:
                target_complete = False
                observation = self._append_insufficient(
                    claim=claim,
                    relation=target.relation,
                    stock=None,
                    window=window,
                    kind="target_market",
                    as_of=as_of,
                    metric_name=None,
                    summary=(
                        f"公司 {target.company.name} 未上市或没有可映射中国 A 股；"
                        "仅保留传导关系，不生成股票或基金数值。"
                    ),
                )
                observations.append(observation)
                gaps.append(f"{window}：{target.company.name} 未上市或缺少中国 A 股映射")
                continue
            for stock in stocks:
                snapshots = [
                    snapshot
                    for snapshot in market_snapshots.get(stock.id, ())
                    if snapshot.metric_name in expected_target
                ]
                actual_names = {snapshot.metric_name for snapshot in snapshots}
                target_return_seen = target_return_seen or return_metric in actual_names
                for snapshot in snapshots:
                    observations.append(
                        self._append_snapshot(
                            claim=claim,
                            relation=target.relation,
                            stock=stock,
                            snapshot=snapshot,
                            window=window,
                            kind="target_market",
                        )
                    )
                for metric in sorted(expected_target - actual_names):
                    target_complete = False
                    observations.append(
                        self._append_insufficient(
                            claim=claim,
                            relation=target.relation,
                            stock=stock,
                            window=window,
                            kind="target_market",
                            as_of=as_of,
                            metric_name=metric,
                            summary=(
                                f"{stock.code} 在 {as_of.isoformat()} 缺少账本指标 {metric}，"
                                "不合成市场数值。"
                            ),
                        )
                    )
                    gaps.append(f"{window}：{stock.code} 缺少 {metric}")

            industry_index = industry_by_company.get(target.company.id)
            industry_snapshot = (
                industry_snapshots.get(industry_index.id)
                if industry_index is not None
                else None
            )
            if industry_snapshot is None:
                observations.append(
                    self._append_insufficient(
                        claim=claim,
                        relation=target.relation,
                        stock=None,
                        window=window,
                        kind="industry_control",
                        as_of=as_of,
                        metric_name=industry_metric,
                        summary=(
                            f"公司 {target.company.name} 在研报公开时点没有"
                            "可审计行业指数映射或窗口快照；"
                            "不将目标股票指标冒充为行业控制。"
                        ),
                    )
                )
                gaps.append(f"{window}：{target.company.name} 缺少可审计行业指数控制")
            else:
                observations.append(
                    self._append_industry_snapshot(
                        claim=claim,
                        relation=target.relation,
                        index=industry_index,
                        snapshot=industry_snapshot,
                        window=window,
                    )
                )

        if not peers:
            observations.append(
                self._append_insufficient(
                    claim=claim,
                    relation=None,
                    stock=None,
                    window=window,
                    kind="peer_control",
                    as_of=as_of,
                    metric_name=None,
                    summary="研报未提供可映射中国 A 股同业关系，缺少同业控制。",
                )
            )
            gaps.append(f"{window}：缺少可映射中国 A 股同业控制")
        else:
            expected_peer = (
                REPORT_PEER_METRICS_1D if window == "1d" else REPORT_PEER_METRICS_5D
            )
            for peer in peers:
                stocks = stocks_by_company.get(peer.company.id, ())
                if peer.company.type != "listed" or not stocks:
                    observations.append(
                        self._append_insufficient(
                            claim=claim,
                            relation=peer.relation,
                            stock=None,
                            window=window,
                            kind="peer_control",
                            as_of=as_of,
                            metric_name=None,
                            summary=f"同业 {peer.company.name} 未上市或没有可映射中国 A 股。",
                        )
                    )
                    gaps.append(f"{window}：同业 {peer.company.name} 缺少中国 A 股映射")
                    continue
                for stock in stocks:
                    snapshots = [
                        snapshot
                        for snapshot in market_snapshots.get(stock.id, ())
                        if snapshot.metric_name in expected_peer
                    ]
                    observations.extend(
                        self._append_control_rows(
                            claim=claim,
                            relation=peer.relation,
                            stock=stock,
                            snapshots=snapshots,
                            expected=expected_peer,
                            window=window,
                            kind="peer_control",
                            as_of=as_of,
                            gaps=gaps,
                        )
                    )

        status = "verified" if target_complete and target_return_seen else "partial"
        if not target_return_seen:
            status = "insufficient"
        return status, observations, gaps

    def _china_stocks_by_company(
        self, companies: Sequence[Company]
    ) -> dict[uuid.UUID, list[Stock]]:
        company_ids = {company.id for company in companies}
        if not company_ids:
            return {}
        rows = self._session.scalars(
            select(Stock)
            .where(Stock.company_id.in_(company_ids))
            .where(Stock.market.in_(tuple(CHINA_A_SHARE_MARKETS)))
            .order_by(Stock.company_id, Stock.code)
        )
        stocks: dict[uuid.UUID, list[Stock]] = {}
        for stock in rows:
            stocks.setdefault(stock.company_id, []).append(stock)
        return stocks

    def _visible_fund_holdings(
        self, stock_ids: set[uuid.UUID], *, as_of: date
    ) -> dict[uuid.UUID, list[HoldingDisclosure]]:
        """One point-in-time, China-fund holding read for every target stock."""
        if not stock_ids:
            return {}
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        rows = self._session.execute(
            select(HoldingDisclosure, Fund)
            .join(Fund, Fund.id == HoldingDisclosure.fund_id)
            .where(HoldingDisclosure.stock_id.in_(stock_ids))
            .where(HoldingDisclosure.published_at <= cutoff)
            .where(HoldingDisclosure.acquired_at <= cutoff)
            .order_by(
                HoldingDisclosure.stock_id,
                HoldingDisclosure.fund_id,
                HoldingDisclosure.report_period.desc(),
                HoldingDisclosure.published_at.desc(),
            )
        )
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for holding, fund in rows:
            if is_china_public_fund(fund.code):
                latest.setdefault((holding.stock_id, holding.fund_id), holding)
        grouped: dict[uuid.UUID, list[HoldingDisclosure]] = {}
        for holding in latest.values():
            grouped.setdefault(holding.stock_id, []).append(holding)
        return grouped

    def _market_snapshots(
        self,
        stock_ids: Sequence[uuid.UUID],
        *,
        metric_names: frozenset[str],
        as_of: date,
    ) -> dict[uuid.UUID, list[ValuationSnapshot]]:
        """Read every target/peer metric for one window in a single query."""
        if not stock_ids:
            return {}
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        rows = self._session.scalars(
            select(ValuationSnapshot)
            .where(ValuationSnapshot.stock_id.in_(set(stock_ids)))
            .where(ValuationSnapshot.as_of_date == as_of)
            .where(ValuationSnapshot.metric_name.in_(metric_names))
            .where(ValuationSnapshot.available_at.is_not(None))
            .where(ValuationSnapshot.available_at <= cutoff)
            .order_by(
                ValuationSnapshot.stock_id,
                ValuationSnapshot.metric_name,
                ValuationSnapshot.available_at.desc(),
                ValuationSnapshot.created_at.desc(),
            )
        )
        latest: dict[tuple[uuid.UUID, str], ValuationSnapshot] = {}
        for snapshot in rows:
            latest.setdefault((snapshot.stock_id, snapshot.metric_name), snapshot)
        grouped: dict[uuid.UUID, list[ValuationSnapshot]] = {}
        for snapshot in latest.values():
            grouped.setdefault(snapshot.stock_id, []).append(snapshot)
        return grouped

    def _append_control_rows(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation,
        stock: Stock,
        snapshots: Sequence[ValuationSnapshot],
        expected: frozenset[str],
        window: str,
        kind: str,
        as_of: date,
        gaps: list[str],
    ) -> list[ReportMarketObservation]:
        rows = [
            self._append_snapshot(
                claim=claim,
                relation=relation,
                stock=stock,
                snapshot=snapshot,
                window=window,
                kind=kind,
            )
            for snapshot in snapshots
        ]
        actual_names = {snapshot.metric_name for snapshot in snapshots}
        for metric in sorted(expected - actual_names):
            rows.append(
                self._append_insufficient(
                    claim=claim,
                    relation=relation,
                    stock=stock,
                    window=window,
                    kind=kind,
                    as_of=as_of,
                    metric_name=metric,
                    summary=(
                        f"{stock.code} 在 {as_of.isoformat()} 缺少 {kind} 指标 {metric}，"
                        "不合成控制变量。"
                    ),
                )
            )
            gaps.append(f"{window}：{stock.code} 缺少 {kind} {metric}")
        return rows

    def _append_snapshot(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation,
        stock: Stock,
        snapshot: ValuationSnapshot,
        window: str,
        kind: str,
    ) -> ReportMarketObservation:
        key = self._key(
            claim.id, relation.id, window, kind, stock.id, snapshot.id, snapshot.metric_name
        )
        return self._persist_unique(
            ReportMarketObservation,
            key,
            lambda: ReportMarketObservation(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=stock.id,
                valuation_snapshot_id=snapshot.id,
                industry_index_snapshot_id=None,
                window=window,
                kind=kind,
                status="verified",
                as_of_date=snapshot.as_of_date,
                metric_name=snapshot.metric_name,
                summary=(
                    f"账本指标 {snapshot.metric_name}={snapshot.metric_value} "
                    f"({snapshot.source}; {snapshot.definition})"
                ),
                collection_key=key,
            ),
        )

    def _append_insufficient(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation | None,
        stock: Stock | None,
        window: str,
        kind: str,
        as_of: date,
        metric_name: str | None,
        summary: str,
    ) -> ReportMarketObservation:
        key = self._key(
            claim.id,
            relation.id if relation is not None else None,
            window,
            kind,
            stock.id if stock is not None else None,
            None,
            metric_name,
        )
        return self._persist_unique(
            ReportMarketObservation,
            key,
            lambda: ReportMarketObservation(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id if relation is not None else None,
                stock_id=stock.id if stock is not None else None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=None,
                window=window,
                kind=kind,
                status="insufficient",
                as_of_date=as_of,
                metric_name=metric_name,
                summary=summary,
                collection_key=key,
            ),
        )

    def _append_industry_snapshot(
        self,
        *,
        claim: ReportClaim,
        relation: ReportRelation,
        index: ChinaIndustryIndex,
        snapshot: ChinaIndustryIndexSnapshot,
        window: str,
    ) -> ReportMarketObservation:
        key = self._key(
            claim.id,
            relation.id,
            window,
            "industry_control",
            index.id,
            snapshot.id,
            snapshot.metric_name,
        )
        return self._persist_unique(
            ReportMarketObservation,
            key,
            lambda: ReportMarketObservation(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                report_relation_id=relation.id,
                stock_id=None,
                valuation_snapshot_id=None,
                industry_index_snapshot_id=snapshot.id,
                window=window,
                kind="industry_control",
                status="verified",
                as_of_date=snapshot.as_of_date,
                metric_name=snapshot.metric_name,
                summary=(
                    f"行业指数 {index.code}（{index.name}）账本指标 "
                    f"{snapshot.metric_name}={snapshot.metric_value} "
                    f"({snapshot.source}; {snapshot.definition})"
                ),
                collection_key=key,
            ),
        )

    def _visible_industry_indexes(
        self,
        targets: Sequence[_Target],
        *,
        available_at: datetime,
    ) -> dict[uuid.UUID, ChinaIndustryIndex]:
        """Latest visible, applicable index mapping for every target company."""
        company_ids = {target.company.id for target in targets}
        if not company_ids:
            return {}
        rows = self._session.execute(
            select(ChinaIndustryIndexMembership, ChinaIndustryIndex)
            .join(
                ChinaIndustryIndex,
                ChinaIndustryIndex.id == ChinaIndustryIndexMembership.industry_index_id,
            )
            .where(ChinaIndustryIndexMembership.company_id.in_(company_ids))
            .where(ChinaIndustryIndexMembership.available_at <= available_at)
            .where(
                (ChinaIndustryIndexMembership.applicable_from.is_(None))
                | (ChinaIndustryIndexMembership.applicable_from <= available_at.date())
            )
            .where(
                (ChinaIndustryIndexMembership.applicable_to.is_(None))
                | (ChinaIndustryIndexMembership.applicable_to >= available_at.date())
            )
            .order_by(
                ChinaIndustryIndexMembership.company_id,
                ChinaIndustryIndexMembership.available_at.desc(),
                ChinaIndustryIndexMembership.created_at.desc(),
            )
        )
        indexed: dict[uuid.UUID, ChinaIndustryIndex] = {}
        for membership, industry_index in rows:
            indexed.setdefault(membership.company_id, industry_index)
        return indexed

    def _industry_snapshots(
        self,
        index_ids: Sequence[uuid.UUID],
        *,
        metric_name: str,
        as_of: date,
    ) -> dict[uuid.UUID, ChinaIndustryIndexSnapshot]:
        if not index_ids:
            return {}
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
        rows = self._session.scalars(
            select(ChinaIndustryIndexSnapshot)
            .where(ChinaIndustryIndexSnapshot.industry_index_id.in_(index_ids))
            .where(ChinaIndustryIndexSnapshot.as_of_date == as_of)
            .where(ChinaIndustryIndexSnapshot.metric_name == metric_name)
            .where(ChinaIndustryIndexSnapshot.available_at <= cutoff)
            .order_by(
                ChinaIndustryIndexSnapshot.industry_index_id,
                ChinaIndustryIndexSnapshot.available_at.desc(),
                ChinaIndustryIndexSnapshot.created_at.desc(),
            )
        )
        snapshots: dict[uuid.UUID, ChinaIndustryIndexSnapshot] = {}
        for snapshot in rows:
            snapshots.setdefault(snapshot.industry_index_id, snapshot)
        return snapshots

    def _prime_existing_records(self, claim_id: uuid.UUID) -> None:
        """Avoid a lookup query per immutable output on repeat/bulk collection."""
        self._existing_records = {
            model: {
                row.collection_key: row
                for row in self._session.scalars(
                    select(model).where(model.report_claim_id == claim_id)
                )
            }
            for model in (
                ReportMarketObservation,
                ReportMarketConfounder,
                ReportFundExposure,
            )
        }

    def _collect_confounders(
        self,
        *,
        claim: ReportClaim,
        report_document_id: uuid.UUID,
        window: str,
        start: date,
        end: date,
    ) -> list[ReportMarketConfounder]:
        end_at = datetime.combine(end, time.max, tzinfo=timezone.utc)
        rows = self._session.execute(
            select(SourceStatement, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id == DocumentVersion.id,
            )
            .where(CaseDocumentVersion.research_case_id == claim.research_case_id)
            .where(DocumentVersion.id != report_document_id)
            .where(DocumentVersion.published_at.is_not(None))
            .where(DocumentVersion.published_at >= datetime.combine(start, time.min, tzinfo=timezone.utc))
            .where(DocumentVersion.published_at <= end_at)
            .where(DocumentVersion.available_at <= end_at)
        )
        confounders: list[ReportMarketConfounder] = []
        for statement, document in rows:
            kind = self._confounder_kind(statement.normalized_text, document.title)
            if kind is None:
                continue
            key = self._key(claim.id, statement.id, window, kind)
            confounder = self._persist_unique(
                ReportMarketConfounder,
                key,
                lambda: ReportMarketConfounder(
                    research_case_id=claim.research_case_id,
                    report_claim_id=claim.id,
                    source_statement_id=statement.id,
                    window=window,
                    kind=kind,
                    as_of_date=document.published_at.date(),
                    summary=statement.normalized_text,
                    collection_key=key,
                ),
            )
            confounders.append(confounder)
        return confounders

    def _persist_unique(self, model, key: str, factory):
        """Insert one immutable collection row or safely re-read its winner.

        The unique collection key is the correctness boundary across workers.
        A failed insert is isolated in a savepoint so PostgreSQL's outer task
        transaction stays usable for the remaining collection records.
        """
        cached = self._existing_records.setdefault(model, {})
        existing = cached.get(key)
        if existing is not None:
            return existing
        try:
            with self._session.begin_nested():
                if self._output_slot is not None and not self._output_slot():
                    raise RuntimeError("report market task no longer owns its output slot")
                _before_report_market_unique_insert(model, key)
                created = factory()
                self._session.add(created)
                self._session.flush()
                cached[key] = created
            return created
        except IntegrityError:
            winner = self._session.scalar(
                select(model).where(model.collection_key == key)
            )
            if winner is None:
                raise
            cached[key] = winner
            return winner

    @staticmethod
    def _confounder_kind(text: str, title: str | None) -> str | None:
        haystack = f"{title or ''} {text}"
        if any(token in haystack for token in ("财报", "业绩", "业绩预告", "业绩快报")):
            return "earnings"
        if any(token in haystack for token in ("公告", "披露", "董事会", "交易所")):
            return "announcement"
        if any(token in haystack for token in ("政策", "监管", "条例", "部委")):
            return "policy"
        if any(token in haystack for token in ("新闻", "报道称", "消息")):
            return "news"
        return None

    @staticmethod
    def _key(*parts: object) -> str:
        payload = "|".join("" if part is None else str(part) for part in parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
