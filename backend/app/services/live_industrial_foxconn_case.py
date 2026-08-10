"""Materialise the approved Industrial Foxconn historical case into a ledger.

The runner is intentionally narrow: it persists only the source bundle
selected by :mod:`industrial_foxconn_forecast_case`, records how the numbers
were evaluated, and creates a clearly named demonstration human verdict.  It
does not infer a market-price cause or an unreported fund position.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import DocumentVersion, ResearchCase, SourceSpan, SourceStatement
from app.repositories.documents import DocumentRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.event_research import EventResearchService
from app.services.forecast_verdicts import (
    ActualObservationInput,
    ForecastTargetInput,
    ForecastVerdictInput,
    ForecastVerdictService,
)
from app.services.industrial_foxconn_forecast_case import IndustrialFoxconnSourceBundle
from app.services.ingest import DocumentService, compute_natural_key
from app.services.instruments import InstrumentService
from app.services.market_expression import (
    ClaimVerificationInput,
    FundamentalImpactInput,
    KeyFactorInput,
    MarketExpressionService,
    MarketInstrumentBindingInput,
    ReportClaimInput,
)
from app.services.source_governance import SourceGovernanceService


CASE_TITLE = "工业富联：2024 年归母净利润预测历史验证（海通证券）"
ACTOR = "human:industrial-foxconn-demo-reviewer"


@dataclass(frozen=True)
class LiveCaseResult:
    case_id: uuid.UUID
    verdict_id: uuid.UUID
    expected_value: str
    baseline_value: str
    actual_value: str
    outcome: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _provider_url(tool: str, title: str) -> str:
    digest = hashlib.sha256(f"{tool}|{title}".encode("utf-8")).hexdigest()[:16]
    return f"gildata://{tool}/{digest}"


def _admit_document(
    session: Session,
    *,
    case_id: uuid.UUID,
    title: str,
    published_at: datetime,
    raw_response: str,
    tool: str,
    query: str,
    authority: str,
) -> DocumentVersion:
    """Freeze a provider payload with historical availability and current acquisition.

    ``available_at`` remains the source publication time; ``acquired_at`` is
    deliberately now because this is a reconstruction performed today.  A
    historical replay before acquisition therefore cannot silently claim this
    reconstructed ledger entry was already present.
    """
    source_url = _provider_url(tool, title)
    digest = hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
    document = session.scalar(select(DocumentVersion).where(DocumentVersion.content_sha256 == digest))
    if document is None:
        document = DocumentVersion(
            content_sha256=digest,
            source_url=source_url,
            natural_key=compute_natural_key(source_url, title, published_at),
            title=title,
            published_at=published_at,
            available_at=published_at,
            acquired_at=_now(),
            parser_version="gildata-live-industrial-foxconn-v1",
            parse_state="success",
            source_authority=authority,
        )
        session.add(document)
        session.flush()
    DocumentService(DocumentRepository(session)).attach_to_case(
        research_case_id=case_id, document_version_id=document.id
    )
    SourceGovernanceService(session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={
            "provider_name": "Gildata",
            "provider_record_id": f"{tool}:{title}",
            "request_scope": {"tool": tool, "query": query, "case": "industrial-foxconn-forecast-v1"},
            "permissions": {"ai_processing": True, "display": True},
            "contract_version": "local-demo-gildata-v1",
            "downstream_restrictions": ["仅限此本地演示 Case 的研究与人工审核"],
        },
        declared_by=ACTOR,
    )
    return document


def _statement(
    session: Session,
    *,
    document: DocumentVersion,
    label: str,
    quote: str,
    kind: str,
    observed_period: date | None,
) -> SourceStatement:
    span = SourceSpan(
        document_version_id=document.id,
        locator={"kind": "gildata_result", "label": label, "title": document.title},
        verbatim_text=quote,
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind=kind,
        normalized_text=quote,
        observed_period=observed_period,
        created_at=_now(),
    )
    session.add(statement)
    session.flush()
    return statement


def materialize_live_industrial_foxconn_case(
    session: Session,
    *,
    bundle: IndustrialFoxconnSourceBundle,
    tenant_id: str,
) -> LiveCaseResult:
    """Write the selected live source bundle and a reviewed demonstration verdict."""
    existing = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE).limit(1))
    if existing is not None:
        raise ValueError(f"local demonstration case already exists: {existing.id}")

    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input=(
                "历史研报预测验证案例：海通证券 2024-03-25 对工业富联 2024 年归母净利润预测，"
                "后续以公司 2024 年年度报告和基金历史披露复核。"
            ),
            source_type="pasted_snapshot",
            event_title=CASE_TITLE,
            company_name="工业富联",
            ticker="601138.SH",
            event_at=bundle.report.published_at,
            research_question="海通证券对工业富联 2024 年归母净利润的预测是否在预设容差内兑现，AI 业务因素和历史基金披露如何对应？",
            candidate_factors=["2024 年归母净利润预测", "AI 服务器收入", "800G 高速交换机"],
            created_by=ACTOR,
        ),
        tenant_id=tenant_id,
    )
    case_id = uuid.UUID(created.case_id)
    report_doc = _admit_document(
        session, case_id=case_id, title=bundle.report.title,
        published_at=bundle.report.published_at, raw_response=bundle.report.raw_response,
        tool=bundle.report.tool, query=bundle.report.query, authority="licensed_research",
    )
    annual_doc = _admit_document(
        session, case_id=case_id, title=bundle.annual_report.title,
        published_at=bundle.annual_report.published_at, raw_response=bundle.annual_report.raw_response,
        tool=bundle.annual_report.tool, query=bundle.annual_report.query, authority="primary_disclosure",
    )
    holding_doc = _admit_document(
        session, case_id=case_id, title=bundle.fund_holding_snapshot.title,
        published_at=bundle.fund_report.published_at, raw_response=bundle.fund_holding_snapshot.raw_response,
        tool=bundle.fund_holding_snapshot.tool, query=bundle.fund_holding_snapshot.query, authority="licensed_research",
    )
    _admit_document(
        session, case_id=case_id, title=bundle.fund_report.title,
        published_at=bundle.fund_report.published_at, raw_response=bundle.fund_report.raw_response,
        tool=bundle.fund_report.tool, query=bundle.fund_report.query, authority="primary_disclosure",
    )

    forecast_statement = _statement(
        session, document=report_doc, label="2024-profit-forecast",
        quote="我们预计2024年归母净利润为251.49亿元。",
        kind="forecast", observed_period=date(2024, 12, 31),
    )
    baseline_statement = _statement(
        session, document=report_doc, label="2023-profit-baseline",
        quote="主要财务数据及预测表：2023 年净利润为 21040 百万元。",
        kind="disclosed_fact", observed_period=date(2023, 12, 31),
    )
    report_factor_statement = _statement(
        session, document=report_doc, label="AI-factor-title",
        quote="工业富联(601138)：盈利整体平稳增长 AI业务表现强劲。",
        kind="research_opinion", observed_period=None,
    )
    actual_statement = _statement(
        session, document=annual_doc, label="2024-profit-actual",
        quote="2024年归属于上市公司股东的净利润232.16亿元。",
        kind="disclosed_fact", observed_period=date(2024, 12, 31),
    )
    driver_statement = _statement(
        session, document=annual_doc, label="AI-driver-actual",
        quote="云计算业务收入3193.77亿元，同比增长64.37%；AI服务器收入同比超过150%；400G、800G高速交换机同比增长数倍。",
        kind="disclosed_fact", observed_period=date(2024, 12, 31),
    )
    holding_statement = _statement(
        session, document=holding_doc, label="fund-515050-position",
        quote="华夏中证5G通信主题ETF（515050）于2024-12-31披露持有工业富联（601138.SH），持仓市值占资产净值比5.33%。",
        kind="holding_disclosure", observed_period=bundle.fund.report_period,
    )

    expression = MarketExpressionService(session)
    claim = expression.register_report_claim(case_id, ReportClaimInput(
        source_statement_id=forecast_statement.id,
        text="海通证券预计工业富联2024年归母净利润251.49亿元。",
        claim_kind="forecast", asserted_period=date(2024, 12, 31), asserted_by="海通证券",
        reviewed_by=ACTOR, review_reason="演示人审：将研报预测与公司实际值分开冻结。",
    ))
    forecast_factor = expression.register_key_factor(case_id, KeyFactorInput(
        report_claim_id=claim.id, thesis_id=None, name="2024 年归母净利润预测兑现",
        expected_direction="positive", metric_name="归母净利润", allowed_source_types=["company_disclosure"],
        verification_window_start=date(2024, 1, 1), verification_window_end=date(2024, 12, 31),
        support_condition="年度报告实际归母净利润与预测值的相对差异不超过 10%。",
        refutation_condition="年度报告实际归母净利润与预测值的相对差异超过 10%。",
        next_verification_event="工业富联 2024 年年度报告", reviewed_by=ACTOR,
        review_reason="演示人审：容差和比较口径在读取实际值前已冻结。",
    ))
    driver_claim = expression.register_report_claim(case_id, ReportClaimInput(
        source_statement_id=report_factor_statement.id,
        text="研报标题表达 AI 业务表现强劲的研究观点。",
        claim_kind="research_opinion", asserted_period=None, asserted_by="海通证券",
        reviewed_by=ACTOR, review_reason="演示人审：保留为研报观点，不升级为已证实事实。",
    ))
    driver_factor = expression.register_key_factor(case_id, KeyFactorInput(
        report_claim_id=driver_claim.id, thesis_id=None, name="AI 服务器与高速交换机增长",
        expected_direction="positive", metric_name="AI服务器收入及高速交换机业务", allowed_source_types=["company_disclosure"],
        verification_window_start=date(2024, 1, 1), verification_window_end=date(2024, 12, 31),
        support_condition="年报披露 AI 服务器收入和高速交换机业务同比增长。",
        refutation_condition="年报未支持上述增长或披露反向变化。",
        next_verification_event="工业富联 2024 年年度报告", reviewed_by=ACTOR,
        review_reason="演示人审：因素以可被年报核验的业务指标表达。",
    ))
    expression.register_claim_verification(case_id, forecast_factor.id, ClaimVerificationInput(
        source_statement_id=actual_statement.id, outcome="supported",
        rationale="年度报告披露归母净利润232.16亿元；相对冻结预测251.49亿元偏差约7.69%，落在预设10%容差内。",
        reviewed_by=ACTOR, review_reason="演示人审：因素核验与数值裁决指向同一冻结年报，但保留为独立记录。",
    ))
    expression.register_claim_verification(case_id, driver_factor.id, ClaimVerificationInput(
        source_statement_id=driver_statement.id, outcome="supported",
        rationale="年度报告披露云计算业务收入同比增长64.37%、AI服务器收入同比超过150%，且400G、800G高速交换机同比增长数倍。",
        reviewed_by=ACTOR, review_reason="演示人审：该验证仅支持业务因素，不单独证明股价因果。",
    ))

    instruments = InstrumentService(session)
    company = instruments.create_company(code="601138", name="工业富联", type="listed_company")
    stock = instruments.add_stock(company=company, code="601138.SH", name="工业富联", market="SSE")
    binding = expression.register_market_instrument_binding(case_id, MarketInstrumentBindingInput(
        company_id=company.id, stock_id=stock.id, source_statement_id=forecast_statement.id,
        relationship_role="directly_affected", reviewed_by=ACTOR,
        review_reason="演示人审：研报标题和预测对象均为工业富联（601138）。",
    ))
    for factor, rationale in (
        (forecast_factor, "年度报告披露的归母净利润为232.16亿元，是预测兑现比较的实际观测。"),
        (driver_factor, "年度报告披露的AI服务器和高速交换机增长是业务因素的可核验观测。"),
    ):
        expression.register_fundamental_impact(case_id, factor.id, FundamentalImpactInput(
            market_instrument_binding_id=binding.id, source_statement_id=actual_statement.id if factor.id == forecast_factor.id else driver_statement.id,
            metric_name="归母净利润" if factor.id == forecast_factor.id else "AI服务器及高速交换机业务",
            expected_direction="positive", rationale=rationale, reviewed_by=ACTOR,
            review_reason="演示人审：记录基本面映射，不生成投资建议或股价因果结论。",
        ))
    fund = instruments.create_fund(code=bundle.fund.code, name="华夏中证5G通信主题交易型开放式指数证券投资基金", fund_type="ETF")
    holding_span = session.get(SourceSpan, holding_statement.source_span_id)
    instruments.add_holding_disclosure(
        fund=fund, stock=stock, weight=bundle.fund.position_weight,
        report_period=bundle.fund.report_period, published_at=bundle.fund_report.published_at,
        source="Gildata FinQuery / 515050 2024Q4 disclosure", source_document_version_id=holding_doc.id,
        source_span_id=holding_span.id if holding_span else None, coverage_status="partial",
    )

    verdicts = ForecastVerdictService(session)
    target = verdicts.create_target(case_id, ForecastTargetInput(
        key_factor_id=forecast_factor.id, report_claim_id=claim.id,
        forecast_source_statement_id=forecast_statement.id, baseline_source_statement_id=baseline_statement.id,
        metric_name="归母净利润", entity_key="601138.SH", baseline_value=bundle.baseline_profit,
        expected_value=bundle.expected_profit, unit="CNY", forecast_period_start=date(2024, 1, 1),
        forecast_period_end=date(2024, 12, 31), comparator="within_tolerance", relative_tolerance=Decimal("0.10"),
        reviewed_by=ACTOR, review_reason="演示人审：预测、2023基线、口径和10%容差均冻结。",
    ))
    actual = verdicts.record_actual(target.id, ActualObservationInput(
        source_statement_id=actual_statement.id, entity_key="601138.SH", observed_value=bundle.actual_profit,
        unit="CNY", observed_period_start=date(2024, 1, 1), observed_period_end=date(2024, 12, 31),
        available_at=bundle.annual_report.published_at, recorded_by=ACTOR,
        record_reason="演示人审：公司2024年年度报告披露的归母净利润。",
    ))
    candidate = verdicts.evaluate(target.id, actual.id, cutoff=_now())
    verdict = verdicts.create_verdict(candidate.id, ForecastVerdictInput(
        decision="confirmed", outcome=None,
        reason="演示人审确认：实际值232.16亿元相对预测251.49亿元偏差约7.69%，处于10%容差内；不据此推断股票价格因果。",
        reviewed_by=ACTOR,
    ))
    session.commit()
    return LiveCaseResult(
        case_id=case_id, verdict_id=verdict.id, expected_value=str(bundle.expected_profit),
        baseline_value=str(bundle.baseline_profit), actual_value=str(bundle.actual_profit), outcome=verdict.outcome,
    )
