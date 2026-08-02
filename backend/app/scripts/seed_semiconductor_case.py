"""Seed the frozen semiconductor-equipment （半导体设备国产化） slice (offline).

Third industry gold case, complementing ``seed_ai_compute_case`` and
``seed_storage_chain_case``: a third industry with its own document texture —
order announcements, annual/quarterly report excerpts, broker research, and an
industry data tracker.  All fixtures are marker-convention ``.txt`` files;
this case deliberately exercises the *contradicted-by-policy-constraint*
assessment shape (T3), which the first two cases do not cover.

Layout mirrors the storage-chain seed: fixture files under
``tests/fixtures/semiconductor/``, statements and evidence links are declared
in the manifests below, and every AIAssessment carries a human ReviewDecision.

Replaying is deterministic: re-freezing identical bytes returns the same
DocumentVersion (content-addressed), so the seed is idempotent at the
document layer.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import Base
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.services.assessment import AssessmentService
from app.services.ingest import DocumentService
from app.services.research import ResearchService

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "semiconductor"

CUTOFF = datetime(2026, 12, 31, tzinfo=timezone.utc)
CREATED_BY = "seed-semiconductor"

_META_RE = re.compile(r"^#\s*([A-Z_]+)\s*:\s*(.*)$", re.MULTILINE)
_SPAN_RE = re.compile(r"\[PAGE\s+(\d+)\]\s*\[PARA\s+(\d+)\]")

THESIS_STATEMENTS = {
    "T1": "2026年国内晶圆厂扩产将驱动国产半导体设备订单持续高增长",
    "T2": "先进封装扩产将显著修复国产测试设备环节盈利",
    "T3": "光刻环节受限不改国产半导体设备板块整体估值支撑",
}

STATEMENTS: list[dict] = [
    # ---- Thesis 1: fab expansion drives domestic equipment orders ----
    {
        "sid": "s_semi_capex",
        "file": "semi_capex_tracker",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "SEMI预测2026年中国大陆晶圆厂设备支出同比增长约18%，保持全球最大设备市场",
        "period": date(2026, 5, 20),
    },
    {
        "sid": "s_domestic_bid",
        "file": "semi_capex_tracker",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "2026年1-4月国产设备中标金额同比增长约62%，刻蚀、清洗等环节国产化率超过50%",
        "period": date(2026, 5, 20),
    },
    {
        "sid": "s_naura_orders",
        "file": "naura_order_announcement",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "北方华创新签刻蚀、薄膜沉积等设备采购合同约85亿元，同比增长约65%",
        "period": date(2026, 3, 12),
    },
    {
        "sid": "s_amec_rev",
        "file": "amec_annual_report",
        "page": 8, "para": 1,
        "kind": "disclosed_fact",
        "text": "中微公司2025年营业收入98.6亿元，同比增长约44%，刻蚀设备市占率进一步提升",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_amec_orders",
        "file": "amec_annual_report",
        "page": 11, "para": 2,
        "kind": "disclosed_fact",
        "text": "中微公司2025年新增订单同比增长约52%，先进封装用深硅刻蚀设备订单翻倍",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_htsc_demand",
        "file": "htsc_equipment_research",
        "page": 3, "para": 1,
        "kind": "forecast",
        "text": "华泰预测2026年中国大陆半导体设备市场规模突破1800亿元，国产厂商收入增速保持35%以上",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_htsc_domestic_share",
        "file": "htsc_equipment_research",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "2026年1-4月国产设备中标金额占比升至约41%，同比提升约9个百分点",
        "period": date(2026, 6, 10),
    },
    # ---- Thesis 2: advanced packaging repairs test-equipment margins ----
    {
        "sid": "s_htsc_packaging",
        "file": "htsc_equipment_research",
        "page": 6, "para": 1,
        "kind": "disclosed_fact",
        "text": "先进封装资本开支占晶圆制造总投资比重持续提升，2026年国内先进封装产线扩建项目明显增多",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_changchuan_rev",
        "file": "changchuan_quarterly_report",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "长川科技2026年第一季度营业收入同比增长约38%，受益于封测厂扩产带来的测试机需求",
        "period": date(2026, 3, 31),
    },
    {
        "sid": "s_changchuan_margin",
        "file": "changchuan_quarterly_report",
        "page": 2, "para": 2,
        "kind": "disclosed_fact",
        "text": "长川科技2026年第一季度综合毛利率48.3%，同比下降2.1个百分点，系测试机价格承压所致",
        "period": date(2026, 3, 31),
    },
    {
        "sid": "s_changchuan_mgmt_price",
        "file": "changchuan_quarterly_report",
        "page": 4, "para": 1,
        "kind": "management_attribution",
        "text": "长川科技管理层表示模拟测试机等成熟品类价格竞争明显加剧，短期毛利率仍存在压力",
        "period": date(2026, 4, 25),
    },
    {
        "sid": "s_htsc_test_caution",
        "file": "htsc_equipment_research",
        "page": 7, "para": 2,
        "kind": "research_opinion",
        "text": "华泰认为测试设备环节收入弹性初步显现，但盈利修复的斜率与持续性仍有待季度数据验证",
        "period": date(2026, 6, 10),
    },
    # ---- Thesis 3: litho constraint does not undermine sector valuation ----
    {
        "sid": "s_htsc_growth_logic",
        "file": "htsc_equipment_research",
        "page": 10, "para": 1,
        "kind": "research_opinion",
        "text": "华泰认为国产刻蚀、薄膜沉积等环节持续突破构成设备板块核心成长逻辑，平台型龙头受益确定",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_naura_rd",
        "file": "naura_order_announcement",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "北方华创2025年研发费用同比增长约40%，投向先进制程刻蚀与零部件自主化平台化布局",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_litho_block",
        "file": "semi_capex_tracker",
        "page": 3, "para": 1,
        "kind": "disclosed_fact",
        "text": "国产光刻设备在产线中的实际装机占比估计低于5%，高端光刻受出口管制约束，短期难以实质性突破",
        "period": date(2026, 5, 20),
    },
    {
        "sid": "s_amec_mgmt_control",
        "file": "amec_annual_report",
        "page": 19, "para": 1,
        "kind": "management_attribution",
        "text": "中微公司管理层表示出口管制对部分关键零部件采购与交付周期造成影响，极端情形下交付扰动不能完全排除",
        "period": date(2026, 3, 28),
    },
    {
        "sid": "s_htsc_valuation_warn",
        "file": "htsc_equipment_research",
        "page": 10, "para": 2,
        "kind": "research_opinion",
        "text": "华泰提示当前板块估值已隐含较高国产化率提升预期，光刻等关键环节若长期不能突破，估值中枢存在下修风险",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_tracker_delivery_lag",
        "file": "semi_capex_tracker",
        "page": 3, "para": 2,
        "kind": "disclosed_fact",
        "text": "部分设备商关键零部件交期受管制政策影响拉长，二季度个别订单确认收入节奏延后",
        "period": date(2026, 5, 20),
    },
]

LINKS: list[dict] = [
    # Thesis 1 -> supported
    {"thesis": "T1", "statement": "s_semi_capex", "role": "supports",
     "reason": "大陆晶圆厂设备支出同比+18%，下游扩产为设备订单提供总量基础",
     "scope": {"segment": "晶圆厂资本开支"}},
    {"thesis": "T1", "statement": "s_domestic_bid", "role": "supports",
     "reason": "国产设备中标金额同比+62%，国产化率超50%，订单向国产厂商集中",
     "scope": {"segment": "设备招标"}},
    {"thesis": "T1", "statement": "s_naura_orders", "role": "supports",
     "reason": "平台型龙头新签合同同比+65%，订单能见度延伸至2027年下半年",
     "scope": {"segment": "设备订单"}},
    {"thesis": "T1", "statement": "s_amec_rev", "role": "supports",
     "reason": "刻蚀龙头收入同比+44%，订单已兑现至收入端",
     "scope": {"segment": "刻蚀设备"}},
    {"thesis": "T1", "statement": "s_amec_orders", "role": "supports",
     "reason": "中微新增订单同比+52%，量的扩张直接印证需求",
     "scope": {"segment": "刻蚀设备"}},
    {"thesis": "T1", "statement": "s_htsc_demand", "role": "supports",
     "reason": "研报预测国产设备市场规模突破1800亿元、收入增速35%以上",
     "scope": {"segment": "设备市场规模"}},
    {"thesis": "T1", "statement": "s_htsc_domestic_share", "role": "supports",
     "reason": "国产中标金额占比提升至41%，替代加速为订单增长提供结构性支撑",
     "scope": {"segment": "设备招标"}},
    # Thesis 2 -> insufficient_evidence (margin repair unproven)
    {"thesis": "T2", "statement": "s_htsc_packaging", "role": "supports",
     "reason": "先进封装产线扩建增多，测试设备需求侧出现结构性拉动",
     "scope": {"segment": "先进封装"}},
    {"thesis": "T2", "statement": "s_changchuan_rev", "role": "supports",
     "reason": "测试设备商收入同比+38%，需求传导已体现在收入端",
     "scope": {"segment": "测试设备"}},
    {"thesis": "T2", "statement": "s_changchuan_margin", "role": "contradicts",
     "reason": "毛利率同比下降2.1个百分点，收入增长未转化为盈利修复",
     "scope": {"segment": "测试设备盈利"}},
    {"thesis": "T2", "statement": "s_changchuan_mgmt_price", "role": "contradicts",
     "reason": "管理层确认成熟品类价格竞争加剧、短期毛利率仍有压力",
     "scope": {"segment": "测试设备盈利"}},
    {"thesis": "T2", "statement": "s_htsc_test_caution", "role": "contradicts",
     "reason": "研报认为盈利修复斜率与持续性待验证，弹性判断证据不足",
     "scope": {"segment": "测试设备盈利"}},
    # Thesis 3 -> contradicted
    {"thesis": "T3", "statement": "s_htsc_growth_logic", "role": "supports",
     "reason": "研报认为刻蚀/薄膜突破构成板块核心成长逻辑，支撑估值叙事",
     "scope": {"segment": "设备板块", "valuation": "成长逻辑"}},
    {"thesis": "T3", "statement": "s_naura_rd", "role": "supports",
     "reason": "龙头研发费用同比+40%投向平台化自主化，支撑长期竞争力假设",
     "scope": {"segment": "设备板块", "valuation": "成长逻辑"}},
    {"thesis": "T3", "statement": "s_litho_block", "role": "contradicts",
     "reason": "光刻装机占比低于5%且受管制约束，关键环节长期缺失削弱整体估值基础",
     "scope": {"segment": "光刻设备", "valuation": "估值中枢"}},
    {"thesis": "T3", "statement": "s_amec_mgmt_control", "role": "contradicts",
     "reason": "出口管制影响零部件交付，龙头亦承认极端情形交付扰动不可排除",
     "scope": {"segment": "供应链", "valuation": "估值中枢"}},
    {"thesis": "T3", "statement": "s_htsc_valuation_warn", "role": "contradicts",
     "reason": "研报明确提示估值已隐含较高国产化预期、存在中枢下修风险，与估值支撑假设直接冲突",
     "scope": {"segment": "设备板块", "valuation": "估值中枢"}},
    {"thesis": "T3", "statement": "s_tracker_delivery_lag", "role": "contradicts",
     "reason": "零部件交期拉长已造成订单确认节奏延后，管制影响从风险变为现实扰动",
     "scope": {"segment": "供应链", "valuation": "估值中枢"}},
]

ASSESSMENTS: list[dict] = [
    {
        "thesis": "T1", "conclusion": "supported",
        "rationale": "下游资本开支、招标份额、龙头订单与收入多源交叉验证，证据一致支持国产设备订单高增长",
        "gaps": [],
    },
    {
        "thesis": "T2", "conclusion": "insufficient_evidence",
        "rationale": "先进封装扩产与测试设备收入增长属实，但毛利率下滑与价格竞争使盈利修复缺乏定量证据",
        "gaps": [
            "缺少测试机分品类价格与毛利率的连续季度数据",
            "先进封装测试平台放量对毛利的贡献缺乏定量拆分",
        ],
    },
    {
        "thesis": "T3", "conclusion": "contradicted",
        "rationale": "光刻环节受管制长期缺失、零部件交付扰动已成现实，且研报明确提示估值隐含预期过高，与板块整体估值支撑假设矛盾",
        "gaps": ["光刻环节国产化进展缺乏季度级定量跟踪"],
    },
]

REVIEWER = "seed-human-reviewer"
REVIEWS: list[dict] = [
    {
        "thesis": "T1", "outcome": "confirmed", "conclusion": "supported",
        "reason": "人工确认：资本开支、招标、订单、收入四类证据一致，维持 supported",
    },
    {
        "thesis": "T2", "outcome": "confirmed", "conclusion": "insufficient_evidence",
        "reason": "人工维持：需求侧证据成立但盈利侧证据不足，维持 insufficient_evidence",
    },
    {
        "thesis": "T3", "outcome": "confirmed", "conclusion": "contradicted",
        "reason": "人工维持：光刻受限与估值预期过高的证据与假设直接冲突，维持 contradicted",
    },
]

COMPANIES: list[dict] = [
    {"key": "naura", "code": "002371", "name": "北方华创", "type": "listed"},
    {"key": "amec", "code": "688012", "name": "中微公司", "type": "listed"},
    {"key": "changchuan", "code": "300604", "name": "长川科技", "type": "listed"},
]

STOCKS: list[dict] = [
    {"company": "naura", "code": "002371.SZ", "name": "北方华创", "market": "SZSE"},
    {"company": "amec", "code": "688012.SH", "name": "中微公司", "market": "SSE"},
    {"company": "changchuan", "code": "300604.SZ", "name": "长川科技", "market": "SZSE"},
]

VALUATIONS: list[dict] = [
    {"stock": "naura", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("42.6"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
    {"stock": "naura", "as_of": date(2026, 6, 30), "metric": "PB",
     "value": Decimal("8.1"), "source": "wind", "definition": "总市值/归属股东权益"},
    {"stock": "amec", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("55.3"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
]

THEME_ROLES: list[dict] = [
    {"company": "naura", "role": "平台型设备龙头",
     "scope": {"segment": "半导体设备"}, "from": date(2026, 1, 1),
     "statement": "s_naura_orders"},
    {"company": "amec", "role": "刻蚀设备龙头",
     "scope": {"segment": "刻蚀设备"}, "from": date(2026, 1, 1),
     "statement": "s_amec_orders"},
    {"company": "changchuan", "role": "测试设备商",
     "scope": {"segment": "测试设备"}, "from": date(2026, 1, 1),
     "statement": "s_changchuan_rev"},
]

FUND_COMPANIES: list[dict] = [
    {"key": "fc_huaxia", "code": "FC103", "name": "华夏基金管理有限公司"},
    {"key": "fc_guolianan", "code": "FC104", "name": "国联安基金管理有限公司"},
]

FUNDS: list[dict] = [
    {"key": "fund_e", "code": "012854", "name": "华夏国证半导体芯片ETF联接",
     "type": "指数", "scale": Decimal("12500000000"),
     "establish": date(2021, 8, 12), "mgmt": "fc_huaxia"},
    {"key": "fund_f", "code": "018565", "name": "国联安半导体设备混合",
     "type": "混合", "scale": Decimal("1800000000"),
     "establish": date(2023, 5, 18), "mgmt": "fc_guolianan"},
]

HOLDINGS: list[dict] = [
    {"fund": "fund_e", "stock": "naura", "weight": Decimal("0.098"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_e", "stock": "amec", "weight": Decimal("0.084"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_f", "stock": "naura", "weight": Decimal("0.071"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_f", "stock": "changchuan", "weight": Decimal("0.046"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    # Retained stale disclosure: superseded by the 2026Q1 row above.
    {"fund": "fund_f", "stock": "naura", "weight": Decimal("0.055"),
     "report_period": date(2025, 6, 30), "published_at": date(2025, 7, 24),
     "source": "fund-report-2025H1"},
]

# Causal chain for the focus thesis (T3: 板块估值支撑), human-reviewed.
CAUSAL_THESIS = "T3"
CAUSAL_CREATOR_TYPE = "human"
CAUSAL_REVIEW_STATE = "reviewed"

CAUSAL_STEPS: list[dict] = [
    {"seq": 1, "description": "国内晶圆厂持续扩产"},
    {"seq": 2, "description": "设备国产化替代加速（刻蚀/薄膜等环节）"},
    {"seq": 3, "description": "设备商订单与收入兑现"},
    {"seq": 4, "description": "光刻环节受管制长期缺失、零部件交付扰动"},
    {"seq": 5, "description": "板块估值隐含预期过高，整体支撑不足"},
]

CAUSAL_EDGES: list[dict] = [
    {"from": 1, "to": 2, "rationale": "扩产为国产设备提供验证与放量场景"},
    {"from": 2, "to": 3, "rationale": "替代加速带动设备商订单与收入兑现"},
    {"from": 3, "to": 4, "rationale": "订单放量同时关键环节受管制约束加剧"},
    {"from": 4, "to": 5, "rationale": "关键环节缺失与交付扰动削弱整体估值支撑"},
]


# ---------------------------------------------------------------------------
# Fixture parsing
# ---------------------------------------------------------------------------


def _parse_txt_fixture(path: Path) -> tuple[dict, bytes, list[tuple[dict, str]]]:
    """Return (metadata, raw_bytes, spans) for a marker-convention .txt fixture."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    meta: dict[str, str] = {}
    for m in _META_RE.finditer(text):
        meta[m.group(1)] = m.group(2).strip()

    markers = list(_SPAN_RE.finditer(text))
    spans: list[tuple[dict, str]] = []
    for idx, m in enumerate(markers):
        start = m.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        chunk = text[start:end]
        lines = []
        for line in chunk.splitlines():
            if not line.strip():
                continue
            if _META_RE.match(line):
                continue
            if _SPAN_RE.match(line):
                continue
            lines.append(line.strip())
        verbatim = " ".join(lines)
        if not verbatim:
            continue
        locator = {"page": int(m.group(1)), "paragraph": int(m.group(2))}
        spans.append((locator, verbatim))
    return meta, raw, spans


def _published_at(meta: dict[str, str]) -> datetime | None:
    value = meta.get("PUBLISHED_AT")
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def seed(session: Session) -> None:
    """Seed the frozen semiconductor-equipment slice into ``session`` (offline).

    Does not commit; callers control the transaction.
    """
    document_service = DocumentService(DocumentRepository(session))
    research_service = ResearchService(ResearchRepository(session))
    assessment_service = AssessmentService(ResearchRepository(session))
    instruments = InstrumentRepository(session)
    research_repo = ResearchRepository(session)

    # 1. Freeze every fixture into a DocumentVersion + SourceSpans.
    span_index: dict[tuple[str, int, int], object] = {}
    for path in sorted(FIXTURES_DIR.glob("*.txt")):
        meta, raw, spans = _parse_txt_fixture(path)
        file_key = meta.get("FILE", path.stem)
        source_url = meta.get(
            "SOURCE_URL", f"https://example.test/semiconductor/{file_key}"
        )
        version = document_service.freeze(
            raw=raw, source_url=source_url, published_at=_published_at(meta)
        )
        for locator, verbatim in spans:
            span = document_service.add_span(
                document_version_id=version.id,
                locator=locator,
                verbatim_text=verbatim,
            )
            span_index[(file_key, locator["page"], locator["paragraph"])] = span

    # 2. One ResearchCase + three Theses.
    case = research_service.add_case(
        title="半导体设备国产化",
        industry_topic="semiconductor_equipment",
        created_by=CREATED_BY,
    )
    theses: dict[str, object] = {}
    for key, statement in THESIS_STATEMENTS.items():
        theses[key] = research_service.add_thesis(
            case.id, statement=statement, created_by=CREATED_BY
        )

    # 3. SourceStatements from spans (looked up by file/page/para).
    statements: dict[str, object] = {}
    for spec in STATEMENTS:
        span = span_index.get((spec["file"], spec["page"], spec["para"]))
        if span is None:
            raise RuntimeError(
                f"required span not found: {spec['file']} "
                f"page={spec['page']} para={spec['para']}"
            )
        statements[spec["sid"]] = research_service.add_statement(
            span.id,
            spec["text"],
            kind=spec["kind"],
            observed_period=spec["period"],
        )

    # 4. EvidenceLinks.
    for spec in LINKS:
        research_service.link_evidence(
            theses[spec["thesis"]].id,
            statements[spec["statement"]].id,
            role=spec["role"],
            reason=spec["reason"],
            scope=spec["scope"],
        )

    # 5. EvidenceSnapshot + AIAssessment per thesis, then human review.
    assessments: dict[str, object] = {}
    for spec in ASSESSMENTS:
        snapshot = assessment_service.freeze_snapshot(
            theses[spec["thesis"]].id, cutoff=CUTOFF
        )
        if not snapshot.evidence_link_ids:
            raise RuntimeError(
                f"snapshot for thesis {spec['thesis']} has no visible links"
            )
        assessments[spec["thesis"]] = assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion=spec["conclusion"],
            rationale=spec["rationale"],
            gaps=spec["gaps"],
        )
    for spec in REVIEWS:
        assessment_service.review(
            assessments[spec["thesis"]].id,
            outcome=spec["outcome"],
            conclusion=spec["conclusion"],
            reason=spec["reason"],
            reviewer=REVIEWER,
        )

    # 6. Companies + Stocks + ValuationSnapshots.
    company_ids: dict[str, object] = {}
    stock_ids: dict[str, object] = {}
    for spec in COMPANIES:
        company_ids[spec["key"]] = instruments.add_company(
            code=spec["code"], name=spec["name"], type=spec["type"]
        )
    for spec in STOCKS:
        stock_ids[spec["company"]] = instruments.add_stock(
            company_id=company_ids[spec["company"]].id,
            code=spec["code"], name=spec["name"], market=spec["market"],
        )
    for spec in VALUATIONS:
        instruments.add_valuation_snapshot(
            stock_id=stock_ids[spec["stock"]].id,
            as_of_date=spec["as_of"],
            metric_name=spec["metric"],
            metric_value=spec["value"],
            source=spec["source"],
            definition=spec["definition"],
        )

    # 7. ThemeRoles linking companies to the case (with source statement).
    for spec in THEME_ROLES:
        instruments.add_theme_role(
            company_id=company_ids[spec["company"]].id,
            role=spec["role"],
            scope=spec["scope"],
            research_case_id=case.id,
            applicable_from=spec["from"],
            source_statement_id=statements[spec["statement"]].id,
        )

    # 8. Fund companies + Funds + HoldingDisclosures.
    fund_company_ids: dict[str, object] = {}
    for spec in FUND_COMPANIES:
        fund_company_ids[spec["key"]] = instruments.add_fund_company(
            code=spec["code"], name=spec["name"]
        )
    fund_ids: dict[str, object] = {}
    for spec in FUNDS:
        fund_ids[spec["key"]] = instruments.add_fund(
            code=spec["code"], name=spec["name"], fund_type=spec["type"],
            management_company_id=fund_company_ids[spec["mgmt"]].id,
            scale=spec["scale"], establish_date=spec["establish"],
        )
    for spec in HOLDINGS:
        instruments.add_holding_disclosure(
            fund_id=fund_ids[spec["fund"]].id,
            stock_id=stock_ids[spec["stock"]].id,
            weight=spec["weight"],
            report_period=spec["report_period"],
            published_at=spec["published_at"],
            source=spec["source"],
        )

    # 9. Causal chain for the focus thesis (human-authored, reviewed).
    causal_thesis = theses[CAUSAL_THESIS]
    if not research_repo.causal_steps_for_thesis(causal_thesis.id):
        step_by_seq: dict[int, object] = {}
        for spec in CAUSAL_STEPS:
            step_by_seq[spec["seq"]] = research_repo.add_causal_step(
                thesis_id=causal_thesis.id,
                description=spec["description"],
                sequence=spec["seq"],
            )
        for spec in CAUSAL_EDGES:
            research_repo.add_causal_edge(
                source_step_id=step_by_seq[spec["from"]].id,
                target_step_id=step_by_seq[spec["to"]].id,
                rationale=spec["rationale"],
                creator_type=CAUSAL_CREATOR_TYPE,
                review_state=CAUSAL_REVIEW_STATE,
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the frozen semiconductor-equipment evidence slice (offline)."
    )
    parser.add_argument(
        "--reset-test-db",
        action="store_true",
        help="drop and recreate all ledger tables before seeding",
    )
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db")
    engine = create_engine(url, future=True)
    if args.reset_test_db:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as session:
        seed(session)
        session.commit()

    print("seeded semiconductor equipment case into", url)


if __name__ == "__main__":
    main()
