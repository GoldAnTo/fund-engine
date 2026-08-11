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

from app.models.ledger import DocumentVersion, EvidenceLink, ResearchCase, SourceSpan, SourceStatement, Thesis
from app.models.operational import EventResearchLifecycle
from app.models.research_protocol import MechanismEdgeVersion, MechanismNodeVersion
from app.models.research_expression import KeyFactor, MarketInstrumentBinding, MarketObservation
from app.repositories.documents import DocumentRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.event_research import EventResearchService
from app.services.event_conclusion import EventConclusionService
from app.services.event_research_scope_evidence import append_current_scope_evidence_assignment
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
    MarketObservationInput,
    ReportClaimInput,
)
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import (
    MetricDefinitionInput,
    OutcomeBindingInput,
    ResearchProtocolService,
    VerificationRuleInput,
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


def _publish_bounded_conclusion(
    session: Session,
    *,
    case_id: uuid.UUID,
    actual_statement: SourceStatement,
    driver_statement: SourceStatement,
    actual_available_at: datetime,
) -> None:
    """Map reviewed evidence to every active factor, then publish a narrow verdict.

    The generic event workbench only permits publication after every current
    scope factor has a reviewed, mapped link.  This case therefore enters the
    same lifecycle rather than flipping its status around the conclusion gate.
    """
    theses = {
        thesis.statement: thesis
        for thesis in session.scalars(
            select(Thesis).where(Thesis.research_case_id == case_id)
        )
    }
    evidence_inputs = (
        (
            "2024 年归母净利润预测",
            actual_statement,
            "年度报告实际归母净利润用于核验预设10%容差内的预测兑现情况。",
        ),
        (
            "AI 服务器收入",
            driver_statement,
            "年度报告披露AI服务器收入同比超过150%，支持该业务因素。",
        ),
        (
            "800G 高速交换机",
            driver_statement,
            "年度报告披露400G、800G高速交换机同比增长数倍，支持该业务因素。",
        ),
    )
    for factor_statement, statement, reason in evidence_inputs:
        thesis = theses.get(factor_statement)
        if thesis is None:
            raise ValueError(f"live case is missing active thesis: {factor_statement}")
        link = EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason=reason,
            scope={"case": "industrial-foxconn-forecast-v1", "factor": factor_statement},
            available_at=actual_available_at,
            creator_type="human",
            review_state="reviewed",
            created_at=_now(),
        )
        session.add(link)
        session.flush()
        append_current_scope_evidence_assignment(
            session,
            case_id=case_id,
            evidence_link_id=link.id,
            factor_statement=factor_statement,
            created_at=_now(),
        )

    conclusion_service = EventConclusionService(session)
    conclusion_service.create_draft(case_id)
    lifecycle = session.get(EventResearchLifecycle, case_id)
    if lifecycle is None:
        raise ValueError("live case lifecycle is missing")
    lifecycle.status = "draft_ready"
    lifecycle.status_summary = "历史预测验证资料已映射，等待演示人审发布范围受限结论"
    lifecycle.current_gap = None
    lifecycle.next_human_action = "发布范围受限的历史预测验证结论"
    lifecycle.updated_at = _now()
    conclusion_service.publish(
        case_id,
        reviewer=ACTOR,
        text=(
            "在本 Case 的冻结资料范围内，海通证券对工业富联2024年归母净利润251.49亿元的预测"
            "得到支持：公司年报实际为232.16亿元，较预测偏差约7.69%，处于预设10%容差内。"
            "年报同时支持AI服务器和高速交换机增长因素。该结论不评估股票事件窗口，"
            "不据此推断股票价格因果或构成投资建议；基金515050仅为截至2024-12-31的部分历史持仓披露。"
        ),
    )


def _configure_demo_research_protocol(
    session: Session,
    *,
    case_id: uuid.UUID,
    forecast_thesis_id: uuid.UUID,
    report_document: DocumentVersion,
    company_id: uuid.UUID,
) -> None:
    """Freeze the protocol that makes the demo's manual replenishment runnable.

    The historical verdict remains a narrow result.  This protocol only
    governs a later, explicitly requested evidence-refresh run: metric,
    baseline source, mechanism path, counter-hypothesis rule and time window
    all remain inspectable on the Case.
    """
    service = ResearchProtocolService(session)
    metrics = [
        service.add_metric_version(
            MetricDefinitionInput(
                metric_id="industrial_foxconn_profit",
                display_name="归母净利润",
                canonical_definition="工业富联归属于上市公司股东的年度净利润。",
                entity_scope="company",
                unit="CNY",
                frequency="annual",
                period_semantics="fiscal_year",
                allowed_source_roles=["company_disclosure"],
                role_eligibility=["outcome", "driver"],
            ),
            approved_by=ACTOR,
            reason="演示人审：预测兑现以公司年报归母净利润为结果指标。",
        ),
        service.add_metric_version(
            MetricDefinitionInput(
                metric_id="industrial_foxconn_ai_order_delivery",
                display_name="AI服务器订单与交付",
                canonical_definition="工业富联AI服务器相关订单、交付或出货的公司披露指标。",
                entity_scope="company",
                unit="disclosed_value",
                frequency="annual",
                period_semantics="fiscal_year",
                allowed_source_roles=["company_disclosure"],
                role_eligibility=["driver"],
            ),
            approved_by=ACTOR,
            reason="演示人审：订单和交付需与收入结果分开观察。",
        ),
        service.add_metric_version(
            MetricDefinitionInput(
                metric_id="industrial_foxconn_ai_revenue",
                display_name="AI服务器及高速交换机业务收入",
                canonical_definition="工业富联披露的AI服务器及高速交换机业务收入或同比变化。",
                entity_scope="company",
                unit="disclosed_value",
                frequency="annual",
                period_semantics="fiscal_year",
                allowed_source_roles=["company_disclosure"],
                role_eligibility=["driver"],
            ),
            approved_by=ACTOR,
            reason="演示人审：AI业务因素以公司披露的收入和同比变化核验。",
        ),
    ]
    template = seed_ai_capex_template(session)
    service.select_template(
        case_id,
        template.id,
        reviewer=ACTOR,
        reason="演示人审：采用已审核AI硬件机制模板，且不把模板本身当作案例结论。",
    )
    nodes = {
        node.id: node
        for node in session.scalars(
            select(MechanismNodeVersion).where(
                MechanismNodeVersion.template_version_id == template.id
            )
        )
    }
    required_edges = [
        edge
        for edge in session.scalars(
            select(MechanismEdgeVersion)
            .where(MechanismEdgeVersion.template_version_id == template.id)
            .order_by(MechanismEdgeVersion.edge_key)
        )
        if nodes[edge.target_node_id].role in {"required_for_outcome", "required_for_attribution"}
    ]
    for index, edge in enumerate(required_edges):
        metric = metrics[min(index, len(metrics) - 1)]
        service.add_verification_rule(
            case_id,
            edge.id,
            VerificationRuleInput(
                metric_definition_id=metric.id,
                expected_direction="increase",
                support_predicate="公司披露在冻结观察期内出现与该机制边一致的正向指标。",
                contradiction_predicate="公司披露与该机制边预期相反，或无法支持该传导。",
                allowed_source_roles=["company_disclosure"],
                observed_period_start=date(2024, 1, 1),
                observed_period_end=date(2025, 12, 31),
                available_at_deadline=date(2026, 4, 30),
                next_verification_event="工业富联后续定期报告与AI业务披露",
                reviewer=ACTOR,
                reason="演示人审：每条必要机制边都有支持与反证条件。",
            ),
        )
    baseline = {
        "source_ref": f"document:{report_document.id}",
        "value": "21040000000",
        "unit": "CNY",
        "observed_period": "2023-12-31",
        "available_at": report_document.available_at.isoformat(),
    }
    binding = service.create_outcome_binding(
        forecast_thesis_id,
        OutcomeBindingInput(
            metric_definition_id=metrics[0].id,
            entity_scope={"company_id": str(company_id), "company": "601138"},
            direction="increase",
            baseline=baseline,
            horizon_start=date(2024, 1, 1),
            horizon_end=date(2024, 12, 31),
            reviewer=ACTOR,
            reason="演示人审：冻结2023年基线、2024年预测窗口及年报可得时点。",
        ),
    )
    service.approve_outcome_binding(
        binding.id,
        reviewer=ACTOR,
        reason="演示人审：确认结果指标、来源、公司范围与观察期。",
    )


def _register_market_window(
    session: Session,
    *,
    case_id: uuid.UUID,
    expression: MarketExpressionService,
    forecast_factor: KeyFactor,
    binding: MarketInstrumentBinding,
    bundle: IndustrialFoxconnSourceBundle,
) -> MarketObservation:
    """Append one source-bound daily price observation, never a causal verdict."""
    existing = session.scalar(
        select(MarketObservation)
        .where(MarketObservation.research_case_id == case_id)
        .where(MarketObservation.key_factor_id == forecast_factor.id)
        .where(MarketObservation.window_label == "2025-04-29 收盘至 2025-04-30 收盘")
        .limit(1)
    )
    if existing is not None:
        return existing
    market_doc = _admit_document(
        session, case_id=case_id, title=bundle.market_window.snapshot.title,
        published_at=bundle.market_window.snapshot.published_at,
        raw_response=bundle.market_window.snapshot.raw_response,
        tool=bundle.market_window.snapshot.tool, query=bundle.market_window.snapshot.query,
        authority="licensed_market_data",
    )
    market_statement = _statement(
        session, document=market_doc, label="2025-04-30-daily-price-window",
        quote=bundle.market_window.snapshot.content,
        kind="market_observation", observed_period=date(2025, 4, 30),
    )
    return expression.register_market_observation(case_id, forecast_factor.id, MarketObservationInput(
        market_instrument_binding_id=binding.id,
        source_statement_id=market_statement.id,
        event_at=bundle.market_window.event_at,
        available_at=bundle.market_window.event_at,
        window_label="2025-04-29 收盘至 2025-04-30 收盘",
        benchmark="中证全指 000985",
        price_source="Gildata FinQuery 历史日度行情",
        after_hours_treatment=(
            "日度价格窗口从前一交易日收盘至2025-04-30收盘；"
            "不将同日年度报告与该价格表现建立因果。"
        ),
        relative_return=bundle.market_window.relative_return,
        reviewed_by=ACTOR,
        review_reason=(
            "演示人审：工业富联前复权收盘价17.44元至17.41元，"
            "中证全指4649.04点至4666.80点；仅记录相对窗口表现。"
        ),
    ))


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

    case_theses = {
        thesis.statement: thesis.id
        for thesis in session.scalars(
            select(Thesis).where(Thesis.research_case_id == case_id)
        )
    }
    forecast_thesis_id = case_theses.get("2024 年归母净利润预测")
    driver_thesis_id = case_theses.get("AI 服务器收入")
    if forecast_thesis_id is None or driver_thesis_id is None:
        raise ValueError("live case is missing a reviewed scope thesis for a registered key factor")

    expression = MarketExpressionService(session)
    claim = expression.register_report_claim(case_id, ReportClaimInput(
        source_statement_id=forecast_statement.id,
        text="海通证券预计工业富联2024年归母净利润251.49亿元。",
        claim_kind="forecast", asserted_period=date(2024, 12, 31), asserted_by="海通证券",
        reviewed_by=ACTOR, review_reason="演示人审：将研报预测与公司实际值分开冻结。",
    ))
    forecast_factor = expression.register_key_factor(case_id, KeyFactorInput(
        report_claim_id=claim.id, thesis_id=forecast_thesis_id, name="2024 年归母净利润预测兑现",
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
        report_claim_id=driver_claim.id, thesis_id=driver_thesis_id, name="AI 服务器与高速交换机增长",
        expected_direction="positive", metric_name="AI服务器收入及高速交换机业务", allowed_source_types=["company_disclosure"],
        verification_window_start=date(2024, 1, 1), verification_window_end=date(2024, 12, 31),
        support_condition="年报披露 AI 服务器收入和高速交换机业务同比增长。",
        refutation_condition="年报未支持上述增长或披露反向变化。",
        next_verification_event="工业富联 2024 年年度报告", reviewed_by=ACTOR,
        review_reason="演示人审：因素以可被年报核验的业务指标表达。",
    ))
    CaseMonitorService(session).save(
        case_id,
        actor=ACTOR,
        config=CaseMonitorConfig(
            frequency="daily_20_00",
            factor_ids=[forecast_thesis_id, driver_thesis_id],
            allowed_source_types=["company_disclosure"],
            next_verification_event="工业富联后续定期报告与AI业务披露",
            budget=20,
            change_reason="演示 Case 初始配置：仅按已审核关键因素补充公司披露。",
        ),
    )
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
    _configure_demo_research_protocol(
        session,
        case_id=case_id,
        forecast_thesis_id=forecast_thesis_id,
        report_document=report_doc,
        company_id=company.id,
    )
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
    _register_market_window(
        session, case_id=case_id, expression=expression,
        forecast_factor=forecast_factor, binding=binding, bundle=bundle,
    )
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
    _publish_bounded_conclusion(
        session,
        case_id=case_id,
        actual_statement=actual_statement,
        driver_statement=driver_statement,
        actual_available_at=bundle.annual_report.published_at,
    )
    session.commit()
    return LiveCaseResult(
        case_id=case_id, verdict_id=verdict.id, expected_value=str(bundle.expected_profit),
        baseline_value=str(bundle.baseline_profit), actual_value=str(bundle.actual_profit), outcome=verdict.outcome,
    )


def append_live_industrial_foxconn_market_window(
    session: Session,
    *,
    bundle: IndustrialFoxconnSourceBundle,
) -> MarketObservation:
    """Append the approved source-bound price window to the existing demo Case.

    This is intentionally idempotent and cannot alter the historical forecast
    verdict, conclusion, fund disclosure, or any prior observation.
    """
    case = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE).limit(1))
    if case is None:
        raise ValueError("local demonstration case does not exist")
    factor = session.scalar(
        select(KeyFactor)
        .where(KeyFactor.research_case_id == case.id)
        .where(KeyFactor.name == "2024 年归母净利润预测兑现")
        .where(KeyFactor.review_state == "reviewed")
        .limit(1)
    )
    binding = session.scalar(
        select(MarketInstrumentBinding)
        .where(MarketInstrumentBinding.research_case_id == case.id)
        .where(MarketInstrumentBinding.review_state == "reviewed")
        .where(MarketInstrumentBinding.stock_id.is_not(None))
        .limit(1)
    )
    if factor is None or binding is None:
        raise ValueError("local demonstration case is missing its reviewed stock expression chain")
    record = _register_market_window(
        session, case_id=case.id, expression=MarketExpressionService(session),
        forecast_factor=factor, binding=binding, bundle=bundle,
    )
    session.commit()
    return record
