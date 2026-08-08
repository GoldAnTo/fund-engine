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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    SourceSpan,
    SourceStatement,
    Stock,
    ValuationSnapshot,
)
from app.models.report_research import (
    ReportClaim,
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportRelation,
)
from app.services.china_market_data import (
    ChinaMarketData,
    LedgerChinaMarketData,
    REPORT_INDUSTRY_METRICS_1D,
    REPORT_INDUSTRY_METRICS_5D,
    REPORT_PEER_METRICS_1D,
    REPORT_PEER_METRICS_5D,
    REPORT_TARGET_METRICS_1D,
    REPORT_TARGET_METRICS_5D,
    is_china_a_share,
)


_WINDOWS: tuple[tuple[str, int], ...] = (("1d", 0), ("5d", 4))


@dataclass(frozen=True)
class MarketImpactResult:
    windows: dict[str, str]
    trading_days: dict[str, date]
    gaps: tuple[str, ...]
    observations: tuple[ReportMarketObservation, ...]
    confounders: tuple[ReportMarketConfounder, ...] = ()


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
        self, session: Session, *, market_data: ChinaMarketData | None = None
    ) -> None:
        self._session = session
        self._market_data = market_data or LedgerChinaMarketData(session)

    def collect(self, claim_id: uuid.UUID) -> MarketImpactResult:
        claim, document = self._claim_and_document(claim_id)
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
        if len(trading_days) < 5:
            return MarketImpactResult(
                windows={},
                trading_days={},
                gaps=("中国市场交易日账本不足，未计算 1 日/5 日市场反应",),
                observations=(),
            )

        targets, peers = self._mapped_china_entities(claim)
        observations: list[ReportMarketObservation] = []
        confounders: list[ReportMarketConfounder] = []
        gaps: list[str] = []
        windows: dict[str, str] = {}
        window_days: dict[str, date] = {}
        for window, index in _WINDOWS:
            as_of = trading_days[index]
            window_days[window] = as_of
            status, created, window_gaps = self._collect_window(
                claim=claim,
                targets=targets,
                peers=peers,
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
                    start=trading_days[0],
                    end=as_of,
                )
            )
        return MarketImpactResult(
            windows=windows,
            trading_days=window_days,
            gaps=tuple(dict.fromkeys(gaps)),
            observations=tuple(observations),
            confounders=tuple(confounders),
        )

    def _claim_and_document(
        self, claim_id: uuid.UUID
    ) -> tuple[ReportClaim, DocumentVersion]:
        row = self._session.execute(
            select(ReportClaim, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .where(ReportClaim.id == claim_id)
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
        targets: list[_Target] = []
        peers: list[_Peer] = []
        seen_targets: set[tuple[uuid.UUID, uuid.UUID]] = set()
        seen_peers: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for relation in relations:
            subject = companies.get(relation.subject_company_id)
            obj = companies.get(relation.object_company_id)
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

    def _collect_window(
        self,
        *,
        claim: ReportClaim,
        targets: Sequence[_Target],
        peers: Sequence[_Peer],
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
        for target in targets:
            stocks = self._china_stocks(target.company)
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
                snapshots = self._market_data.report_target_observations(
                    stock, as_of=as_of, window=window
                )
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

                industry = self._market_data.report_industry_observations(
                    stock, as_of=as_of, window=window
                )
                observations.extend(
                    self._append_control_rows(
                        claim=claim,
                        relation=target.relation,
                        stock=stock,
                        snapshots=industry,
                        expected=(
                            REPORT_INDUSTRY_METRICS_1D
                            if window == "1d"
                            else REPORT_INDUSTRY_METRICS_5D
                        ),
                        window=window,
                        kind="industry_control",
                        as_of=as_of,
                        gaps=gaps,
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
                stocks = self._china_stocks(peer.company)
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
                    snapshots = self._market_data.report_peer_observations(
                        stock, as_of=as_of, window=window
                    )
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

    def _china_stocks(self, company: Company) -> list[Stock]:
        return [
            stock
            for stock in self._session.scalars(
                select(Stock).where(Stock.company_id == company.id)
            )
            if is_china_a_share(stock)
        ]

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
        existing = self._session.scalar(
            select(ReportMarketObservation).where(
                ReportMarketObservation.collection_key == key
            )
        )
        if existing is not None:
            return existing
        observation = ReportMarketObservation(
            research_case_id=claim.research_case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id,
            stock_id=stock.id,
            valuation_snapshot_id=snapshot.id,
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
        )
        self._session.add(observation)
        return observation

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
        existing = self._session.scalar(
            select(ReportMarketObservation).where(
                ReportMarketObservation.collection_key == key
            )
        )
        if existing is not None:
            return existing
        observation = ReportMarketObservation(
            research_case_id=claim.research_case_id,
            report_claim_id=claim.id,
            report_relation_id=relation.id if relation is not None else None,
            stock_id=stock.id if stock is not None else None,
            valuation_snapshot_id=None,
            window=window,
            kind=kind,
            status="insufficient",
            as_of_date=as_of,
            metric_name=metric_name,
            summary=summary,
            collection_key=key,
        )
        self._session.add(observation)
        return observation

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
            existing = self._session.scalar(
                select(ReportMarketConfounder).where(
                    ReportMarketConfounder.collection_key == key
                )
            )
            if existing is not None:
                confounders.append(existing)
                continue
            confounder = ReportMarketConfounder(
                research_case_id=claim.research_case_id,
                report_claim_id=claim.id,
                source_statement_id=statement.id,
                window=window,
                kind=kind,
                as_of_date=document.published_at.date(),
                summary=statement.normalized_text,
                collection_key=key,
            )
            self._session.add(confounder)
            confounders.append(confounder)
        return confounders

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
