"""Source-backed, scope-bound company impact candidate resolution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Callable, Protocol, Sequence
from unicodedata import normalize

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.errors import NotFoundError, ValidationFailedError
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyImpactRelationReview,
    CompanyIdentityAlias,
    EventImpactRefreshClaim,
    EventImpactHypothesis,
    EventImpactHypothesisAssessment,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.events import DomainEvent
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    EvidenceLink,
    Fund,
    HoldingDisclosure,
    SourceSpan,
    SourceStatement,
    Stock,
    Thesis,
    ValuationSnapshot,
)
from app.models.operational import ResearchRun, ResearchTask, TaskItem
from app.services.source_admission import classify_source
from app.repositories.outbox import emit_event
from app.services.china_market_data import (
    CHINA_A_SHARE_MARKETS,
    ChinaMarketData,
    LedgerChinaMarketData,
    is_china_public_fund,
)
from app.services.event_research_scope_evidence import lock_event_research_lifecycle


_INITIAL_REFRESH_KEY_SUFFIX = "initial"
_REFRESH_REQUEST_EVENT_TYPE = "event_impact_refresh_requested"


def _before_refresh_claim_insert() -> None:
    """Test seam for proving the unique-claim race without sleeps."""


def _before_refresh_claim_lock() -> None:
    """Test seam for synchronizing competing worker execution attempts."""


def _before_company_insert() -> None:
    """Test seam for proving cross-case canonical-company races without sleeps."""


def _before_impact_data_relation_lock() -> None:
    """Test seam for concurrent data-collection lock coverage."""


def _before_company_impact_review_insert() -> None:
    """Test seam for proving the unique review-task race without sleeps."""


def _before_company_impact_review_schedule_lock() -> None:
    """Test seam for scope replacement versus a stale review handoff."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware_datetime(value: datetime) -> datetime:
    """Interpret naive ledger timestamps as UTC, matching instrument writes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ResolvedImpactCompany:
    company_name: str
    type: str
    relation_kind: str
    direction: str
    mechanism: str
    source_statement_id: uuid.UUID | None


class ImpactResolver(Protocol):
    def resolve(
        self,
        *,
        factor_statement: str,
        statements: Sequence[SourceStatement],
    ) -> Sequence[ResolvedImpactCompany]: ...


class _EmptyImpactResolver:
    """Production-safe default until a prompted resolver is introduced."""

    def resolve(
        self,
        *,
        factor_statement: str,
        statements: Sequence[SourceStatement],
    ) -> Sequence[ResolvedImpactCompany]:
        return ()


@dataclass(frozen=True)
class ImpactRefreshResult:
    hypotheses_created: int
    relations_created: int
    source_rejected_count: int
    unresolved_candidate_count: int


@dataclass(frozen=True)
class _ResolvedCandidate:
    hypothesis: EventImpactHypothesis
    candidate: ResolvedImpactCompany


@dataclass(frozen=True)
class _RefreshFactor:
    statement: str
    position: int


@dataclass(frozen=True)
class _ResolvedFactor:
    factor: _RefreshFactor
    candidates: Sequence[ResolvedImpactCompany]


@dataclass(frozen=True)
class _PreparedImpactRefresh:
    case_id: uuid.UUID
    scope_id: uuid.UUID
    refresh_key: str
    factors: Sequence[_RefreshFactor]
    admissible_statements: Sequence[SourceStatement]
    admissible_by_id: dict[uuid.UUID, SourceStatement]
    statement_dates: dict[uuid.UUID, date]


@dataclass(frozen=True)
class ImpactDataCollectionResult:
    observations_created: int
    insufficient_kinds: tuple[str, ...]


@dataclass(frozen=True)
class FundImpactPosition:
    stock_id: uuid.UUID
    stock_code: str
    stock_name: str
    weight: Decimal
    report_period: date
    published_at: datetime
    source: str


@dataclass(frozen=True)
class FundImpactExposure:
    fund_id: uuid.UUID
    fund_code: str
    fund_name: str
    report_period: date | None
    published_at: datetime | None
    source: str | None
    covered_positions: tuple[FundImpactPosition, ...]
    coverage_ratio: Decimal
    coverage_status: str
    computable: bool
    exposure: Decimal | None


class EventImpactResearchService:
    def __init__(
        self,
        session: Session,
        resolver: ImpactResolver | None = None,
        *,
        preflight_session_factory: Callable[[], Session] | None = None,
        market_data: ChinaMarketData | None = None,
    ) -> None:
        self._session = session
        self._resolver = resolver or _EmptyImpactResolver()
        self._market_data = market_data or LedgerChinaMarketData(session)
        if preflight_session_factory is not None:
            self._preflight_session_factory = preflight_session_factory
        else:
            bind = session.get_bind()
            if isinstance(bind, Connection):
                bind = bind.engine
            self._preflight_session_factory = sessionmaker(
                bind=bind,
                future=True,
                expire_on_commit=False,
            )

    def refresh(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
        refresh_key: str | None = None,
        output_slot=None,
    ) -> ImpactRefreshResult:
        # Claim/input loading belongs to a short private transaction.  The
        # injected Session may be an API command's outer unit of work, so this
        # service must never commit or roll it back merely to call a provider.
        with self._preflight_session_factory() as preflight_session:
            preflight = EventImpactResearchService(
                preflight_session,
                resolver=self._resolver,
                preflight_session_factory=self._preflight_session_factory,
                market_data=self._market_data,
            )
            prepared = preflight._prepare_provider_refresh(
                case_id,
                scope_version_id=scope_version_id,
                refresh_key=refresh_key,
            )
        if prepared is None:
            return ImpactRefreshResult(0, 0, 0, 0)

        # Providers are deliberately run before any refresh output is added
        # to this Session.  If one factor fails, the task failure commit cannot
        # accidentally publish the hypotheses from factors that happened to
        # resolve first.
        resolved_factors: list[_ResolvedFactor] = []
        for factor in prepared.factors:
            candidates = list(
                self._resolver.resolve(
                    factor_statement=factor.statement,
                    statements=prepared.admissible_statements,
                )
            )
            resolved_factors.append(_ResolvedFactor(factor, candidates))

        if output_slot is not None and not output_slot():
            return ImpactRefreshResult(0, 0, 0, 0)

        # This is the durable execution mutex, deliberately acquired only
        # after the provider returns and after the caller owns its output slot.
        # A competing worker may have published while this provider ran, so
        # recheck the completed append-only trace under the claim lock.
        _before_refresh_claim_lock()
        self._session.scalar(
            select(EventImpactRefreshClaim)
            .where(EventImpactRefreshClaim.scope_version_id == prepared.scope_id)
            .where(EventImpactRefreshClaim.refresh_key == prepared.refresh_key)
            .with_for_update()
        )
        completed = self._session.scalars(
            select(EventImpactHypothesis).where(
                EventImpactHypothesis.research_case_id == case_id,
                EventImpactHypothesis.scope_version_id == prepared.scope_id,
            )
        )
        if any(
            hypothesis.score_components.get("refresh_key") == prepared.refresh_key
            for hypothesis in completed
        ):
            return ImpactRefreshResult(0, 0, 0, 0)

        return self._append_refresh_output(
            case_id=prepared.case_id,
            scope_id=prepared.scope_id,
            refresh_key=prepared.refresh_key,
            resolved_factors=resolved_factors,
            admissible_by_id=prepared.admissible_by_id,
            statement_dates=prepared.statement_dates,
        )

    def _prepare_provider_refresh(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None,
        refresh_key: str | None,
    ) -> _PreparedImpactRefresh | None:
        """Commit the private claim/input transaction before provider work."""
        scope = self._scope_for(case_id, scope_version_id)
        refresh_key = refresh_key or self._initial_refresh_key(scope.id)
        self._claim_refresh(case_id, scope.id, refresh_key)
        existing_hypotheses = self._session.scalars(
            select(EventImpactHypothesis).where(
                EventImpactHypothesis.research_case_id == case_id,
                EventImpactHypothesis.scope_version_id == scope.id,
            )
        )
        if any(
            hypothesis.score_components.get("refresh_key") == refresh_key
            for hypothesis in existing_hypotheses
        ):
            self._session.commit()
            return None
        factors = [
            _RefreshFactor(statement=factor.statement, position=factor.position)
            for factor in self._session.scalars(
                select(EventResearchScopeFactor)
                .where(EventResearchScopeFactor.scope_version_id == scope.id)
                .order_by(EventResearchScopeFactor.position)
            )
        ]
        admissible_records = self._admissible_statement_records(case_id)
        admissible_statements = [statement for statement, _ in admissible_records]
        prepared = _PreparedImpactRefresh(
            case_id=case_id,
            scope_id=scope.id,
            refresh_key=refresh_key,
            factors=factors,
            admissible_statements=admissible_statements,
            admissible_by_id={statement.id: statement for statement in admissible_statements},
            statement_dates={
                statement.id: statement.observed_period
                or (
                    document.published_at.date()
                    if document.published_at
                    else document.available_at.date()
                )
                for statement, document in admissible_records
            },
        )
        for statement in admissible_statements:
            self._session.expunge(statement)
        self._session.commit()
        return prepared

    def collect_data(
        self,
        relation_id: uuid.UUID,
        *,
        as_of: date,
        kinds: Sequence[str] | None = None,
    ) -> ImpactDataCollectionResult:
        """Append ledger-backed operating/market/peer evidence for one relation.

        This service never calls a provider.  Missing ledger coverage is an
        explicit ``insufficient`` observation, not a synthetic market fact.
        """
        _before_impact_data_relation_lock()
        relation = self._session.scalar(
            select(CompanyImpactRelation)
            .where(CompanyImpactRelation.id == relation_id)
            .with_for_update()
        )
        if relation is None:
            raise NotFoundError(f"impact relation {relation_id} not found")
        company = self._session.get(Company, relation.affected_company_id)
        if company is None:
            raise ValidationFailedError("impact relation company not found")
        existing_snapshot_ids = {
            snapshot_id
            for snapshot_id in self._session.scalars(
                select(CompanyImpactObservation.valuation_snapshot_id)
                .where(CompanyImpactObservation.relation_id == relation.id)
                .where(CompanyImpactObservation.as_of_date == as_of)
                .where(CompanyImpactObservation.kind.in_(("operating", "market", "peer_control")))
                .where(CompanyImpactObservation.valuation_snapshot_id.is_not(None))
            )
        }
        existing_insufficient_kinds = {
            kind
            for kind in self._session.scalars(
                select(CompanyImpactObservation.kind)
                .where(CompanyImpactObservation.relation_id == relation.id)
                .where(CompanyImpactObservation.as_of_date == as_of)
                .where(CompanyImpactObservation.status == "insufficient")
                .where(CompanyImpactObservation.valuation_snapshot_id.is_(None))
            )
        }
        existing_verified_kinds = {
            kind
            for kind in self._session.scalars(
                select(CompanyImpactObservation.kind)
                .where(CompanyImpactObservation.relation_id == relation.id)
                .where(CompanyImpactObservation.as_of_date == as_of)
                .where(CompanyImpactObservation.status == "verified")
                .where(CompanyImpactObservation.valuation_snapshot_id.is_not(None))
            )
        }
        requested_kinds = tuple(kinds or ("operating", "market", "peer_control"))
        allowed_kinds = {"operating", "market", "peer_control"}
        if not set(requested_kinds) <= allowed_kinds:
            raise ValidationFailedError("unsupported impact data collection kind")
        if company.type != "listed":
            created_kinds = []
            # Unlisted companies are explicit operating-data gaps only; they
            # are never made tradable through market/peer observations.
            for kind in ("operating",) if "operating" in requested_kinds else ():
                if kind in existing_insufficient_kinds:
                    continue
                self._append_insufficient_data_observation(
                    relation,
                    kind,
                    as_of,
                    f"Company is unlisted; no A-share {kind} ledger coverage.",
                )
                created_kinds.append(kind)
            return ImpactDataCollectionResult(
                observations_created=len(created_kinds),
                insufficient_kinds=tuple(created_kinds),
            )

        stocks = list(
            self._session.scalars(
                select(Stock)
                .where(Stock.company_id == company.id)
                .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            )
        )
        sources = (
            ("operating", self._market_data.operating_observations),
            ("market", self._market_data.market_observations),
            ("peer_control", self._market_data.peer_observations),
        )
        created = 0
        insufficient: list[str] = []
        for kind, loader in sources:
            if kind not in requested_kinds:
                continue
            snapshots = {
                snapshot.id: snapshot
                for stock in stocks
                for snapshot in loader(stock, as_of=as_of)
            }
            if not snapshots:
                if kind not in existing_insufficient_kinds and kind not in existing_verified_kinds:
                    self._append_insufficient_data_observation(
                        relation,
                        kind,
                        as_of,
                        f"No ledger-backed {kind} metric is available by {as_of.isoformat()}.",
                    )
                    created += 1
                    insufficient.append(kind)
                continue
            for snapshot in snapshots.values():
                if snapshot.id in existing_snapshot_ids:
                    continue
                self._session.add(
                    CompanyImpactObservation(
                        relation_id=relation.id,
                        kind=kind,
                        status="verified",
                        source_statement_id=None,
                        valuation_snapshot_id=snapshot.id,
                        summary=(
                            f"Ledger {kind} metric {snapshot.metric_name}="
                            f"{snapshot.metric_value} ({snapshot.source}; "
                            f"{snapshot.definition})"
                        ),
                        as_of_date=as_of,
                        created_at=_utcnow(),
                    )
                )
                created += 1
        return ImpactDataCollectionResult(created, tuple(insufficient))

    def fund_exposure(
        self, relation_id: uuid.UUID, *, as_of: date
    ) -> list[FundImpactExposure]:
        """Return point-in-time, coverage-gated fund exposure for a relation."""
        relation = self._session.get(CompanyImpactRelation, relation_id)
        if relation is None:
            raise NotFoundError(f"impact relation {relation_id} not found")
        if relation.status != "verified":
            return []
        company = self._session.get(Company, relation.affected_company_id)
        if company is None or company.type != "listed":
            return []
        stocks = list(
            self._session.scalars(
                select(Stock)
                .where(Stock.company_id == company.id)
                .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            )
        )
        if not stocks:
            return []
        stock_by_id = {stock.id: stock for stock in stocks}
        holdings = self._market_data.fund_holdings(list(stock_by_id), as_of=as_of)
        by_fund: dict[uuid.UUID, list[HoldingDisclosure]] = {}
        for holding in holdings:
            by_fund.setdefault(holding.fund_id, []).append(holding)
        funds = {
            fund.id: fund
            for fund in self._session.scalars(select(Fund).where(Fund.id.in_(by_fund)))
        } if by_fund else {}
        results: list[FundImpactExposure] = []
        for fund_id, disclosures in by_fund.items():
            fund = funds.get(fund_id)
            if fund is None or not is_china_public_fund(fund.code):
                continue
            positions = tuple(
                FundImpactPosition(
                    stock_id=disclosure.stock_id,
                    stock_code=stock_by_id[disclosure.stock_id].code,
                    stock_name=stock_by_id[disclosure.stock_id].name,
                    weight=disclosure.weight,
                    report_period=disclosure.report_period,
                    published_at=disclosure.published_at,
                    source=disclosure.source,
                )
                for disclosure in sorted(disclosures, key=lambda item: item.weight, reverse=True)
            )
            coverage_ratio = Decimal(len({row.stock_id for row in positions})) / Decimal(len(stocks))
            latest = max(disclosures, key=lambda item: (item.report_period, item.published_at))
            stale = (as_of - latest.report_period).days > 180
            coverage_status = "stale" if stale else ("complete" if coverage_ratio >= Decimal("0.80") else "partial")
            computable = coverage_status == "complete"
            results.append(
                FundImpactExposure(
                    fund_id=fund.id,
                    fund_code=fund.code,
                    fund_name=fund.name,
                    report_period=latest.report_period,
                    published_at=latest.published_at,
                    source=latest.source,
                    covered_positions=positions,
                    coverage_ratio=coverage_ratio,
                    coverage_status=coverage_status,
                    computable=computable,
                    exposure=sum((position.weight for position in positions), Decimal("0")) if computable else None,
                )
            )
        return sorted(results, key=lambda result: (result.exposure is None, result.fund_code))

    def run_stage(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID,
        thesis_id: uuid.UUID | None,
        stage: str,
        output_slot: Callable[[], bool] | None = None,
    ) -> dict[str, int | bool]:
        """Perform one exact-scope impact task at the worker output boundary.

        A stage is intentionally narrow: its ``ResearchTask`` records the
        thesis and exact scope, while the durable run/task output-slot check
        prevents a superseded scope from appending observations or assessments.
        """
        if stage not in {
            "impact_companies",
            "impact_operating",
            "impact_market",
            "impact_peer",
            "impact_fund",
            "impact_alternative",
        }:
            raise ValidationFailedError("unsupported impact research stage")
        if thesis_id is None:
            raise ValidationFailedError("impact stage requires a thesis")
        scope = self._scope_for(case_id, scope_version_id)
        thesis = self._session.get(Thesis, thesis_id)
        if thesis is None or thesis.research_case_id != case_id:
            raise ValidationFailedError("impact stage thesis does not belong to case")
        hypotheses = list(
            self._session.scalars(
                select(EventImpactHypothesis)
                .where(EventImpactHypothesis.research_case_id == case_id)
                .where(EventImpactHypothesis.scope_version_id == scope.id)
                .where(EventImpactHypothesis.statement == thesis.statement)
            )
        )
        relations = list(
            self._session.scalars(
                select(CompanyImpactRelation).where(
                    CompanyImpactRelation.hypothesis_id.in_(
                        [row.id for row in hypotheses]
                    )
                )
            )
        ) if hypotheses else []
        relation_ids = [relation.id for relation in relations]
        latest_reviews: dict[uuid.UUID, CompanyImpactRelationReview] = {}
        if relation_ids:
            for review in self._session.scalars(
                select(CompanyImpactRelationReview)
                .where(CompanyImpactRelationReview.relation_id.in_(relation_ids))
                .order_by(CompanyImpactRelationReview.created_at.desc())
            ):
                latest_reviews.setdefault(review.relation_id, review)
        if output_slot is not None and not output_slot():
            return {"cancelled": True, "observations_created": 0}
        if stage == "impact_companies":
            return {
                "cancelled": False,
                "hypotheses": len(hypotheses),
                "relations": len(relation_ids),
            }
        if stage in {"impact_operating", "impact_market", "impact_peer"}:
            kind = {
                "impact_operating": "operating",
                "impact_market": "market",
                "impact_peer": "peer_control",
            }[stage]
            created = sum(
                self.collect_data(
                    relation_id, as_of=_utcnow().date(), kinds=(kind,)
                ).observations_created
                for relation_id in relation_ids
            )
            return {"cancelled": False, "observations_created": created}
        if stage == "impact_fund":
            # The classifier's bulk PIT helper loads stocks, holdings and
            # funds once for all relations.  A stage only needs auditable
            # coverage work/counts, not an N+1 presentation projection.
            as_of = _utcnow().date()
            coverage = self._bulk_fund_coverage(
                relations,
                {relation.id: as_of for relation in relations},
            )
            return {"cancelled": False, "fund_exposures": len(coverage)}
        assessments = self.classify(
            case_id,
            scope_version_id=scope.id,
            hypothesis_ids=[row.id for row in hypotheses],
            update_lifecycle=False,
        )
        return {"cancelled": False, "assessments_created": len(assessments)}

    def schedule_company_impact_reviews(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
        as_of: date | None = None,
    ) -> int:
        """Create human review work only for material, source-missing candidates.

        Material means that the candidate already resolves to an A-share and a
        Chinese public fund has a ledger-visible holding.  Unlisted candidates
        and candidates without that fund signal remain explicit research gaps,
        rather than generating noisy operational review tasks.
        """
        # Take the same case → lifecycle lock sequence used by a scope update.
        # The latest-scope check must occur *after* that lock: an old worker
        # may have started its handoff before replacement, but it must not
        # recreate review work once the successor commits.
        _before_company_impact_review_schedule_lock()
        lock_event_research_lifecycle(self._session, case_id)
        scope = self._scope_for(case_id, scope_version_id)
        current_scope_id = self._session.scalar(
            select(EventResearchScopeVersion.id)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if current_scope_id != scope.id:
            return 0
        cutoff = datetime.combine(
            as_of or _utcnow().date(), time.max, tzinfo=timezone.utc
        )
        admissible_ids = {
            statement.id
            for statement, _ in self._admissible_statement_records(case_id)
        }
        rows = self._session.execute(
            select(CompanyImpactRelation, Fund.code)
            .join(
                EventImpactHypothesis,
                EventImpactHypothesis.id == CompanyImpactRelation.hypothesis_id,
            )
            .join(Company, Company.id == CompanyImpactRelation.affected_company_id)
            .join(Stock, Stock.company_id == CompanyImpactRelation.affected_company_id)
            .join(HoldingDisclosure, HoldingDisclosure.stock_id == Stock.id)
            .join(Fund, Fund.id == HoldingDisclosure.fund_id)
            .where(EventImpactHypothesis.research_case_id == case_id)
            .where(EventImpactHypothesis.scope_version_id == scope.id)
            .where(CompanyImpactRelation.scope_version_id == scope.id)
            .where(CompanyImpactRelation.status == "candidate")
            .where(Company.type == "listed")
            .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
            .where(HoldingDisclosure.published_at <= cutoff)
        )
        candidates: dict[uuid.UUID, CompanyImpactRelation] = {}
        for relation, fund_code in rows:
            if relation.source_statement_id in admissible_ids:
                continue
            if is_china_public_fund(fund_code):
                candidates[relation.id] = relation
        existing_refs = set(
            self._session.scalars(
                select(TaskItem.ref_id)
                .where(TaskItem.task_type == "review_company_impact")
                .where(TaskItem.ref_type == "company_impact_relation")
                .where(TaskItem.scope_version_id == scope.id)
                .where(TaskItem.ref_id.in_(candidates))
            )
        ) if candidates else set()
        created = 0
        for relation in candidates.values():
            if relation.id in existing_refs:
                continue
            try:
                with self._session.begin_nested():
                    _before_company_impact_review_insert()
                    self._session.add(
                        TaskItem(
                            title="审核高影响公司传导",
                            description=(
                                "已解析 A 股且中国基金存在可见持仓，但该候选关系"
                                "缺少可采纳关系来源；请补充或审核来源后再确认传导。"
                            ),
                            task_type="review_company_impact",
                            ref_type="company_impact_relation",
                            ref_id=relation.id,
                            research_case_id=case_id,
                            scope_version_id=scope.id,
                            priority="high",
                            created_at=_utcnow(),
                        )
                    )
                    self._session.flush()
            except IntegrityError:
                # A concurrent scheduler inserted the same scope/ref key.
                # The unique constraint makes this a normal idempotent race.
                continue
            created += 1
        return created

    def close_superseded_company_impact_reviews(
        self, case_id: uuid.UUID, *, keep_scope_version_id: uuid.UUID
    ) -> int:
        """Hide open review work for prior scopes after a replacement commits."""
        result = self._session.execute(
            update(TaskItem)
            .where(TaskItem.research_case_id == case_id)
            .where(TaskItem.task_type == "review_company_impact")
            .where(TaskItem.scope_version_id.is_not(None))
            .where(TaskItem.scope_version_id != keep_scope_version_id)
            .where(TaskItem.status.in_(("open", "in_progress")))
            .values(status="cancelled")
        )
        return result.rowcount or 0

    def review_relation(
        self, relation_id: uuid.UUID, *, outcome: str, reason: str, reviewer: str
    ) -> CompanyImpactRelationReview:
        """Append a human relation review without mutating the ledger relation."""
        relation = self._session.get(CompanyImpactRelation, relation_id)
        if relation is None:
            raise NotFoundError(f"impact relation {relation_id} not found")
        hypothesis = self._session.get(EventImpactHypothesis, relation.hypothesis_id)
        if hypothesis is None:
            raise ValidationFailedError("impact relation hypothesis not found")
        lock_event_research_lifecycle(self._session, hypothesis.research_case_id)
        current_scope = self._session.scalar(
            select(EventResearchScopeVersion.id)
            .where(EventResearchScopeVersion.research_case_id == hypothesis.research_case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if current_scope != relation.scope_version_id or relation.status not in {"candidate", "unresolved"}:
            raise ValidationFailedError("impact relation is not reviewable in the current scope")
        if self.schedule_company_impact_reviews(
            hypothesis.research_case_id, scope_version_id=relation.scope_version_id
        ) == 0 and self._session.scalar(
            select(TaskItem.id)
            .where(TaskItem.task_type == "review_company_impact")
            .where(TaskItem.ref_type == "company_impact_relation")
            .where(TaskItem.ref_id == relation.id)
            .where(TaskItem.scope_version_id == relation.scope_version_id)
        ) is None:
            raise ValidationFailedError("impact relation is not a high-impact source gap")
        review = CompanyImpactRelationReview(
            relation_id=relation.id, outcome=outcome, reason=reason.strip(), reviewer=reviewer.strip(), created_at=_utcnow()
        )
        self._session.add(review)
        self._session.flush()
        self.classify(hypothesis.research_case_id, scope_version_id=relation.scope_version_id,
                      hypothesis_ids=[hypothesis.id], update_lifecycle=False)
        return review

    def classify(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
        hypothesis_ids: Sequence[uuid.UUID] | None = None,
        update_lifecycle: bool = True,
    ) -> list[EventImpactHypothesisAssessment]:
        """Append transparent, scope-bound assessments for current hypotheses.

        Classifications are never written back to ``EventImpactHypothesis``:
        every run adds a new immutable assessment trace so later ledger data
        can change the current interpretation without erasing the earlier one.
        """
        lifecycle = lock_event_research_lifecycle(self._session, case_id)
        requested_hypothesis_ids = hypothesis_ids
        scope = self._scope_for(case_id, scope_version_id)
        hypothesis_stmt = (
            select(EventImpactHypothesis)
            .where(EventImpactHypothesis.research_case_id == case_id)
            .where(EventImpactHypothesis.scope_version_id == scope.id)
            .order_by(EventImpactHypothesis.rank, EventImpactHypothesis.created_at)
        )
        if hypothesis_ids is not None:
            hypothesis_stmt = hypothesis_stmt.where(EventImpactHypothesis.id.in_(hypothesis_ids))
        hypotheses = list(self._session.scalars(hypothesis_stmt))
        hypothesis_ids = [hypothesis.id for hypothesis in hypotheses]
        relations = list(
            self._session.scalars(
                select(CompanyImpactRelation).where(
                    CompanyImpactRelation.hypothesis_id.in_(hypothesis_ids)
                )
            )
        ) if hypothesis_ids else []
        relation_ids = [relation.id for relation in relations]
        latest_reviews: dict[uuid.UUID, CompanyImpactRelationReview] = {}
        if relation_ids:
            for review in self._session.scalars(
                select(CompanyImpactRelationReview)
                .where(CompanyImpactRelationReview.relation_id.in_(relation_ids))
                .order_by(CompanyImpactRelationReview.created_at.desc())
            ):
                latest_reviews.setdefault(review.relation_id, review)
        observations = list(
            self._session.scalars(
                select(CompanyImpactObservation).where(
                    CompanyImpactObservation.relation_id.in_(relation_ids)
                )
            )
        ) if relation_ids else []

        relations_by_hypothesis: dict[uuid.UUID, list[CompanyImpactRelation]] = {}
        for relation in relations:
            relations_by_hypothesis.setdefault(relation.hypothesis_id, []).append(relation)
        observations_by_relation: dict[uuid.UUID, list[CompanyImpactObservation]] = {}
        for observation in observations:
            observations_by_relation.setdefault(observation.relation_id, []).append(observation)
        relation_as_of = {
            relation.id: max(
                (
                    observation.as_of_date
                    for observation in observations_by_relation.get(relation.id, [])
                    if observation.as_of_date is not None
                ),
                default=_utcnow().date(),
            )
            for relation in relations
        }
        fund_coverage_by_relation = self._bulk_fund_coverage(
            relations, relation_as_of
        )

        classifications: list[
            tuple[EventImpactHypothesis, str, dict[str, int], str]
        ] = []
        for hypothesis in hypotheses:
            hypothesis_relations = relations_by_hypothesis.get(hypothesis.id, [])
            hypothesis_observations = [
                observation
                for relation in hypothesis_relations
                for observation in observations_by_relation.get(relation.id, [])
            ]
            score = self._evidence_components(
                hypothesis_relations,
                hypothesis_observations,
                fund_coverage_by_relation,
                {relation.id: relation.status for relation in hypothesis_relations},
            )
            classification, explanation = self._impact_classification(
                hypothesis, score
            )
            classifications.append((hypothesis, classification, score, explanation))

        priority = {"key": 0, "alternative": 1, "background": 2, "unresolved": 3}
        classifications.sort(
            key=lambda row: (
                priority[row[1]],
                -sum(row[2].values()),
                row[0].rank,
                str(row[0].id),
            )
        )
        assessments = [
            EventImpactHypothesisAssessment(
                hypothesis_id=hypothesis.id,
                research_case_id=case_id,
                scope_version_id=scope.id,
                classification=classification,
                rank=rank,
                score_components=score,
                explanation=explanation,
                created_at=_utcnow(),
            )
            for rank, (hypothesis, classification, score, explanation) in enumerate(
                classifications, start=1
            )
        ]
        self._session.add_all(assessments)
        self._session.flush()

        if (
            update_lifecycle
            and requested_hypothesis_ids is None
            and not any(assessment.classification == "key" for assessment in assessments)
        ):
            self._mark_no_key_factor(lifecycle)
        return assessments

    def _evidence_components(
        self,
        relations: Sequence[CompanyImpactRelation],
        observations: Sequence[CompanyImpactObservation],
        fund_coverage_by_relation: dict[uuid.UUID, bool],
        effective_relation_statuses: dict[uuid.UUID, str] | None = None,
    ) -> dict[str, int]:
        verified_kinds = {
            observation.kind
            for observation in observations
            if observation.status == "verified"
        }
        return {
            "event": int("event" in verified_kinds),
            "company": int(any(
                (effective_relation_statuses or {}).get(relation.id, relation.status) == "verified"
                for relation in relations
            )),
            "operating": int("operating" in verified_kinds),
            "market": int("market" in verified_kinds),
            "peer_control": int("peer_control" in verified_kinds),
            "fund_coverage": int(
                any(
                    fund_coverage_by_relation.get(relation.id, False)
                    for relation in relations
                )
            ),
        }

    def _bulk_fund_coverage(
        self,
        relations: Sequence[CompanyImpactRelation],
        relation_as_of: dict[uuid.UUID, date],
    ) -> dict[uuid.UUID, bool]:
        """Evaluate verified relations' PIT fund coverage without relation N+1s."""
        verified_relations = [
            relation for relation in relations if relation.status == "verified"
        ]
        if not verified_relations:
            return {}
        company_ids = {relation.affected_company_id for relation in verified_relations}
        stocks_by_company: dict[uuid.UUID, list[Stock]] = {}
        for stock in self._session.scalars(
            select(Stock)
            .join(Company, Company.id == Stock.company_id)
            .where(Company.id.in_(company_ids))
            .where(Company.type == "listed")
            .where(Stock.market.in_(CHINA_A_SHARE_MARKETS))
        ):
            stocks_by_company.setdefault(stock.company_id, []).append(stock)

        eligible = [
            relation
            for relation in verified_relations
            if stocks_by_company.get(relation.affected_company_id)
        ]
        if not eligible:
            return {}
        all_stock_ids = {
            stock.id
            for relation in eligible
            for stock in stocks_by_company[relation.affected_company_id]
        }
        max_as_of = max(relation_as_of[relation.id] for relation in eligible)
        holding_history = self._market_data.fund_holding_history(
            list(all_stock_ids), as_of=max_as_of
        )
        fund_ids = {
            holding.fund_id
            for holding in holding_history
        }
        funds = {
            fund.id: fund
            for fund in self._session.scalars(select(Fund).where(Fund.id.in_(fund_ids)))
        } if fund_ids else {}

        coverage: dict[uuid.UUID, bool] = {}
        latest_holdings_by_cutoff: dict[
            date, dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure]
        ] = {}
        for relation in eligible:
            as_of = relation_as_of[relation.id]
            stocks = stocks_by_company[relation.affected_company_id]
            stock_ids = {stock.id for stock in stocks}
            disclosures_by_fund: dict[uuid.UUID, list[HoldingDisclosure]] = {}
            visible_holdings = latest_holdings_by_cutoff.get(as_of)
            if visible_holdings is None:
                visible_holdings = {}
                cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
                for holding in holding_history:
                    published_at = _to_aware_datetime(holding.published_at)
                    if published_at > cutoff:
                        continue
                    key = (holding.fund_id, holding.stock_id)
                    current = visible_holdings.get(key)
                    if current is None or (
                        holding.report_period,
                        published_at,
                    ) > (
                        current.report_period,
                        _to_aware_datetime(current.published_at),
                    ):
                        visible_holdings[key] = holding
                latest_holdings_by_cutoff[as_of] = visible_holdings
            for holding in visible_holdings.values():
                if holding.stock_id in stock_ids:
                    disclosures_by_fund.setdefault(holding.fund_id, []).append(holding)
            for fund_id, disclosures in disclosures_by_fund.items():
                fund = funds.get(fund_id)
                if fund is None or not is_china_public_fund(fund.code):
                    continue
                covered_stock_ids = {disclosure.stock_id for disclosure in disclosures}
                latest = max(
                    disclosures, key=lambda item: (item.report_period, item.published_at)
                )
                stale = (as_of - latest.report_period).days > 180
                if not stale and Decimal(len(covered_stock_ids)) / Decimal(len(stocks)) >= Decimal("0.80"):
                    coverage[relation.id] = True
                    break
        return coverage

    @staticmethod
    def _impact_classification(
        hypothesis: EventImpactHypothesis, score: dict[str, int]
    ) -> tuple[str, str]:
        if hypothesis.classification == "unresolved":
            return (
                "unresolved",
                "候选公司关系缺少可采纳来源，尚不能形成可审计的影响传导。",
            )
        required = ("event", "company", "operating", "market", "peer_control")
        if all(score[name] for name in required):
            return "key", "事件、传导、经营、市场和对照证据完整。"
        if score["event"] and (score["company"] or score["market"]):
            return "alternative", "存在部分支持，但缺少区分替代解释的完整证据。"
        return "background", "仅有环境线索，未形成可验证资产传导。"

    def _mark_no_key_factor(self, lifecycle) -> None:
        if lifecycle is None or lifecycle.status == "published":
            return
        lifecycle.status = "exhausted"
        lifecycle.status_summary = "当前范围内没有满足证据门槛的关键因素"
        lifecycle.current_gap = "不能确定关键因素；请补充传导、经营、市场或对照证据，或编辑研究因素。"
        lifecycle.next_human_action = "补充来源或调整研究范围"
        lifecycle.updated_at = _utcnow()

    def _append_insufficient_data_observation(
        self,
        relation: CompanyImpactRelation,
        kind: str,
        as_of: date,
        summary: str,
    ) -> bool:
        self._session.add(
            CompanyImpactObservation(
                relation_id=relation.id,
                kind=kind,
                status="insufficient",
                source_statement_id=None,
                valuation_snapshot_id=None,
                summary=summary,
                as_of_date=as_of,
                created_at=_utcnow(),
            )
        )
        return True

    def _append_refresh_output(
        self,
        *,
        case_id: uuid.UUID,
        scope_id: uuid.UUID,
        refresh_key: str,
        resolved_factors: Sequence[_ResolvedFactor],
        admissible_by_id: dict[uuid.UUID, SourceStatement],
        statement_dates: dict[uuid.UUID, date],
    ) -> ImpactRefreshResult:
        """Append a complete refresh trace in one savepoint or append none."""
        with self._session.begin_nested():
            resolved_candidates: list[_ResolvedCandidate] = []
            for resolved_factor in resolved_factors:
                factor = resolved_factor.factor
                candidates = resolved_factor.candidates
                hypothesis = EventImpactHypothesis(
                    research_case_id=case_id,
                    scope_version_id=scope_id,
                    statement=factor.statement,
                    classification="candidate",
                    rank=factor.position,
                    score_components={"refresh_key": refresh_key},
                    explanation=self._pending_evidence_explanation(
                        candidates, admissible_by_id
                    ),
                    created_at=_utcnow(),
                )
                self._session.add(hypothesis)
                resolved_candidates.extend(
                    _ResolvedCandidate(hypothesis=hypothesis, candidate=candidate)
                    for candidate in candidates
                )

            source_rejected_count = 0
            unresolved_candidate_count = 0
            source_backed_candidates: list[
                tuple[_ResolvedCandidate, SourceStatement]
            ] = []
            for entry in resolved_candidates:
                candidate = entry.candidate
                if candidate.source_statement_id is None:
                    unresolved_candidate_count += 1
                    self._session.add(
                        EventImpactHypothesis(
                            research_case_id=entry.hypothesis.research_case_id,
                            scope_version_id=entry.hypothesis.scope_version_id,
                            statement=(
                                f"{candidate.company_name} {candidate.relation_kind}: "
                                f"{candidate.mechanism}"
                            ),
                            classification="unresolved",
                            rank=entry.hypothesis.rank,
                            score_components={"refresh_key": refresh_key, "source": 0},
                            explanation=(
                                "source_statement_id is missing for "
                                f"{candidate.company_name}'s {candidate.relation_kind} "
                                "relationship; no relation or observation was appended."
                            ),
                            created_at=_utcnow(),
                        )
                    )
                    continue
                statement = admissible_by_id.get(candidate.source_statement_id)
                if statement is None:
                    source_rejected_count += 1
                    continue
                source_backed_candidates.append((entry, statement))

            # Source ownership/admission is checked before company creation.  A
            # resolver cannot manufacture entities by offering foreign/invalid ids.
            companies = self._companies_for(
                [entry.candidate for entry, _ in source_backed_candidates]
            )
            resolved_companies = [
                (
                    entry,
                    statement,
                    self._resolve_or_create_company(entry.candidate, companies),
                )
                for entry, statement in source_backed_candidates
            ]
            # New Company ids are needed by the append-only relation rows.  This
            # is a single bulk flush, rather than a lookup/flush for each candidate.
            self._session.flush()

            relations_created = 0
            relation_sources: list[
                tuple[CompanyImpactRelation, _ResolvedCandidate, SourceStatement]
            ] = []
            for entry, statement, company in resolved_companies:
                candidate = entry.candidate
                relation = CompanyImpactRelation(
                    hypothesis=entry.hypothesis,
                    scope_version_id=entry.hypothesis.scope_version_id,
                    affected_company_id=company.id,
                    relation_kind=candidate.relation_kind,
                    direction=candidate.direction,
                    mechanism=candidate.mechanism,
                    status="candidate",
                    source_statement_id=statement.id,
                    created_at=_utcnow(),
                )
                self._session.add(relation)
                relation_sources.append((relation, entry, statement))
                relations_created += 1
            self._session.flush()
            for relation, entry, statement in relation_sources:
                candidate = entry.candidate
                self._session.add(
                    CompanyImpactObservation(
                        relation_id=relation.id,
                        kind="event",
                        status="verified",
                        source_statement_id=statement.id,
                        valuation_snapshot_id=None,
                        summary=(
                            f"Event factor: {entry.hypothesis.statement}. "
                            f"Evidence: {statement.normalized_text}"
                        ),
                        as_of_date=statement_dates[statement.id],
                        created_at=_utcnow(),
                    )
                )
                self._session.add(
                    CompanyImpactObservation(
                        relation_id=relation.id,
                        kind="relation",
                        status="verified",
                        source_statement_id=statement.id,
                        valuation_snapshot_id=None,
                        summary=(
                            f"{candidate.relation_kind}: {candidate.mechanism}. "
                            f"Evidence: {statement.normalized_text}"
                        ),
                        as_of_date=statement_dates[statement.id],
                        created_at=_utcnow(),
                    )
                )
            self._session.flush()
        return ImpactRefreshResult(
            hypotheses_created=len(resolved_factors) + unresolved_candidate_count,
            relations_created=relations_created,
            source_rejected_count=source_rejected_count,
            unresolved_candidate_count=unresolved_candidate_count,
        )

    def schedule_refresh(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID, run_id: uuid.UUID
    ) -> EventImpactRefreshClaim:
        """Atomically claim and enqueue one executable scope refresh task."""
        scope = self._scope_for(case_id, scope_version_id)
        run = self._session.get(ResearchRun, run_id)
        if run is None or run.research_case_id != case_id:
            raise ValidationFailedError("research run does not belong to the event research case")
        refresh_key = self._initial_refresh_key(scope.id)
        claim, created = self._claim_refresh(
            case_id, scope.id, refresh_key, run_id=run_id
        )
        task_query = f"impact_refresh:{claim.id}:{scope.id}:{refresh_key}"
        active_task = self._session.scalar(
            select(ResearchTask.id)
            .where(ResearchTask.run_id == run_id)
            .where(ResearchTask.task_type == "impact_refresh")
            .where(ResearchTask.query == task_query)
            .where(ResearchTask.status.in_(("queued", "running", "done")))
            .limit(1)
        )
        if active_task is None:
            self._session.add(
                ResearchTask(
                    run_id=run_id,
                    research_case_id=case_id,
                    thesis_id=None,
                    task_type="impact_refresh",
                    query=task_query,
                    result=None,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
        if created:
            emit_event(
                self._session,
                type=_REFRESH_REQUEST_EVENT_TYPE,
                aggregate_type="event_impact_refresh_claim",
                aggregate_id=claim.id,
                ref_type="research_case",
                ref_id=case_id,
                origin="operational",
                payload={
                    "research_case_id": str(case_id),
                    "scope_version_id": str(scope.id),
                    "refresh_claim_id": str(claim.id),
                    "run_id": str(run_id),
                },
            )
        self._session.flush()
        return claim

    def _claim_refresh(
        self,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        refresh_key: str,
        *,
        run_id: uuid.UUID | None = None,
    ) -> tuple[EventImpactRefreshClaim, bool]:
        """Use the unique ledger claim as the concurrency boundary."""
        existing = self._session.scalar(
            select(EventImpactRefreshClaim)
            .where(EventImpactRefreshClaim.scope_version_id == scope_version_id)
            .where(EventImpactRefreshClaim.refresh_key == refresh_key)
            .limit(1)
        )
        if existing is not None:
            return existing, False
        try:
            _before_refresh_claim_insert()
            with self._session.begin_nested():
                claim = EventImpactRefreshClaim(
                    research_case_id=case_id,
                    scope_version_id=scope_version_id,
                    run_id=run_id,
                    refresh_key=refresh_key,
                    created_at=_utcnow(),
                )
                self._session.add(claim)
                self._session.flush()
            return claim, True
        except IntegrityError:
            claim = self._session.scalar(
                select(EventImpactRefreshClaim)
                .where(EventImpactRefreshClaim.scope_version_id == scope_version_id)
                .where(EventImpactRefreshClaim.refresh_key == refresh_key)
                .limit(1)
            )
            if claim is None:  # pragma: no cover - protects unusual DB drivers
                raise
            return claim, False

    def _legacy_schedule_event(
        self, case_id: uuid.UUID, scope: EventResearchScopeVersion
    ) -> DomainEvent | None:
        """Retained only for audit compatibility; tasks/claims drive execution."""
        existing = self._session.scalar(
            select(DomainEvent)
            .where(DomainEvent.type == _REFRESH_REQUEST_EVENT_TYPE)
            .where(DomainEvent.aggregate_type == "event_research_scope")
            .where(DomainEvent.aggregate_id == str(scope.id))
            .limit(1)
        )
        if existing is not None:
            return existing
        return emit_event(
            self._session,
            type=_REFRESH_REQUEST_EVENT_TYPE,
            aggregate_type="event_research_scope",
            aggregate_id=scope.id,
            ref_type="research_case",
            ref_id=case_id,
            origin="operational",
            payload={
                "research_case_id": str(case_id),
                "scope_version_id": str(scope.id),
            },
        )

    def _scope_for(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID | None
    ) -> EventResearchScopeVersion:
        if self._session.scalar(
            select(EventResearchBrief.id).where(
                EventResearchBrief.research_case_id == case_id
            )
        ) is None:
            raise NotFoundError("event research case not found")
        scope_query = select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
        if scope_version_id is not None:
            scope = self._session.scalar(
                scope_query.where(EventResearchScopeVersion.id == scope_version_id)
            )
            if scope is None:
                raise ValidationFailedError(
                    "scope version does not belong to the event research case"
                )
            return scope
        scope = self._session.scalar(
            scope_query.order_by(EventResearchScopeVersion.version.desc()).limit(1)
        )
        if scope is None:
            raise ValidationFailedError("event research scope has not been created")
        return scope

    @staticmethod
    def _initial_refresh_key(scope_version_id: uuid.UUID) -> str:
        """Identity for the one initial refresh of a scope; later runs use a new key."""
        return f"scope:{scope_version_id}:{_INITIAL_REFRESH_KEY_SUFFIX}"

    def _admissible_statement_records(
        self, case_id: uuid.UUID
    ) -> list[tuple[SourceStatement, DocumentVersion]]:
        rows = self._session.execute(
            select(SourceStatement, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id == DocumentVersion.id,
            )
            .join(
                EvidenceLink,
                EvidenceLink.source_statement_id == SourceStatement.id,
            )
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(Thesis.research_case_id == case_id)
            .order_by(SourceStatement.id)
            .distinct()
        )
        return [
            (statement, document)
            for statement, document in rows
            if classify_source(
                document.source_url,
                document.parser_version,
                document.parse_state in {"success", "parsed"},
            ).can_accept
        ]

    def _companies_for(
        self, candidates: Sequence[ResolvedImpactCompany]
    ) -> dict[tuple[str, str, str], Company]:
        keys = {
            self._company_key(candidate.company_name, candidate.type)
            for candidate in candidates
        }
        if not keys:
            return {}
        identities = {key[1] for key in keys}
        company_types = {candidate.type for candidate in candidates}
        # The steady-state lookup is deliberately restricted to the two
        # persisted identities.  Migration 0021 backfills aliases for legacy
        # immutable rows, so refresh never falls back to a type-wide (or even
        # repeated lower/trim) scan of companies with a NULL identity.
        existing = list(
            self._session.scalars(
                select(Company)
                .where(Company.type.in_(company_types))
                .where(Company.canonical_identity.in_(identities))
            )
        )
        existing.extend(
            self._session.scalars(
                select(Company)
                .join(
                    CompanyIdentityAlias,
                    CompanyIdentityAlias.company_id == Company.id,
                )
                .where(CompanyIdentityAlias.company_type.in_(company_types))
                .where(CompanyIdentityAlias.canonical_identity.in_(identities))
            )
        )
        companies: dict[tuple[str, str, str], Company] = {}
        for company in {company.id: company for company in existing}.values():
            companies.setdefault(self._company_key(company.name, company.type), company)
            code_identity = self._canonical_identity(company.code.replace("-", " "))
            companies.setdefault(
                (
                    self._canonical_code(company.code),
                    code_identity,
                    company.type,
                ),
                company,
            )
        return companies

    def _resolve_or_create_company(
        self,
        candidate: ResolvedImpactCompany,
        companies: dict[tuple[str, str, str], Company],
    ) -> Company:
        key = self._company_key(candidate.company_name, candidate.type)
        company = companies.get(key)
        if company is not None:
            return company
        company = Company(
            code=key[0],
            name=self._normalized_title(candidate.company_name),
            type=candidate.type,
            created_at=_utcnow(),
        )
        try:
            with self._session.begin_nested():
                _before_company_insert()
                self._session.add(company)
                self._session.flush()
            companies[key] = company
            return company
        except IntegrityError:
            winner = self._session.scalar(
                select(Company)
                .where(Company.type == candidate.type)
                .where(Company.canonical_identity == key[1])
                .limit(1)
            )
            if winner is None:
                raise
            companies[key] = winner
            return winner

    @staticmethod
    def _company_key(
        company_name: str,
        company_type: str,
    ) -> tuple[str, str, str]:
        identity = EventImpactResearchService._canonical_identity(company_name)
        return identity.replace(" ", "-"), identity, company_type

    @staticmethod
    def _normalized_title(value: str) -> str:
        return " ".join(normalize("NFKC", value).split())

    @staticmethod
    def _canonical_identity(value: str) -> str:
        return EventImpactResearchService._normalized_title(value).casefold()

    @staticmethod
    def _canonical_code(value: str) -> str:
        return EventImpactResearchService._canonical_identity(value).replace(" ", "-")

    @staticmethod
    def _pending_evidence_explanation(
        candidates: Sequence[ResolvedImpactCompany],
        admissible_by_id: dict[uuid.UUID, SourceStatement],
    ) -> str:
        if any(candidate.source_statement_id is None for candidate in candidates):
            return "Candidate company identified, but no admissible source statement supports it."
        if any(
            candidate.source_statement_id not in admissible_by_id
            for candidate in candidates
        ):
            return "Candidate company relation awaits an admissible same-case source statement."
        return "Candidate hypothesis pending evidence-based impact classification."
