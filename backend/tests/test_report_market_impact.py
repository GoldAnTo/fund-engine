"""Point-in-time market verification for report claims."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from threading import Barrier, Lock, Thread

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    ValuationSnapshot,
)
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportMarketConfounder,
    ReportMarketObservation,
    ReportFundExposure,
    ReportRelation,
)
from app.services.report_market_impact import ReportMarketImpactService


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
PUBLISHED_AT = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
TRADING_DAYS = (
    date(2026, 8, 4),
    date(2026, 8, 5),
    date(2026, 8, 6),
    date(2026, 8, 7),
    date(2026, 8, 10),
)


def _report_claim(session, *, published_at: datetime | None = PUBLISHED_AT):
    case = ResearchCase(
        id=uuid.uuid4(),
        title="服务器产业链更新",
        industry_topic="算力",
        created_at=NOW,
        created_by="test",
    )
    document = DocumentVersion(
        id=uuid.uuid4(),
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        source_url="research://broker/report",
        published_at=published_at,
        available_at=NOW,
        acquired_at=NOW,
        parser_version="fixture",
    )
    span = SourceSpan(
        id=uuid.uuid4(),
        document_version_id=document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="核心观点：订单增长。",
    )
    statement = SourceStatement(
        id=uuid.uuid4(),
        source_span_id=span.id,
        kind="research_opinion",
        normalized_text=span.verbatim_text,
        observed_period=None,
        created_at=NOW,
    )
    session.add_all([case, document, span, statement])
    session.flush()
    session.add(
        CaseDocumentVersion(
            id=uuid.uuid4(),
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=NOW,
        )
    )
    session.flush()
    session.add(
        ReportCaseSourceSpan(
            id=uuid.uuid4(),
            research_case_id=case.id,
            document_version_id=document.id,
            source_span_id=span.id,
            created_at=NOW,
        )
    )
    session.flush()
    claim = ReportClaim(
        id=uuid.uuid4(),
        research_case_id=case.id,
        source_span_id=span.id,
        source_statement_id=statement.id,
        kind="report_opinion",
        statement=statement.normalized_text,
        created_at=NOW,
    )
    session.add(claim)
    session.flush()
    return case, claim


def _company_stock(session, *, name: str, code: str, market: str = "SSE"):
    company = Company(
        id=uuid.uuid4(), code=code, name=name, type="listed", created_at=NOW
    )
    stock = Stock(
        id=uuid.uuid4(),
        company_id=company.id,
        code=f"{code}.SH",
        name=name,
        market=market,
        created_at=NOW,
    )
    session.add_all([company, stock])
    session.flush()
    return company, stock


def _snapshot(
    session,
    stock: Stock,
    *,
    as_of: date,
    metric: str,
    value: str = "1",
    available_at: datetime | None = None,
):
    snapshot = ValuationSnapshot(
        id=uuid.uuid4(),
        stock_id=stock.id,
        as_of_date=as_of,
        metric_name=metric,
        metric_value=Decimal(value),
        source="ledger-fixture",
        definition=f"fixture {metric}",
        available_at=available_at or datetime.combine(as_of, time.max, tzinfo=timezone.utc),
        created_at=NOW,
    )
    session.add(snapshot)
    return snapshot


def _mapped_relation(session, claim: ReportClaim, *, target: Company, peer: Company | None = None):
    relation = ReportRelation(
        id=uuid.uuid4(),
        claim_id=claim.id,
        research_case_id=claim.research_case_id,
        source_span_id=claim.source_span_id,
        source_statement_id=claim.source_statement_id,
        subject_company_id=target.id,
        object_company_id=peer.id if peer else None,
        subject_name=None,
        object_name=peer.name if peer is not None else "行业需求",
        relation_kind="competitor" if peer else "customer",
        mechanism="研报明确关系",
        status="report_claim",
        created_at=NOW,
    )
    session.add(relation)
    session.flush()
    return relation


def test_report_market_window_uses_next_trading_days_and_ledger_metrics(session):
    _case, claim = _report_claim(session)
    target, target_stock = _company_stock(session, name="目标公司", code="600001")
    peer, peer_stock = _company_stock(session, name="可比公司", code="600002")
    _mapped_relation(session, claim, target=target, peer=peer)
    for as_of in TRADING_DAYS:
        for metric in ("VOLUME", "TURNOVER_RATE", "VOLATILITY", "PE_TTM"):
            _snapshot(session, target_stock, as_of=as_of, metric=metric)
    _snapshot(session, target_stock, as_of=TRADING_DAYS[0], metric="INDUSTRY_RETURN_1D")
    _snapshot(session, target_stock, as_of=TRADING_DAYS[4], metric="INDUSTRY_RETURN_5D")
    _snapshot(session, peer_stock, as_of=TRADING_DAYS[0], metric="PEER_RETURN_1D")
    _snapshot(session, peer_stock, as_of=TRADING_DAYS[4], metric="PEER_RETURN_5D")
    _snapshot(session, target_stock, as_of=TRADING_DAYS[0], metric="EVENT_RETURN_1D")
    _snapshot(session, target_stock, as_of=TRADING_DAYS[4], metric="EVENT_RETURN_5D")
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)
    session.commit()

    assert result.windows == {"1d": "verified", "5d": "verified"}
    assert result.trading_days == {"1d": TRADING_DAYS[0], "5d": TRADING_DAYS[4]}
    assert {observation.kind for observation in result.observations} >= {
        "target_market",
        "peer_control",
        "industry_control",
    }
    assert all(
        observation.status == "verified"
        for observation in result.observations
        if observation.kind != "industry_control"
    )
    assert {
        observation.status
        for observation in result.observations
        if observation.kind == "industry_control"
    } == {"insufficient"}
    persisted = list(
        session.scalars(
            select(ReportMarketObservation).where(
                ReportMarketObservation.report_claim_id == claim.id
            )
        )
    )
    assert {row.valuation_snapshot_id for row in persisted if row.status == "verified"}

    again = ReportMarketImpactService(session).collect(claim.id)
    session.commit()
    assert len(again.observations) == len(result.observations)
    assert len(
        list(
            session.scalars(
                select(ReportMarketObservation).where(
                    ReportMarketObservation.report_claim_id == claim.id
                )
            )
        )
    ) == len(persisted)


def test_missing_publish_time_skips_market_window(session):
    _case, claim = _report_claim(session, published_at=None)
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)

    assert result.windows == {}
    assert result.trading_days == {}
    assert result.gaps == ("缺少公开时间，未计算市场反应",)
    assert result.observations == ()


def test_unlisted_relation_never_receives_stock_or_fund_values(session):
    _case, claim = _report_claim(session)
    unlisted = Company(
        id=uuid.uuid4(),
        code="PRIVATE-SUPPLIER",
        name="未上市供应商",
        type="unlisted_supplier",
        created_at=NOW,
    )
    session.add(unlisted)
    session.flush()
    _mapped_relation(session, claim, target=unlisted)
    _calendar_days(session)
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)

    assert result.windows == {"1d": "insufficient", "5d": "insufficient"}
    assert all(observation.stock_id is None for observation in result.observations)
    assert all(observation.valuation_snapshot_id is None for observation in result.observations)
    assert any("未上市" in gap for gap in result.gaps)


def test_missing_ledger_data_appends_insufficient_observations(session):
    _case, claim = _report_claim(session)
    target, _stock = _company_stock(session, name="缺数据公司", code="600003")
    _mapped_relation(session, claim, target=target)
    _calendar_days(session)
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)

    assert result.windows == {"1d": "insufficient", "5d": "insufficient"}
    assert {observation.status for observation in result.observations} == {"insufficient"}
    assert any("缺少" in gap for gap in result.gaps)


def test_missing_trading_calendar_appends_insufficient_not_an_empty_result(session):
    _case, claim = _report_claim(session)
    target, _stock = _company_stock(session, name="日历缺失公司", code="600004")
    _mapped_relation(session, claim, target=target)
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)
    session.commit()

    assert result.windows == {"1d": "insufficient", "5d": "insufficient"}
    assert result.trading_days == {}
    assert {row.status for row in result.observations} == {"insufficient"}
    assert {row.window for row in result.observations} == {"1d", "5d"}


def test_late_backfilled_market_snapshot_is_not_point_in_time_evidence(session):
    _case, claim = _report_claim(session)
    target, stock = _company_stock(session, name="回填公司", code="600005")
    _mapped_relation(session, claim, target=target)
    _calendar_days(session)
    for metric in ("EVENT_RETURN_1D", "VOLUME", "TURNOVER_RATE", "VOLATILITY", "PE_TTM"):
        _snapshot(
            session,
            stock,
            as_of=TRADING_DAYS[0],
            metric=metric,
            available_at=NOW,
        )
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)

    assert result.windows["1d"] == "insufficient"
    assert not [
        row
        for row in result.observations
        if row.window == "1d" and row.status == "verified"
    ]


def test_visible_china_fund_holding_is_mapped_only_for_listed_target(session):
    _case, claim = _report_claim(session)
    target, stock = _company_stock(session, name="基金映射公司", code="600006")
    _mapped_relation(session, claim, target=target)
    _calendar_days(session)
    fund = Fund(
        id=uuid.uuid4(),
        code="000001",
        name="中国公募基金",
        fund_type="equity",
        created_at=NOW,
    )
    disclosure = HoldingDisclosure(
        id=uuid.uuid4(),
        fund_id=fund.id,
        stock_id=stock.id,
        weight=Decimal("3.25"),
        report_period=date(2026, 6, 30),
        published_at=datetime(2026, 8, 3, 7, tzinfo=timezone.utc),
        acquired_at=datetime(2026, 8, 3, 7, tzinfo=timezone.utc),
        source="fund-fixture",
        created_at=NOW,
    )
    session.add_all([fund, disclosure])
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)
    session.commit()

    assert [(row.window, row.status, row.fund_id, row.weight) for row in result.fund_exposures] == [
        ("1d", "verified", fund.id, Decimal("3.25")),
        ("5d", "verified", fund.id, Decimal("3.25")),
    ]
    assert len(
        list(
            session.scalars(
                select(ReportFundExposure).where(ReportFundExposure.report_claim_id == claim.id)
            )
        )
    ) == 2


def test_collects_only_same_case_confounder_visible_in_market_window(session):
    case, claim = _report_claim(session)
    unlisted = Company(
        id=uuid.uuid4(),
        code="PRIVATE-TRANSMISSION",
        name="未上市传导节点",
        type="unlisted_supplier",
        created_at=NOW,
    )
    session.add(unlisted)
    session.flush()
    _mapped_relation(session, claim, target=unlisted)
    _calendar_days(session)
    document = DocumentVersion(
        id=uuid.uuid4(),
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        source_url="announcement://target/earnings",
        published_at=datetime(2026, 8, 5, 9, tzinfo=timezone.utc),
        available_at=datetime(2026, 8, 5, 9, tzinfo=timezone.utc),
        acquired_at=NOW,
        parser_version="fixture",
        title="业绩预告",
    )
    span = SourceSpan(
        id=uuid.uuid4(),
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="公司发布业绩预告。",
    )
    statement = SourceStatement(
        id=uuid.uuid4(),
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text=span.verbatim_text,
        observed_period=None,
        created_at=NOW,
    )
    session.add_all([document, span, statement])
    session.flush()
    session.add(
        CaseDocumentVersion(
            id=uuid.uuid4(),
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=NOW,
        )
    )
    session.commit()

    result = ReportMarketImpactService(session).collect(claim.id)
    session.commit()

    assert [(row.window, row.kind, row.source_statement_id) for row in result.confounders] == [
        ("5d", "earnings", statement.id)
    ]
    assert session.scalar(
        select(ReportMarketConfounder).where(
            ReportMarketConfounder.source_statement_id == statement.id
        )
    ) is not None


def test_cross_case_confounder_source_is_rejected_by_ledger_validation(session):
    _case, claim = _report_claim(session)
    other_case = ResearchCase(
        id=uuid.uuid4(), title="其他研究", industry_topic="其他", created_at=NOW, created_by="test"
    )
    other_document = DocumentVersion(
        id=uuid.uuid4(), content_sha256=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        source_url="news://other-case", published_at=PUBLISHED_AT,
        available_at=PUBLISHED_AT, acquired_at=PUBLISHED_AT, parser_version="fixture",
    )
    other_span = SourceSpan(
        id=uuid.uuid4(), document_version_id=other_document.id,
        locator={"page": 1}, verbatim_text="其他案例的公告。",
    )
    other_statement = SourceStatement(
        id=uuid.uuid4(), source_span_id=other_span.id, kind="disclosed_fact",
        normalized_text=other_span.verbatim_text, observed_period=None, created_at=NOW,
    )
    session.add_all([other_case, other_document, other_span, other_statement])
    session.flush()
    session.add(CaseDocumentVersion(
        id=uuid.uuid4(), research_case_id=other_case.id,
        document_version_id=other_document.id, linked_at=NOW,
    ))
    session.commit()

    session.add(ReportMarketConfounder(
        id=uuid.uuid4(), research_case_id=claim.research_case_id,
        report_claim_id=claim.id, source_statement_id=other_statement.id,
        window="1d", kind="announcement", as_of_date=TRADING_DAYS[0],
        summary="forged cross-case source", collection_key=uuid.uuid4().hex, created_at=NOW,
    ))
    with pytest.raises(ValueError, match="attached to its claim case"):
        session.flush()


@pytest.mark.pg_only
def test_postgres_concurrent_collection_reuses_unique_observations(engine, monkeypatch):
    """A losing insert rolls back only its savepoint and reads the winner."""
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        _case, claim = _report_claim(bootstrap)
        _calendar_days(bootstrap)
        bootstrap.commit()
        claim_id = claim.id
    finally:
        bootstrap.close()

    barrier = Barrier(2)
    gate_lock = Lock()
    gate_count = 0

    def _gate() -> None:
        nonlocal gate_count
        with gate_lock:
            should_wait = gate_count < 2
            gate_count += 1
        if should_wait:
            barrier.wait(timeout=5)

    monkeypatch.setattr(
        "app.services.report_market_impact._before_report_market_unique_insert", _gate
    )
    errors: list[BaseException] = []

    def _collect() -> None:
        db = SessionLocal()
        try:
            ReportMarketImpactService(db).collect(claim_id)
            db.commit()
        except BaseException as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first, second = Thread(target=_collect), Thread(target=_collect)
    first.start(); second.start()
    first.join(timeout=15); second.join(timeout=15)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    verify = SessionLocal()
    try:
        rows = list(
            verify.scalars(
                select(ReportMarketObservation).where(
                    ReportMarketObservation.report_claim_id == claim_id
                )
            )
        )
        assert len(rows) == 2  # one target-mapping gap for each 1d/5d window
    finally:
        verify.close()


def _calendar_days(session) -> None:
    """Seed only calendar visibility; it must not make the target computable."""
    _company, stock = _company_stock(session, name="交易日样本", code="600099")
    for as_of in TRADING_DAYS:
        _snapshot(session, stock, as_of=as_of, metric="PE_TTM")
