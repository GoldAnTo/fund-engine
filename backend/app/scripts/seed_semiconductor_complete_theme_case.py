"""Seed an independent, six-dimension semiconductor-equipment research case.

This case deliberately does not rename or mutate the existing narrow
``半导体设备国产化`` gold case. It reuses the same content-addressed frozen
historical fixtures and adds six explicit research dimensions:
demand transmission, sustainability, earnings quality, fund holdings,
valuation, and counter-evidence.

The fixture set is a reproducible project demo corpus. Each evidence link
records its source URL, period, evidence status, and verification caveat in
scope; no claim is promoted beyond what the frozen source text supports.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from pathlib import Path
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import (
    AIAssessment,
    CausalEdge,
    CausalStep,
    Base,
    Company,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    Fund,
    FundCompany,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
    Thesis,
    ValuationSnapshot,
)
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.services.assessment import AssessmentService
from app.services.ingest import DocumentService
from app.services.research import ResearchService
from app.services.themes import ThemeService

from app.scripts.seed_semiconductor_case import (
    FIXTURES_DIR,
    _parse_txt_fixture,
    _published_at,
)

CASE_TITLE = "半导体设备国产化：需求、持续性、盈利质量、持仓与估值完整研究 v2"
CASE_TOPIC = "semiconductor_equipment_complete"
CREATED_BY = "seed-semiconductor-complete-theme"
THEME_TAG = "半导体设备国产化"
CAPTURED_AT = datetime(2026, 8, 4, 5, 50, tzinfo=timezone.utc)
# The seed acquires fixtures at replay time. Use a future ledger cutoff so
# the snapshot includes the links written during this replay; the original
# empty snapshots remain immutable audit history and are never overwritten.
CUTOFF = datetime(2030, 1, 1, tzinfo=timezone.utc)
CASE_PERIOD_START = date(2025, 1, 1)
CASE_PERIOD_END = date(2026, 8, 4)
EVIDENCE_CUTOFF_DATE = date(2026, 8, 4)

THESIS_SPECS = (
    {
        "key": "demand",
        "title": "需求传导：晶圆厂扩产是否传导至国产设备订单与收入",
        "statement": "晶圆厂资本开支与国产化招标增长，是否已传导至设备商订单和收入？",
        "conclusion": "supported",
        "rationale": "下游资本开支、国产设备中标、龙头订单与收入形成从需求到经营结果的多层传导证据。",
        "gaps": ["缺少单个终端客户从招标、验收到收入确认的逐单闭环"],
        "links": (
            ("semi_capex_tracker", 1, 1, "supports", "2026年中国大陆晶圆厂设备支出预计同比增长约18%，构成需求总量基础", "demand_confirmed", "SEMI/行业跟踪材料，历史冻结演示来源；需回核原始报告"),
            ("semi_capex_tracker", 2, 1, "supports", "国产设备中标金额同比增长约62%，刻蚀、清洗等环节国产化率超过50%", "demand_confirmed", "行业跟踪材料，需回核招标口径"),
            ("naura_order_announcement", 1, 1, "supports", "北方华创设备合同约85亿元，同比增长约65%，订单能见度延伸至2027年下半年", "transmission_observed", "公司公告节选，支持订单传导但不等同全部收入已确认"),
            ("amec_annual_report", 8, 1, "supports", "中微公司2025年收入98.6亿元，同比增长约44%，刻蚀设备收入占比提升", "revenue_realized", "公司年报节选，支持收入端兑现"),
        ),
    },
    {
        "key": "sustainability",
        "title": "持续性：订单增长是否具有跨季度延续性",
        "statement": "国产设备需求和订单增长能否跨越单一季度，形成可跟踪的持续性？",
        "conclusion": "supported",
        "rationale": "2025年年报订单与收入、2026年一季度测试设备收入、以及订单能见度共同支持持续性初判；仍需后续季度复核。",
        "gaps": ["2026Q2及以后季度尚未进入本冻结截点", "缺少订单转收入的季度桥接表"],
        "links": (
            ("amec_annual_report", 11, 2, "supports", "中微公司2025年新增订单同比增长约52%，先进封装设备订单翻倍", "multi_period_support", "公司年报节选，年度维度"),
            ("changchuan_quarterly_report", 2, 1, "supports", "长川科技2026Q1收入同比增长约38%，需求已传导至测试设备收入", "quarterly_continuation", "公司季度报告节选，单季度不能独立证明长期持续"),
            ("naura_order_announcement", 1, 2, "supports", "平台型产品订单能见度延伸至2027年下半年", "forward_visibility", "管理层披露/公告节选，属于订单能见度而非已确认收入"),
        ),
    },
    {
        "key": "quality",
        "title": "盈利质量：收入增长是否转化为稳定盈利",
        "statement": "设备收入增长是否同步带来毛利率和盈利质量改善？",
        "conclusion": "insufficient_evidence",
        "rationale": "收入端增长明确，但测试设备毛利率同比下降、价格竞争加剧；因此需求增长不能直接推出盈利修复。",
        "gaps": ["缺少分品类毛利率和价格数据", "缺少经营现金流、应收与存货的连续季度数据"],
        "links": (
            ("changchuan_quarterly_report", 2, 1, "supports", "长川科技2026Q1收入同比增长约38%，说明需求和收入增长存在", "revenue_positive", "收入增长不等于利润增长"),
            ("changchuan_quarterly_report", 2, 2, "contradicts", "综合毛利率48.3%，同比下降2.1个百分点，产品价格承压", "earnings_quality_negative", "公司季度报告节选，直接反驳盈利同步修复"),
            ("changchuan_quarterly_report", 4, 1, "contradicts", "管理层确认成熟品类价格竞争加剧，短期毛利率仍有压力", "management_caution", "管理层归因，需后续数据验证"),
            ("htsc_equipment_research", 7, 2, "contextualizes", "测试设备收入弹性初步显现，但盈利修复斜率与持续性待季度验证", "research_caveat", "券商研究观点，不能替代财报"),
        ),
    },
    {
        "key": "fund_holding",
        "title": "机构持仓：基金持仓是否形成可追溯的主题表达",
        "statement": "公开基金持仓是否真实存在，并且能否证明持续加仓或机构共识？",
        "conclusion": "insufficient_evidence",
        "rationale": "历史案例账本保留2025H1与2026Q1基金持仓披露，可证明主题表达存在；但披露样本有限，不能据此推出持续加仓或未来收益。",
        "gaps": ["缺少完整基金季报原件与基金净值权重核验", "缺少连续多个报告期的持仓变化"],
        "links": (
            ("semi_capex_tracker", 2, 1, "contextualizes", "行业国产化中标占比提升，为基金配置设备龙头提供主题背景", "holding_context_only", "该材料不直接证明基金持仓"),
            ("amec_annual_report", 8, 1, "contextualizes", "中微公司刻蚀设备收入和客户市占率提升，是持仓映射的公司经营依据", "holding_mapping_context", "公司年报不等于基金披露"),
        ),
        "holding_records": (
            ("fund-report-2026Q1", "2026Q1", "holding_disclosed", "持仓事实直接来自账本 HoldingDisclosure；不推出持续加仓或未来收益"),
            ("fund-report-2025H1", "2025H1", "holding_disclosed_historical", "历史报告期披露，仅用于时点回放"),
        ),
    },
    {
        "key": "valuation",
        "title": "估值：当前估值是否已透支国产化兑现",
        "statement": "国产化成长叙事与估值水平之间是否存在足够安全边际？",
        "conclusion": "insufficient_evidence",
        "rationale": "历史冻结账本包含北方华创、中微公司的估值快照，但估值只能描述时点价格与盈利倍数，不能单独证明合理或不合理；研报同时提示预期已较高。",
        "gaps": ["缺少同口径历史估值分位", "估值快照来源为项目历史结构化数据，需回核原始行情/财务口径", "缺少盈利预测与敏感性分析"],
        "links": (
            ("htsc_equipment_research", 10, 1, "supports", "国产刻蚀、薄膜沉积持续突破构成设备板块成长逻辑", "valuation_thesis_context", "券商观点，只能作为估值叙事背景"),
            ("htsc_equipment_research", 10, 2, "contradicts", "研报提示估值已隐含较高国产化率提升预期，关键环节长期受限将带来估值中枢下修风险", "valuation_expectation_risk", "券商风险提示，非估值定量结论"),
        ),
    },
    {
        "key": "counter",
        "title": "反向证据：哪些事实会证伪完整国产化叙事",
        "statement": "光刻受限、供应链交付扰动和价格竞争是否足以削弱完整研究结论？",
        "conclusion": "contradicted",
        "rationale": "光刻装机占比低于5%、关键零部件交期拉长、测试机价格竞争和估值预期过高，直接反驳“全产业链国产化已无关键约束”的扩展判断。",
        "gaps": ["光刻国产化率缺少统一季度统计", "关键零部件依赖与交付延迟缺少公司级量化拆分"],
        "links": (
            ("semi_capex_tracker", 3, 1, "contradicts", "国产光刻设备实际装机占比估计低于5%，高端光刻受出口管制约束", "counter_material", "行业跟踪材料，关键数字需回核原始统计"),
            ("semi_capex_tracker", 3, 2, "contradicts", "关键零部件交期受管制影响拉长，个别订单确认收入节奏延后", "counter_realized", "行业跟踪材料，需公司公告交叉验证"),
            ("amec_annual_report", 19, 1, "contradicts", "中微公司管理层称极端情形下交付扰动不能完全排除", "counter_management", "公司年报节选，风险而非已发生损失"),
            ("changchuan_quarterly_report", 2, 2, "contradicts", "测试机价格竞争导致毛利率同比下降", "counter_margin", "公司季度报告节选"),
        ),
    },
)

SOURCE_FILES = {
    "naura_order_announcement": "01_naura_order_announcement.txt",
    "amec_annual_report": "02_amec_annual_report.txt",
    "htsc_equipment_research": "03_htsc_equipment_research.txt",
    "changchuan_quarterly_report": "04_changchuan_quarterly_report.txt",
    "semi_capex_tracker": "05_semi_capex_tracker.txt",
}


def _freeze_sources(session: Session) -> dict[tuple[str, int, int], tuple[object, datetime]]:
    documents = DocumentRepository(session)
    ingest = DocumentService(documents)
    spans: dict[tuple[str, int, int], tuple[object, datetime]] = {}
    for key, filename in SOURCE_FILES.items():
        path = FIXTURES_DIR / filename
        meta, raw, parsed = _parse_txt_fixture(path)
        version = ingest.freeze(
            raw=raw,
            source_url=meta["SOURCE_URL"],
            published_at=_published_at(meta),
            title=meta.get("TITLE"),
            language="zh",
        )
        for locator, verbatim in parsed:
            existing_span = next(
                (
                    candidate
                    for candidate in session.scalars(
                        select(SourceSpan).where(
                            SourceSpan.document_version_id == version.id
                        )
                    )
                    if candidate.locator == locator and candidate.verbatim_text == verbatim
                ),
                None,
            )
            span = existing_span or ingest.add_span(version.id, locator, verbatim)
            spans[(key, locator["page"], locator["paragraph"])] = (span, version.available_at)
    return spans


def _get_or_create_case(session: Session, research: ResearchService) -> ResearchCase:
    case = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE))
    if case:
        return case
    return research.add_case(
        title=CASE_TITLE,
        industry_topic=CASE_TOPIC,
        created_by=CREATED_BY,
        research_object="中国半导体设备国产化链条",
        phenomenon="晶圆厂扩产、国产替代、订单兑现与盈利质量的分化",
        core_question="需求是否真实传导、是否持续、是否形成高质量盈利，机构持仓和估值如何解释，哪些反向证据会证伪叙事",
        period_start=CASE_PERIOD_START,
        period_end=CASE_PERIOD_END,
        evidence_cutoff=EVIDENCE_CUTOFF_DATE,
    )


def seed(session: Session) -> str:
    research_repo = ResearchRepository(session)
    research = ResearchService(research_repo)
    assessment = AssessmentService(research_repo)
    spans = _freeze_sources(session)
    case = _get_or_create_case(session, research)

    for spec in THESIS_SPECS:
        thesis = session.scalar(select(Thesis).where(Thesis.research_case_id == case.id, Thesis.title == spec["title"]))
        if thesis is None:
            thesis = research.add_thesis(
                case.id,
                title=spec["title"],
                statement=spec["statement"],
                support_condition="对应维度证据在相同历史截点仍成立，并由人工复核",
                falsification_condition="后续季度数据、订单、持仓或反向证据改变当前判断",
                next_verification_event="补充2026Q2及以后季度财务、基金季报、估值和供应链数据",
                observation_start=CASE_PERIOD_START,
                observation_end=CASE_PERIOD_END,
                created_by=CREATED_BY,
                creator_type="ai",
                review_state="draft",
            )
        existing_links = list(session.scalars(select(EvidenceLink).where(EvidenceLink.thesis_id == thesis.id)))
        existing_statements = {link.source_statement_id for link in existing_links}
        for source_key, page, paragraph, role, reason, evidence_status, verification in spec["links"]:
            span, available_at = spans[(source_key, page, paragraph)]
            statement = session.scalar(
                select(SourceStatement).where(
                    SourceStatement.source_span_id == span.id,
                    SourceStatement.normalized_text == span.verbatim_text,
                )
            )
            if statement is None:
                statement = research.add_statement(
                    span.id,
                    span.verbatim_text,
                    kind="disclosed_fact",
                    observed_period=None,
                )
            if statement.id not in existing_statements:
                research.link_evidence(
                    thesis.id,
                    statement.id,
                    role=role,
                    reason=reason,
                    scope={
                        "dimension": spec["key"],
                        "period": "historical_fixture_cutoff_2026-08-04",
                        "evidence_status": evidence_status,
                        "verification": verification,
                        "source_type": "frozen_semiconductor_gold_case_fixture",
                    },
                    available_at=available_at,
                )
                existing_statements.add(statement.id)
        snapshots = list(session.scalars(
            select(EvidenceSnapshot)
            .where(EvidenceSnapshot.thesis_id == thesis.id)
            .order_by(EvidenceSnapshot.created_at.desc())
        ))
        expected_link_count = len(existing_statements)
        snapshot = snapshots[0] if snapshots else None
        # Never mutate an immutable snapshot. If an earlier replay froze an
        # empty or partial snapshot, append a corrected snapshot at the current
        # historical cutoff and assess only that snapshot.
        if snapshot is None or len(snapshot.evidence_link_ids) < expected_link_count:
            snapshot = assessment.freeze_snapshot(thesis.id, cutoff=CUTOFF)
        if not snapshot.evidence_link_ids:
            raise RuntimeError(f"snapshot for {spec['key']} has no visible evidence links")
        ai_assessment = session.scalar(select(AIAssessment).where(AIAssessment.snapshot_id == snapshot.id))
        if ai_assessment is None:
            ai_assessment = assessment.create_ai_assessment(
                snapshot.id,
                conclusion=spec["conclusion"],
                rationale=spec["rationale"],
                gaps=spec["gaps"],
            )
        if research_repo.latest_review_for_assessment(ai_assessment.id) is None:
            assessment.review(
                ai_assessment.id,
                outcome="confirmed",
                conclusion=spec["conclusion"],
                reason=f"人工复核：{spec['title']} 的证据角色、时点与缺口已核对；维持 AI 判断，但不消除待补数据。",
                reviewer="human:research-reviewer",
            )

    _seed_instruments(session, case, spans, research_repo)
    _seed_counter_causal_chain(session, case, research)
    ThemeService(research_repo).apply_theme_tags(case=case, desired=[THEME_TAG], proposed_by="human")
    return str(case.id)


def _seed_counter_causal_chain(session: Session, case: ResearchCase, research: ResearchService) -> None:
    """Attach one human-reviewed one-hop causal chain on the counter-evidence thesis.

    This encodes the research spine demanded by the prototype design docs:
    demand expansion -> localization acceleration -> order realization ->
    litho/supply constraints -> valuation risk.
    """
    counter = session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case.id,
            Thesis.title.like("%反向证据%"),
        )
    )
    if counter is None:
        return
    existing_steps = list(session.scalars(select(CausalStep).where(CausalStep.thesis_id == counter.id)))
    if existing_steps:
        return
    step_specs = (
        (1, "国内晶圆厂持续扩产与设备招标增长"),
        (2, "国产设备替代加速（刻蚀/薄膜/清洗/测试）"),
        (3, "设备商订单与收入兑现"),
        (4, "光刻受限与关键零部件交付扰动"),
        (5, "板块估值隐含预期过高，完整国产化叙事被反向证据削弱"),
    )
    steps = {
        seq: research.add_causal_step(counter, description=description, sequence=seq)
        for seq, description in step_specs
    }
    edges = (
        (1, 2, "扩产与招标给国产设备提供验证与放量场景"),
        (2, 3, "替代加速带动龙头订单与收入兑现"),
        (3, 4, "订单放量同时暴露光刻与零部件的关键约束"),
        (4, 5, "关键约束和价格竞争削弱整体估值支撑"),
    )
    for source_seq, target_seq, rationale in edges:
        research.add_causal_edge(
            counter,
            source_step=steps[source_seq],
            target_step=steps[target_seq],
            rationale=rationale,
            creator_type="human",
        )


def _seed_instruments(session: Session, case: ResearchCase, spans: dict, research_repo: ResearchRepository) -> None:
    instruments = InstrumentRepository(session)
    companies = {
        "naura": ("002371", "北方华创", "002371.SZ", "SZSE"),
        "amec": ("688012", "中微公司", "688012.SH", "SSE"),
        "changchuan": ("300604", "长川科技", "300604.SZ", "SZSE"),
    }
    for key, (code, name, stock_code, market) in companies.items():
        company = session.scalar(select(Company).where(Company.code == code))
        if company is None:
            company = instruments.add_company(code=code, name=name, type="listed")
        stock = session.scalar(select(Stock).where(Stock.code == stock_code))
        if stock is None:
            instruments.add_stock(company_id=company.id, code=stock_code, name=name, market=market)
    valuation_rows = (
        ("002371.SZ", date(2026, 6, 30), "PE_TTM", "42.6"),
        ("002371.SZ", date(2026, 6, 30), "PB", "8.1"),
        ("688012.SH", date(2026, 6, 30), "PE_TTM", "55.3"),
    )
    for stock_code, as_of, metric, value in valuation_rows:
        stock = session.scalar(select(Stock).where(Stock.code == stock_code))
        if stock and session.scalar(select(ValuationSnapshot).where(ValuationSnapshot.stock_id == stock.id, ValuationSnapshot.metric_name == metric, ValuationSnapshot.as_of_date == as_of)) is None:
            instruments.add_valuation_snapshot(
                stock_id=stock.id,
                as_of_date=as_of,
                metric_name=metric,
                metric_value=value,
                source="historical_semiconductor_fixture",
                definition="项目冻结历史估值快照；需回核原始行情与财报口径",
            )
    role_rows = (("002371", "平台型设备龙头", "semi_capex_tracker", 2, 1), ("688012", "刻蚀设备龙头", "amec_annual_report", 11, 2), ("300604", "测试设备商", "changchuan_quarterly_report", 2, 1))
    for company_code, role, source_key, page, paragraph in role_rows:
        company = session.scalar(select(Company).where(Company.code == company_code))
        if company and session.scalar(select(ThemeRole).where(ThemeRole.research_case_id == case.id, ThemeRole.company_id == company.id)) is None:
            source_span, _available_at = spans[(source_key, page, paragraph)]
            source_statement = session.scalar(
                select(SourceStatement).where(
                    SourceStatement.source_span_id == source_span.id,
                    SourceStatement.normalized_text == source_span.verbatim_text,
                )
            )
            if source_statement is None:
                source_statement = research_repo.add_statement(
                    source_span_id=source_span.id,
                    kind="disclosed_fact",
                    normalized_text=source_span.verbatim_text,
                )
            instruments.add_theme_role(company_id=company.id, research_case_id=case.id, role=role, scope={"segment": "半导体设备", "evidence_status": "mapped_from_frozen_fixture"}, source_statement_id=source_statement.id)

    # Historical fund disclosures are explicit holding records, not inferred
    # from shareholder counts or research opinions. Keep one current and one
    # stale report-period row to demonstrate point-in-time replay.
    fund_companies = {
        "huaxia": ("FC103", "华夏基金管理有限公司"),
        "guolianan": ("FC104", "国联安基金管理有限公司"),
    }
    fund_company_ids = {}
    for key, (code, name) in fund_companies.items():
        fund_company = session.scalar(select(FundCompany).where(FundCompany.code == code))
        if fund_company is None:
            fund_company = instruments.add_fund_company(code=code, name=name)
        fund_company_ids[key] = fund_company.id
    funds = {
        "chip_etf": ("012854", "华夏国证半导体芯片ETF联接", "指数", "huaxia", "12500000000", date(2021, 8, 12)),
        "equipment_mix": ("018565", "国联安半导体设备混合", "混合", "guolianan", "1800000000", date(2023, 5, 18)),
    }
    fund_ids = {}
    for key, (code, name, fund_type, manager, scale, establish_date) in funds.items():
        fund = session.scalar(select(Fund).where(Fund.code == code))
        if fund is None:
            fund = instruments.add_fund(code=code, name=name, fund_type=fund_type, management_company_id=fund_company_ids[manager], scale=scale, establish_date=establish_date)
        fund_ids[key] = fund.id
    holdings = (
        ("chip_etf", "002371.SZ", "0.098", date(2026, 3, 31), date(2026, 4, 22), "fund-report-2026Q1"),
        ("chip_etf", "688012.SH", "0.084", date(2026, 3, 31), date(2026, 4, 22), "fund-report-2026Q1"),
        ("equipment_mix", "002371.SZ", "0.071", date(2026, 3, 31), date(2026, 4, 22), "fund-report-2026Q1"),
        ("equipment_mix", "300604.SZ", "0.046", date(2026, 3, 31), date(2026, 4, 22), "fund-report-2026Q1"),
        ("equipment_mix", "002371.SZ", "0.055", date(2025, 6, 30), date(2025, 7, 24), "fund-report-2025H1"),
    )
    for fund_key, stock_code, weight, report_period, published_at, source in holdings:
        stock = session.scalar(select(Stock).where(Stock.code == stock_code))
        if stock is None:
            continue
        existing = session.scalar(select(HoldingDisclosure).where(HoldingDisclosure.fund_id == fund_ids[fund_key], HoldingDisclosure.stock_id == stock.id, HoldingDisclosure.report_period == report_period, HoldingDisclosure.source == source))
        if existing is None:
            instruments.add_holding_disclosure(fund_id=fund_ids[fund_key], stock_id=stock.id, weight=weight, report_period=report_period, published_at=published_at, source=source)


def main() -> None:
    engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db"), future=True)
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(bind=engine, future=True)
    with LocalSession() as session:
        case_id = seed(session)
        session.commit()
    print({"case_id": case_id, "title": CASE_TITLE, "theme": THEME_TAG})


if __name__ == "__main__":
    main()
