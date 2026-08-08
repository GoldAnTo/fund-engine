"""Point-in-time market verification for report claims."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
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


def _snapshot(session, stock: Stock, *, as_of: date, metric: str, value: str = "1"):
    snapshot = ValuationSnapshot(
        id=uuid.uuid4(),
        stock_id=stock.id,
        as_of_date=as_of,
        metric_name=metric,
        metric_value=Decimal(value),
        source="ledger-fixture",
        definition=f"fixture {metric}",
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
    assert all(observation.status == "verified" for observation in result.observations)
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


def _calendar_days(session) -> None:
    """Seed only calendar visibility; it must not make the target computable."""
    _company, stock = _company_stock(session, name="交易日样本", code="600099")
    for as_of in TRADING_DAYS:
        _snapshot(session, stock, as_of=as_of, metric="PE_TTM")
