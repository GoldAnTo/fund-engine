"""Seed the frozen storage-chain （锂电储能链） evidence slice (offline).

Second industry gold case, complementing ``seed_ai_compute_case``: different
industry, different document texture, and — critically — the first case whose
source material includes a *real binary PDF* parsed through
``app.services.pdf_text`` (pypdf), not just hand-authored marker text.

Layout mirrors the AI-compute seed: fixture files under
``tests/fixtures/storage_chain/`` (``.txt`` files use the ``[PAGE n][PARA m]``
marker convention; ``.pdf`` files are parsed with pypdf), statements and
evidence links are declared in the manifests below, and every AIAssessment
carries a human ReviewDecision.

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
from app.services.pdf_text import PARSER_VERSION as PDF_PARSER_VERSION
from app.services.pdf_text import extract_spans as pdf_extract_spans
from app.services.research import ResearchService

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "storage_chain"

CUTOFF = datetime(2026, 12, 31, tzinfo=timezone.utc)
CREATED_BY = "seed-storage-chain"

_META_RE = re.compile(r"^#\s*([A-Z_]+)\s*:\s*(.*)$", re.MULTILINE)
_SPAN_RE = re.compile(r"\[PAGE\s+(\d+)\]\s*\[PARA\s+(\d+)\]")

THESIS_STATEMENTS = {
    "T1": "2026年全球储能装机高增长将驱动锂电池需求持续扩张",
    "T2": "碳酸锂价格回升将在2026年修复锂电中游材料环节盈利",
    "T3": "宁德时代储能业务高增长将支撑其当前估值溢价持续",
}

STATEMENTS: list[dict] = [
    # ---- Thesis 1: storage install growth drives battery demand ----
    {
        "sid": "s_cnesa_install",
        "file": "citic_storage_research",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "据CNESA统计，2026年1-5月全球新型储能新增装机同比增长约58%，海外市场增速超过70%",
        "period": date(2026, 6, 5),
    },
    {
        "sid": "s_citic_demand_forecast",
        "file": "citic_storage_research",
        "page": 3, "para": 1,
        "kind": "forecast",
        "text": "中信预测2026年全球储能电池需求将超过400GWh，同比增长约55%",
        "period": date(2026, 6, 5),
    },
    {
        "sid": "s_catl_storage_rev",
        "file": "catl_annual_report",
        "page": 12, "para": 1,
        "kind": "disclosed_fact",
        "text": "宁德时代2025年储能电池系统收入同比增长62%，占营业收入比重提升至19%",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_catl_shipment",
        "file": "catl_annual_report",
        "page": 15, "para": 2,
        "kind": "disclosed_fact",
        "text": "宁德时代2025年储能电池出货量同比增长58%，海外市场储能订单占比持续提升",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_eve_capacity",
        "file": "eve_capacity_announcement",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "亿纬锂能拟在湖北荆门新增储能电池产能60GWh，项目总投资约120亿元，一期预计2027年投产",
        "period": date(2026, 2, 10),
    },
    {
        "sid": "s_sungrow_q1",
        "file": "sungrow_quarterly_report",
        "page": 3, "para": 1,
        "kind": "disclosed_fact",
        "text": "阳光电源2026年第一季度储能系统发货量同比增长75%，海外收入占比超过60%",
        "period": date(2026, 3, 31),
    },
    {
        "sid": "s_sungrow_annual_pdf",
        "file": "sungrow_annual_summary_pdf",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "阳光电源2025年储能系统收入298.5亿元，同比增长67.5%，海外收入占比达58%",
        "period": date(2025, 12, 31),
    },
    # ---- Thesis 2: lithium price rebound repairs mid-stream margins ----
    {
        "sid": "s_lithium_rebound",
        "file": "lithium_price_tracker",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "截至2026年5月中旬电池级碳酸锂均价报9.8万元/吨，较2026年1月低点回升约30%",
        "period": date(2026, 5, 15),
    },
    {
        "sid": "s_lithium_glut",
        "file": "lithium_price_tracker",
        "page": 1, "para": 2,
        "kind": "disclosed_fact",
        "text": "2026年全球锂资源有效产能仍处过剩区间，行业库存维持高位，供需平衡表难以实质性收紧",
        "period": date(2026, 5, 15),
    },
    {
        "sid": "s_eve_mgmt_lag",
        "file": "eve_capacity_announcement",
        "page": 2, "para": 2,
        "kind": "management_attribution",
        "text": "亿纬锂能管理层表示，碳酸锂价格向电芯成本的传导通常存在2至3个季度的滞后",
        "period": date(2026, 2, 10),
    },
    {
        "sid": "s_citic_margin_caution",
        "file": "citic_storage_research",
        "page": 6, "para": 2,
        "kind": "research_opinion",
        "text": "中信认为碳酸锂价格回升对中游材料环节盈利修复的弹性有限，正极材料加工费仍处下行通道",
        "period": date(2026, 6, 5),
    },
    # ---- Thesis 3: CATL storage business supports valuation premium ----
    {
        "sid": "s_citic_catl_premium",
        "file": "citic_storage_research",
        "page": 9, "para": 1,
        "kind": "research_opinion",
        "text": "中信给予宁德时代储能分部估值溢价，认为市场尚未充分定价其海外储能长单的盈利韧性",
        "period": date(2026, 6, 5),
    },
    {
        "sid": "s_citic_price_war",
        "file": "citic_storage_research",
        "page": 9, "para": 2,
        "kind": "research_opinion",
        "text": "国内储能电芯价格战加剧，2026年以来中标均价同比下降约15%，龙头毛利率与份额面临稀释风险",
        "period": date(2026, 6, 5),
    },
    {
        "sid": "s_catl_mgmt_guidance",
        "file": "catl_annual_report",
        "page": 18, "para": 1,
        "kind": "management_attribution",
        "text": "宁德时代管理层指引2026年储能电池出货目标为同比增长50%以上，优先保障海外长单交付",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_catl_margin_down",
        "file": "catl_annual_report",
        "page": 21, "para": 3,
        "kind": "disclosed_fact",
        "text": "宁德时代2025年储能电池系统毛利率为24.1%，同比下降3.2个百分点，受价格竞争加剧影响",
        "period": date(2025, 12, 31),
    },
]

LINKS: list[dict] = [
    # Thesis 1 -> supported
    {"thesis": "T1", "statement": "s_cnesa_install", "role": "supports",
     "reason": "全球新型储能装机同比高增，海外市场增速更高，直接驱动锂电池需求",
     "scope": {"segment": "储能装机"}},
    {"thesis": "T1", "statement": "s_citic_demand_forecast", "role": "supports",
     "reason": "研报预测2026年全球储能电池需求超400GWh，同比高增",
     "scope": {"segment": "储能电池需求"}},
    {"thesis": "T1", "statement": "s_catl_storage_rev", "role": "supports",
     "reason": "龙头储能电池收入同比+62%，验证需求扩张已兑现至收入端",
     "scope": {"segment": "储能电池"}},
    {"thesis": "T1", "statement": "s_catl_shipment", "role": "supports",
     "reason": "储能电池出货量同比+58%，量的扩张直接印证需求",
     "scope": {"segment": "储能电池"}},
    {"thesis": "T1", "statement": "s_eve_capacity", "role": "supports",
     "reason": "二线厂商大额扩产响应需求增长，订单能见度延伸至2027年",
     "scope": {"segment": "储能电池产能"}},
    {"thesis": "T1", "statement": "s_sungrow_q1", "role": "supports",
     "reason": "储能系统商发货量同比+75%，终端需求景气传导至集成环节",
     "scope": {"segment": "储能系统"}},
    {"thesis": "T1", "statement": "s_sungrow_annual_pdf", "role": "supports",
     "reason": "阳光电源年报（PDF原件）显示储能系统收入同比+67.5%，交叉验证行业景气",
     "scope": {"segment": "储能系统", "source_format": "pdf"}},
    # Thesis 2 -> insufficient_evidence (transmission to margins unproven)
    {"thesis": "T2", "statement": "s_lithium_rebound", "role": "supports",
     "reason": "碳酸锂价格自低点回升约30%，成本端出现修复迹象",
     "scope": {"segment": "碳酸锂"}},
    {"thesis": "T2", "statement": "s_lithium_glut", "role": "contradicts",
     "reason": "供给过剩与库存高位未改，价格回升的持续性存疑",
     "scope": {"segment": "碳酸锂供给"}},
    {"thesis": "T2", "statement": "s_eve_mgmt_lag", "role": "contradicts",
     "reason": "管理层确认成本传导存在2-3个季度滞后，短期盈利弹性受限",
     "scope": {"segment": "成本传导"}},
    {"thesis": "T2", "statement": "s_citic_margin_caution", "role": "contradicts",
     "reason": "研报认为加工费仍下行、长单定价削弱传导，盈利修复弹性有限",
     "scope": {"segment": "中游材料盈利"}},
    # Thesis 3 -> contradicted
    {"thesis": "T3", "statement": "s_citic_catl_premium", "role": "supports",
     "reason": "研报给予储能分部估值溢价，看好海外长单盈利韧性",
     "scope": {"segment": "储能电池", "valuation": "分部估值"}},
    {"thesis": "T3", "statement": "s_catl_mgmt_guidance", "role": "supports",
     "reason": "管理层出货指引同比+50%以上，支撑高增长叙事",
     "scope": {"segment": "储能电池"}},
    {"thesis": "T3", "statement": "s_citic_price_war", "role": "contradicts",
     "reason": "电芯中标均价同比-15%，价格战侵蚀盈利，估值溢价持续性存疑",
     "scope": {"segment": "储能电芯", "valuation": "估值溢价"}},
    {"thesis": "T3", "statement": "s_catl_margin_down", "role": "contradicts",
     "reason": "储能毛利率同比下降3.2个百分点，高增长未转化为盈利质量，与估值溢价假设矛盾",
     "scope": {"segment": "储能电池", "valuation": "估值溢价"}},
]

ASSESSMENTS: list[dict] = [
    {
        "thesis": "T1", "conclusion": "supported",
        "rationale": "装机数据、龙头收入/出货、二线扩产与系统商发货多源交叉验证，证据一致支持储能需求扩张",
        "gaps": [],
    },
    {
        "thesis": "T2", "conclusion": "insufficient_evidence",
        "rationale": "碳酸锂回升仅为成本端迹象，供给过剩未改且传导存在滞后，缺少中游环节盈利修复的定量证据",
        "gaps": [
            "缺少正极/电解液等中游环节加工费与盈利定量数据",
            "碳酸锂价格向电芯成本传导的时滞缺乏定量验证",
        ],
    },
    {
        "thesis": "T3", "conclusion": "contradicted",
        "rationale": "储能电芯价格战导致中标均价同比-15%且宁德时代储能毛利率下滑3.2个百分点，高增长未支撑盈利质量，与估值溢价持续假设矛盾",
        "gaps": ["2026年储能电芯价格战持续性缺乏定量跟踪数据"],
    },
]

REVIEWER = "seed-human-reviewer"
REVIEWS: list[dict] = [
    {
        "thesis": "T1", "outcome": "confirmed", "conclusion": "supported",
        "reason": "人工确认：装机、收入、出货、扩产多源证据一致，维持 supported",
    },
    {
        "thesis": "T2", "outcome": "confirmed", "conclusion": "insufficient_evidence",
        "reason": "人工维持：成本传导时滞与供给过剩均有据，中游盈利修复缺定量证据，维持 insufficient_evidence",
    },
    {
        "thesis": "T3", "outcome": "confirmed", "conclusion": "contradicted",
        "reason": "人工维持：毛利率下滑与价格战证据与估值溢价假设直接冲突，维持 contradicted",
    },
]

COMPANIES: list[dict] = [
    {"key": "catl", "code": "300750", "name": "宁德时代", "type": "listed"},
    {"key": "eve", "code": "300014", "name": "亿纬锂能", "type": "listed"},
    {"key": "sungrow", "code": "300274", "name": "阳光电源", "type": "listed"},
]

STOCKS: list[dict] = [
    {"company": "catl", "code": "300750.SZ", "name": "宁德时代", "market": "SZSE"},
    {"company": "eve", "code": "300014.SZ", "name": "亿纬锂能", "market": "SZSE"},
    {"company": "sungrow", "code": "300274.SZ", "name": "阳光电源", "market": "SZSE"},
]

VALUATIONS: list[dict] = [
    {"stock": "catl", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("28.4"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
    {"stock": "catl", "as_of": date(2026, 6, 30), "metric": "PB",
     "value": Decimal("5.2"), "source": "wind", "definition": "总市值/归属股东权益"},
    {"stock": "sungrow", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("18.9"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
]

THEME_ROLES: list[dict] = [
    {"company": "catl", "role": "储能电池龙头",
     "scope": {"segment": "储能电池"}, "from": date(2026, 1, 1),
     "statement": "s_catl_shipment"},
    {"company": "eve", "role": "储能电池扩产方",
     "scope": {"segment": "储能电池产能"}, "from": date(2026, 1, 1),
     "statement": "s_eve_capacity"},
    {"company": "sungrow", "role": "储能系统集成商",
     "scope": {"segment": "储能系统"}, "from": date(2026, 1, 1),
     "statement": "s_sungrow_q1"},
]

FUND_COMPANIES: list[dict] = [
    {"key": "fc_yifangda", "code": "FC101", "name": "易方达基金管理有限公司"},
    {"key": "fc_guangfa", "code": "FC102", "name": "广发基金管理有限公司"},
]

FUNDS: list[dict] = [
    {"key": "fund_c", "code": "011479", "name": "易方达中证新能源主题ETF联接",
     "type": "指数", "scale": Decimal("8600000000"),
     "establish": date(2021, 3, 10), "mgmt": "fc_yifangda"},
    {"key": "fund_d", "code": "016858", "name": "广发储能产业混合",
     "type": "混合", "scale": Decimal("2100000000"),
     "establish": date(2022, 9, 20), "mgmt": "fc_guangfa"},
]

HOLDINGS: list[dict] = [
    {"fund": "fund_c", "stock": "catl", "weight": Decimal("0.091"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_c", "stock": "sungrow", "weight": Decimal("0.058"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_d", "stock": "catl", "weight": Decimal("0.076"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_d", "stock": "eve", "weight": Decimal("0.049"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    # Retained stale disclosure: superseded by the 2026Q1 row above.
    {"fund": "fund_d", "stock": "catl", "weight": Decimal("0.062"),
     "report_period": date(2025, 6, 30), "published_at": date(2025, 7, 24),
     "source": "fund-report-2025H1"},
]

# Causal chain for the focus thesis (T3: CATL 估值溢价持续性), human-reviewed.
CAUSAL_THESIS = "T3"
CAUSAL_CREATOR_TYPE = "human"
CAUSAL_REVIEW_STATE = "reviewed"

CAUSAL_STEPS: list[dict] = [
    {"seq": 1, "description": "全球储能需求爆发（海外大储+工商储）"},
    {"seq": 2, "description": "头部电池厂储能出货量增长"},
    {"seq": 3, "description": "宁德时代储能收入兑现"},
    {"seq": 4, "description": "国内电芯价格战侵蚀毛利率"},
    {"seq": 5, "description": "估值溢价难以仅靠收入高增长维持"},
]

CAUSAL_EDGES: list[dict] = [
    {"from": 1, "to": 2, "rationale": "终端装机需求传导至电池厂出货"},
    {"from": 2, "to": 3, "rationale": "出货增长带动储能收入兑现"},
    {"from": 3, "to": 4, "rationale": "收入扩张同时行业价格竞争加剧"},
    {"from": 4, "to": 5, "rationale": "毛利率下滑削弱估值溢价的盈利支撑"},
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


# PDF fixtures carry their metadata in this sidecar map (binary files cannot
# hold the ``# KEY: value`` header convention of the .txt fixtures).
PDF_FIXTURES: dict[str, dict] = {
    "06_sungrow_annual_summary.pdf": {
        "file_key": "sungrow_annual_summary_pdf",
        "source_url": "https://example.test/storage-chain/sungrow-2025-annual-summary-pdf",
        "published_at": "2026-03-25",
    }
}


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def seed(session: Session) -> None:
    """Seed the frozen storage-chain slice into ``session`` (offline).

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
            "SOURCE_URL", f"https://example.test/storage-chain/{file_key}"
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

    # 1b. Real PDF fixtures: parsed through pypdf, stamped with the pypdf
    #     parser version so parser generations are distinguishable.
    for filename, meta in sorted(PDF_FIXTURES.items()):
        raw = (FIXTURES_DIR / filename).read_bytes()
        version = document_service.freeze(
            raw=raw,
            source_url=meta["source_url"],
            published_at=_published_at({"PUBLISHED_AT": meta["published_at"]}),
            parser_version=PDF_PARSER_VERSION,
        )
        for locator, verbatim in pdf_extract_spans(raw):
            span = document_service.add_span(
                document_version_id=version.id,
                locator=locator,
                verbatim_text=verbatim,
            )
            span_index[(meta["file_key"], locator["page"], locator["paragraph"])] = span

    # 2. One ResearchCase + three Theses.
    case = research_service.add_case(
        title="锂电储能链",
        industry_topic="storage_chain",
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
        description="Seed the frozen storage-chain evidence slice (offline)."
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

    print("seeded storage chain case into", url)


if __name__ == "__main__":
    main()
