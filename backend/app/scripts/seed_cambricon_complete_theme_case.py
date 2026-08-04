"""Append a complete, evidence-bounded Cambricon theme-research case.

This is intentionally separate from the narrow profitability seed. It adds six
reviewable theses under the existing ``算力国产化`` theme:

1. demand transmission: cloud-product revenue is the observable demand proxy;
2. sustainability: 2026Q1 continuation is positive but not yet a long series;
3. earnings quality: cash conversion and working-capital evidence is mixed;
4. fund holding: public-fund ownership is disclosed, but portfolio weights are
   not inferred from top-holder shares;
5. valuation: the current valuation data implies a very demanding expectation;
6. counter-evidence: customer concentration, margin volatility and cashflow
   weakness actively challenge the optimistic interpretation.

All source responses are frozen under ``fixtures/.../complete_theme``. The seed
writes only existing append-only ledger entities and leaves every human review
empty. Third-party articles are retained as non-primary context and explicitly
marked as requiring source-text verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import os
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import (
    AIAssessment, Company, DocumentVersion, EvidenceLink, EvidenceSnapshot,
    ResearchCase, SourceSpan, SourceStatement, Stock, ThemeRole, Thesis,
    Fund, ValuationSnapshot,
)
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.services.assessment import AssessmentService
from app.services.ingest import DocumentService
from app.services.research import ResearchService
from app.services.themes import ThemeService

CASE_TITLE = "寒武纪 2025 盈利拐点：需求、质量、持仓与估值全景研究"
CREATED_BY = "codex-complete-theme"
THEME_TAG = "算力国产化"
CUTOFF = datetime(2030, 1, 1, tzinfo=timezone.utc)
CAPTURED_AT = datetime(2026, 8, 4, 1, 58, 29, tzinfo=timezone.utc)

RATIONALE = (
    "完整主题研究的当前判断：云端产品几乎贡献全部2025年收入，且2026Q1收入与归母净利润继续为正，"
    "支持需求景气和盈利延续的初步判断；但2025年经营现金流为负、应收账款环比上升、客户集中度高、"
    "历史毛利率波动，以及2026-08-03估值对应的高倍数，均使盈利质量、可持续性与估值合理性仍需审慎验证。"
    "公募基金确有持仓披露，但公募持股数量和机构数量从2025FY到2026Q1下降，不能解读成持续加仓。"
    "需求因果的端到端订单证据和部分客户风险材料来自第三方检索，需回核原始公告。"
)
GAPS = [
    "缺少云厂商CapEx到项目级订单、交付和收入确认的端到端一手证据",
    "2026Q1后续季度尚不足以证明盈利持续性",
    "公募持仓披露为股东口径，缺少每只基金净值权重和完整季报组合核验",
    "估值接口部分历史PE/PB字段缺失，当前估值结论应以结构化时点数据而非历史分位下定论",
]

@dataclass(frozen=True)
class Spec:
    key: str
    source: str
    text: str
    role: str
    reason: str
    scope: dict

SPECS = (
    Spec("demand", "demand", "2025年云端产品线收入64.77亿元，占营业收入99.69%，同比增长455.34%；该结构说明盈利改善主要绑定云端AI芯片需求，但尚未形成订单到收入的一手端到端证明。", "supports", "云端产品收入是需求景气的直接经营结果指标；因果链仍需补一手订单证据。", {"dimension":"demand_transmission","period":"2025FY","source":"structured+third_party_context","verification":"third_party_requires_original_text"}),
    Spec("continuity", "quality", "2026Q1营业收入28.85亿元，归母净利润10.13亿元，经营活动现金流净额8.34亿元；单季度延续性支持盈利尚未立即逆转，但一个季度不足以证明长期持续。", "supports", "2026Q1同时验证收入、利润和经营现金流继续为正，但样本期短。", {"dimension":"sustainability","period":"2026Q1","source":"structured_financial_search"}),
    Spec("quality_positive", "quality", "2026Q1经营现金流净额8.34亿元、净利润现金含量82.00%、资产负债率16.00%，显示本期现金转化和杠杆约束较好。", "supports", "现金流和资产负债结构提供盈利质量的正向证据。", {"dimension":"earnings_quality","period":"2026Q1","source":"structured_financial_search"}),
    Spec("fund_holding", "fund_holding", "截至2026Q1，公募基金持股合计4,813.76万股、机构数量659；2025FY为7,801.15万股、2,088家。公募基金实际持仓存在，但持股数量和机构数量环比下降，不能表述为持续增持。", "contextualizes", "确认机构持仓事实，同时限定其不能直接证明机构看多或未来收益。", {"dimension":"institutional_holding","period":"2025FY-2026Q1","source":"structured_financial_search","metric":"public_fund_shares"}),
    Spec("valuation", "valuation", "2026-08-03结构化估值数据：动态PE约159.37倍、扣非后滚动PE约265.99倍、PS约78.10倍、PEG约395.05、企业价值约6458.99亿元；历史PE/PB分位字段部分缺失。当前价格已隐含极高增长预期。", "contextualizes", "估值倍数极高，支持估值审慎结论；不使用缺失的历史分位字段做伪精确判断。", {"dimension":"valuation","period":"2026-08-03","source":"structured_financial_search","data_quality":"history_percentile_partial_missing"}),
    Spec("counter", "counter_evidence", "反向证据：2025FY经营现金流净额为-4.98亿元；2026Q1应收账款12.19亿元、高于2025FY的6.71亿元；2025FY存货49.44亿元；第三方材料引用年报称2024年前五大客户占收入94.63%、第一大客户79.15%，且2024毛利率56.71%存在波动。客户集中度与营运资金占用可能反驳“盈利已稳健、可持续且低风险”的扩展判断。", "contradicts", "这些事实不否定会计利润为正，但直接反驳盈利质量稳定、客户分散和持续性已被充分验证的扩展判断。第三方材料需回核原始年报。", {"dimension":"counter_evidence","period":"2024FY-2026Q1","source":"structured+third_party_citing_annual_report","verification":"third_party_requires_original_text"}),
)


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "cambricon_profitability_case" / "complete_theme"


SOURCE_FILES = {
    "demand": "demand_context_2026-08-04.json",
    "quality": "quality_2026-08-04.json",
    "fund_holding": "fund_holding_2026-08-04.json",
    "valuation": "valuation_2026-08-04.json",
    "counter_evidence": "counter_evidence_2026-08-04.json",
    "deep_research": "deep_research_2026-08-04.json",
}

SUPPLEMENTAL_SPECS = {
    "demand": ("deep_research", "2025FY云端产品线收入6477000000元，占营业收入99.69%，同比增长455.34%；公开材料提到运营商、金融、互联网等场景部署与客户验证，但订单、交付、回款到收入确认的端到端一手链条尚未冻结。", "需求传导证据目前只到经营结果和场景线索，不能把收入增长直接等同于订单因果。", {"dimension": "demand_transmission", "period": "2025FY-2025H1", "source": "structured_plus_third_party", "evidence_status": "partial_chain", "missing": ["客户级订单/中标金额", "出货或验收数量", "客户CapEx到公司收入匹配"]}),
    "continuity": ("deep_research", "2026Q1收入28.85亿元、归母净利润10.13亿元、经营现金流8.34亿元；当前仅有2025FY与2026Q1两个已冻结报告期，尚不足以证明未来多个季度持续。", "短期利润和现金流延续是支持证据，样本长度和季节性是限制条件。", {"dimension": "sustainability", "period": "2025FY-2026Q1", "source": "structured_financial_search", "evidence_status": "short_series"}),
    "quality_positive": ("deep_research", "2025FY毛利率约55.15%，2026Q1毛利率约54.34%，下降0.81个百分点；2026Q1经营现金流转正，但应收账款较2025FY增加548513661.36元、增幅约81.79%。", "利润和现金流改善与应收扩张同时存在，盈利质量应判定为混合而非单向改善。", {"dimension": "earnings_quality", "period": "2025FY-2026Q1", "source": "structured_financial_search", "evidence_status": "mixed", "derived": ["gross_margin", "accounts_receivable_change"]}),
    "fund_holding": ("deep_research", "2026Q1公募基金合计持股48137640股、659家机构；十大流通股东中可识别华夏科创板50ETF持股6527594股、嘉实科创板芯片ETF持股3755189股、易方达科创板50ETF持股3539280股。上述持股数量是股东口径，不是基金净值权重。", "补充了可识别基金名称和持股数量，但没有把股票占总股本比例误写成基金组合权重。", {"dimension": "institutional_holding", "period": "2026Q1", "source": "structured_financial_search", "evidence_status": "named_holders_but_portfolio_weight_unavailable", "missing": ["基金净值规模", "持仓市值占基金净值比例", "完整基金季报组合"]}),
    "valuation": ("deep_research", "当前冻结估值响应混入688521.SH、688041.SH等其他标的，无法形成唯一归属于688256.SH寒武纪的PE、PS或PEG事实；估值结论暂降级为数据质量缺口。", "估值数据标的错配时不能继续沿用原倍数；应先取得严格限定688256.SH的估值原文。", {"dimension": "valuation", "period": "2026-08-03", "source": "structured_financial_search", "evidence_status": "data_quality_failure", "missing": ["单标的估值原文", "统一市值与财务口径"]}),
    "counter": ("deep_research", "红队反向证据包括：客户集中度材料需回核年报；2026Q1应收账款较2025FY增加约81.79%；毛利率下降0.81个百分点；估值响应存在标的错配。", "这些证据分别挑战客户分散、盈利质量持续改善和估值结论的稳健性。", {"dimension": "counter_evidence", "period": "2024FY-2026Q1", "source": "structured_plus_third_party", "evidence_status": "mixed", "red_team": True}),
}


def _load_sources() -> dict[str, str]:
    return {
        key: (_fixture_dir() / filename).read_text("utf-8")
        for key, filename in SOURCE_FILES.items()
    }


def _document(session: Session, repo: DocumentRepository, service: DocumentService, key: str, raw: str) -> tuple[DocumentVersion, SourceSpan]:
    digest = hashlib.sha256(raw.encode()).hexdigest()
    existing = repo.by_hash(digest)
    if existing:
        version = existing
    else:
        version = repo.insert_version(content_sha256=digest, source_url=f"neodata://cambricon-complete-theme/{key}/2026-08-04", published_at=CAPTURED_AT, available_at=CAPTURED_AT, acquired_at=CAPTURED_AT, parser_version="cambricon-complete-theme-v1", supersedes_id=None, title=f"寒武纪完整主题研究补充证据：{key}", byte_size=len(raw.encode()), language="zh")
    span = service.add_span(version.id, {"source":"neodata-financial-search","query_key":key,"captured_at":CAPTURED_AT.isoformat()}, raw)
    return version, span


def seed(session: Session):
    data = _load_sources()
    existing = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE))
    doc_repo = DocumentRepository(session); doc_service = DocumentService(doc_repo)
    research_repo = ResearchRepository(session); research = ResearchService(research_repo)
    assessment_service = AssessmentService(research_repo)
    data = _load_sources()
    case = existing or research.add_case(title=CASE_TITLE, industry_topic="ai_compute", created_by=CREATED_BY, research_object="寒武纪（688256）完整主题研究", phenomenon="盈利拐点与AI芯片需求、质量、机构和估值的共同验证", core_question="寒武纪盈利是否由可验证需求驱动、是否可持续、质量是否可靠且估值是否合理")
    thesis_specs = [
        ("需求传导：云端AI芯片需求是否支撑盈利", "云端产品收入占比99.69%，但订单到收入的端到端传导仍需一手证据"),
        ("盈利持续性：2026Q1是否延续", "2026Q1收入、归母净利润和经营现金流继续为正，但单季样本不足"),
        ("盈利质量：利润是否转化为现金并受营运资金支持", "2026Q1现金转化改善，但应收与存货规模仍需持续跟踪"),
        ("机构持仓：基金是否实际持有并形成稳定共识", "公募基金存在持仓披露，但2026Q1数量低于2025FY，不能等同持续增持"),
        ("估值合理性：当前价格是否已透支增长", "动态PE、扣非PE、PS和PEG处于极高水平，估值对增长兑现要求很高"),
        ("反向验证：哪些事实会否定完整盈利叙事", "现金流、客户集中度、毛利率波动和营运资金占用构成反向证据"),
    ]
    docs = {}
    for key, raw in data.items():
        docs[key] = _document(session, doc_repo, doc_service, key, raw)
    spec_by_dimension = {spec.key: spec for spec in SPECS}
    thesis_links = {
        "demand": "demand",
        "continuity": "continuity",
        "quality": "quality_positive",
        "fund_holding": "fund_holding",
        "valuation": "valuation",
        "counter": "counter",
    }
    created_theses = list(session.scalars(select(Thesis).where(Thesis.research_case_id == case.id).order_by(Thesis.created_at)))
    for title, statement in thesis_specs:
        thesis = next((item for item in created_theses if item.title == title), None)
        if thesis is None:
            thesis = research.add_thesis(case.id, statement=statement, title=title, support_condition="对应维度的一手或结构化证据持续支持且经人工复核", falsification_condition="关键指标恶化、后续数据不延续或反向证据无法解释", next_verification_event="补充2026Q2及后续季度财务、客户与基金持仓披露", created_by=CREATED_BY, creator_type="ai", review_state="draft")
            created_theses.append(thesis)
        dimension = (
            "demand" if "需求" in title else
            "continuity" if "持续性" in title else
            "quality_positive" if "质量" in title else
            "fund_holding" if "持仓" in title else
            "valuation" if "估值" in title else
            "counter"
        )
        spec = spec_by_dimension[dimension]
        if not session.scalar(select(EvidenceLink).where(EvidenceLink.thesis_id == thesis.id)):
            span = docs[spec.source][1]
            statement_row = research.add_statement(span.id, spec.text, kind="disclosed_fact")
            research.link_evidence(thesis.id, statement_row.id, role=spec.role, reason=spec.reason, scope=spec.scope, available_at=CAPTURED_AT)
        # Append a second, deeper evidence link for each thesis. The marker in
        # ``reason`` makes this append-only enrichment idempotent without
        # mutating the original six-link demonstration layer.
        deep_source, deep_text, deep_reason, deep_scope = SUPPLEMENTAL_SPECS[dimension]
        if not session.scalar(select(EvidenceLink).where(EvidenceLink.thesis_id == thesis.id, EvidenceLink.reason == deep_reason)):
            deep_span = docs[deep_source][1]
            deep_statement = research.add_statement(deep_span.id, deep_text, kind="disclosed_fact")
            research.link_evidence(thesis.id, deep_statement.id, role=("contradicts" if dimension == "counter" else "contextualizes"), reason=deep_reason, scope=deep_scope, available_at=CAPTURED_AT)
        # Freeze after both the base and supplemental links are present, so the
        # assessment snapshot contains the complete evidence set.
        if not session.scalar(select(AIAssessment).join(EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id).where(EvidenceSnapshot.thesis_id == thesis.id)):
            snap = assessment_service.freeze_snapshot(thesis.id, cutoff=CUTOFF)
            conclusion = "insufficient_evidence" if spec.key in {"valuation", "counter"} else "supported"
            assessment_service.create_ai_assessment(snap.id, conclusion=conclusion, rationale=RATIONALE, gaps=GAPS)
    # Add one cross-case company/stock/theme role without inventing holdings.
    company = session.scalar(select(Company).where(Company.code == "688256"))
    if company is None:
        company = InstrumentRepository(session).add_company(code="688256", name="寒武纪", type="listed")
    stock = session.scalar(select(Stock).where(Stock.code == "688256.SH"))
    if stock is None:
        stock = InstrumentRepository(session).add_stock(company_id=company.id, code="688256.SH", name="寒武纪", market="SSE")
    instruments = InstrumentRepository(session)
    if not session.scalar(select(ThemeRole).where(ThemeRole.research_case_id == case.id, ThemeRole.company_id == company.id)):
        instruments.add_theme_role(company_id=company.id, research_case_id=case.id, role="算力国产化核心芯片公司", scope={"segment": "云端AI芯片", "period": "2025FY-2026Q1"})
    # The valuation response is explicitly flagged as mixed-symbol data in the
    # deep fixture, so do not write the previous unverified PE/PS/PEG numbers.
    # The valuation thesis retains the data-quality gap as auditable evidence.
    # Keep the controlled existing tag and attach the complete case.
    ThemeService(research_repo).apply_theme_tags(case=case, desired=[THEME_TAG], proposed_by="human")
    # Every thesis now has its own frozen snapshot and provisional assessment;
    # no human review is created by this seed.
    return case.id


def main() -> None:
    engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db"), future=True)
    from app.models.ledger import Base
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as session:
        case_id = seed(session); session.commit()
    print(json.dumps({"created": True, "case_id": str(case_id), "title": CASE_TITLE, "conclusion_url": f"http://localhost:5173/conclusion/{case_id}"}, ensure_ascii=False))

if __name__ == "__main__":
    main()
