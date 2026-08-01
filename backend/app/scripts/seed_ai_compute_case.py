"""Seed the frozen AI-compute evidence slice into an append-only ledger.

This script is the fixed vertical-slice fixture import for the
"AI 算力链" ResearchCase. It is fully offline and reproducible:

* reads hand-prepared plain-text sample materials from
  ``tests/fixtures/ai_compute/*.txt`` (no network, no Docling),
* freezes each file into an immutable ``DocumentVersion`` by content hash,
* parses ``[PAGE n][PARA m]`` locatable spans from the text,
* wires the full auditable chain:
  DocumentVersion -> SourceSpan -> SourceStatement -> EvidenceLink ->
  Thesis -> EvidenceSnapshot -> AIAssessment, plus
  ThemeRole -> Company -> Stock -> ValuationSnapshot, and
  Fund -> HoldingDisclosure -> Stock.

The core function ``seed(session)`` accepts a SQLAlchemy ``Session`` and writes
exclusively through it.  ``main()`` reads ``DATABASE_URL`` (default
``sqlite:///./evidence_seed.db``), creates the schema, and runs the seed.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import UTC, date, datetime, timezone
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

FIXTURES_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ai_compute"
)

# Everything is frozen "now"; the snapshot cutoff is set after all material is
# available so every evidence link is visible and traceable.
CUTOFF = datetime(2026, 12, 31, tzinfo=timezone.utc)
CREATED_BY = "seed-ai-compute"

_META_RE = re.compile(r"^#\s*([A-Z_]+)\s*:\s*(.*)$", re.MULTILINE)
_SPAN_RE = re.compile(r"\[PAGE\s+(\d+)\]\s*\[PARA\s+(\d+)\]")

# ---------------------------------------------------------------------------
# Statement + link manifest.
#
# Each statement references a span by (file, page, para) and carries a kind,
# normalized text, and observed period. Each link wires a statement to a thesis
# (T1/T2/T3) with role/reason/scope. The thesis keys map to THESIS_STATEMENTS.
# ---------------------------------------------------------------------------

THESIS_STATEMENTS = {
    "T1": "2026年云厂商资本开支高增长将持续驱动AI算力需求扩张",
    "T2": "云厂商算力采购将沿供应链向代工/ODM端传导，带动工业富联AI服务器收入兑现",
    "T3": "寒武纪将在2026年兑现算力芯片出货并支撑当前估值",
}

STATEMENTS: list[dict] = [
    # ---- Thesis 1: cloud CapEx drives compute demand ----
    {
        "sid": "s_cloud_capex_total",
        "file": "cloud_vendor_capex_note",
        "page": 1, "para": 1,
        "kind": "disclosed_fact",
        "text": "2026年北美四大云厂商合计资本开支指引中值同比增长约36%，AI数据中心与GPU集群为增量核心投向",
        "period": date(2026, 5, 20),
    },
    {
        "sid": "s_cloud_capex_msft",
        "file": "cloud_vendor_capex_note",
        "page": 1, "para": 2,
        "kind": "disclosed_fact",
        "text": "微软2026年资本开支指引约950亿美元，同比增长约28%，主要用于Azure AI训练与推理集群扩容",
        "period": date(2026, 5, 20),
    },
    {
        "sid": "s_hynix_revenue",
        "file": "sk_hynix_quarterly_report",
        "page": 4, "para": 2,
        "kind": "disclosed_fact",
        "text": "SK海力士2026Q1营业收入17.63万亿韩元，同比增长82%，营业利润率39.2%创历史新高",
        "period": date(2026, 3, 31),
    },
    {
        "sid": "s_citic_capex_forecast",
        "file": "citic_research_report",
        "page": 3, "para": 1,
        "kind": "forecast",
        "text": "中信测算2026年海外四大云厂商合计资本开支将超过2800亿美元，同比增长约35%",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_citic_hbm_opinion",
        "file": "citic_research_report",
        "page": 7, "para": 1,
        "kind": "research_opinion",
        "text": "HBM景气度将在2026年延续，SK海力士HBM产能被云厂商锁定，存储CapEx扩张验证算力需求真实落地",
        "period": date(2026, 6, 10),
    },
    # ---- Thesis 2: transmission to foundry/ODM ----
    {
        "sid": "s_fii_ai_server_rev",
        "file": "fii_annual_report_disclosure",
        "page": 18, "para": 1,
        "kind": "disclosed_fact",
        "text": "工业富联2025年AI服务器及相关算力基础设施收入同比增长超过80%，占云计算板块收入比重约45%",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_fii_mgmt_visibility",
        "file": "fii_annual_report_disclosure",
        "page": 22, "para": 4,
        "kind": "management_attribution",
        "text": "工业富联管理层表示AI服务器订单能见度已延伸至2026年下半年",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_citic_transmission_forecast",
        "file": "citic_research_report",
        "page": 5, "para": 2,
        "kind": "forecast",
        "text": "中信预测工业富联2026年AI服务器收入有望同比增长60%以上，订单能见度清晰",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_guosen_transmission_caution",
        "file": "guosen_research_report",
        "page": 2, "para": 2,
        "kind": "research_opinion",
        "text": "国信认为市场对算力采购向代工端传导节奏过于乐观，工业富联AI服务器毛利率偏低，传导弹性需审慎验证",
        "period": date(2026, 6, 18),
    },
    {
        "sid": "s_fii_segment_gap",
        "file": "fii_annual_report_disclosure",
        "page": 25, "para": 1,
        "kind": "disclosed_fact",
        "text": "工业富联整体收入中代工及零部件业务占比较高，AI服务器分部数据尚需进一步披露",
        "period": date(2025, 12, 31),
    },
    # ---- Thesis 3: Cambricon delivery and valuation ----
    {
        "sid": "s_cambricon_placement_use",
        "file": "cambricon_private_placement_announcement",
        "page": 2, "para": 1,
        "kind": "disclosed_fact",
        "text": "寒武纪定增募资49.8亿元，投向新一代云端训练芯片、推理芯片与智能算力集群项目",
        "period": date(2026, 3, 15),
    },
    {
        "sid": "s_cambricon_mgmt_delivery",
        "file": "cambricon_private_placement_announcement",
        "page": 2, "para": 2,
        "kind": "management_attribution",
        "text": "寒武纪管理层表示思元系列芯片已在国内多个智算中心完成适配，预计随云厂商算力采购放量出货规模将显著提升",
        "period": date(2026, 3, 15),
    },
    {
        "sid": "s_cambricon_shipment",
        "file": "cambricon_annual_report",
        "page": 14, "para": 3,
        "kind": "disclosed_fact",
        "text": "寒武纪思元系列加速卡2025年出货量同比增长超过60%，已在15个规模化智算中心完成集群适配",
        "period": date(2025, 12, 31),
    },
    {
        "sid": "s_citic_cambricon_valuation",
        "file": "citic_research_report",
        "page": 10, "para": 3,
        "kind": "research_opinion",
        "text": "中信看好寒武纪思元系列在国产智算中心的兑现节奏，维持买入评级，对应2026年PE约85倍",
        "period": date(2026, 6, 10),
    },
    {
        "sid": "s_guosen_cambricon_valuation",
        "file": "guosen_research_report",
        "page": 4, "para": 1,
        "kind": "research_opinion",
        "text": "国信认为寒武纪当前股价对应2026年PE超过300倍，已显著透支出货预期，估值存在回调风险",
        "period": date(2026, 6, 18),
    },
]

LINKS: list[dict] = [
    # Thesis 1 -> supported
    {"thesis": "T1", "statement": "s_cloud_capex_total", "role": "supports",
     "reason": "海外云厂商CapEx指引中值同比高增，AI数据中心为增量核心，直接驱动算力需求",
     "scope": {"segment": "云厂商CapEx"}},
    {"thesis": "T1", "statement": "s_cloud_capex_msft", "role": "supports",
     "reason": "微软CapEx指引高增且投向Azure AI集群，验证云厂商算力采购",
     "scope": {"segment": "云厂商CapEx"}},
    {"thesis": "T1", "statement": "s_hynix_revenue", "role": "supports",
     "reason": "SK海力士Q1营收同比高增，存储景气由算力需求落地驱动",
     "scope": {"segment": "存储/HBM"}},
    {"thesis": "T1", "statement": "s_citic_capex_forecast", "role": "supports",
     "reason": "研报测算海外四大云厂商CapEx超2800亿美元，同比高增",
     "scope": {"segment": "云厂商CapEx"}},
    {"thesis": "T1", "statement": "s_citic_hbm_opinion", "role": "contextualizes",
     "reason": "HBM产能被云厂商锁定，存储CapEx扩张与算力需求一致",
     "scope": {"segment": "HBM"}},
    # Thesis 2 -> insufficient_evidence (missing direct transmission evidence)
    {"thesis": "T2", "statement": "s_fii_ai_server_rev", "role": "supports",
     "reason": "工业富联AI服务器收入同比高增体现向代工端传导迹象",
     "scope": {"level": "company", "segment": "AI服务器",
               "note": "公司整体口径，非AI服务器分部单独披露"}},
    {"thesis": "T2", "statement": "s_fii_mgmt_visibility", "role": "supports",
     "reason": "管理层确认AI服务器订单能见度延伸至2026H2",
     "scope": {"segment": "AI服务器代工"}},
    {"thesis": "T2", "statement": "s_citic_transmission_forecast", "role": "supports",
     "reason": "研报预测AI服务器收入同比+60%，传导兑现",
     "scope": {"segment": "AI服务器代工"}},
    {"thesis": "T2", "statement": "s_guosen_transmission_caution", "role": "contradicts",
     "reason": "国信认为代工传导节奏过于乐观，毛利率偏低，传导弹性需审慎验证",
     "scope": {"segment": "AI服务器代工"}},
    {"thesis": "T2", "statement": "s_fii_segment_gap", "role": "contextualizes",
     "reason": "AI服务器分部数据未单独披露，限制传导证据强度",
     "scope": {"level": "company", "note": "公司整体口径，分部缺失"}},
    # Thesis 3 -> contradicted
    {"thesis": "T3", "statement": "s_cambricon_placement_use", "role": "contextualizes",
     "reason": "募投方向聚焦算力芯片与集群，与兑现假设一致",
     "scope": {"segment": "AI算力芯片"}},
    {"thesis": "T3", "statement": "s_cambricon_mgmt_delivery", "role": "supports",
     "reason": "管理层确认思元系列适配完成，预计出货放量",
     "scope": {"segment": "AI算力芯片"}},
    {"thesis": "T3", "statement": "s_cambricon_shipment", "role": "supports",
     "reason": "思元加速卡出货同比+60%，支撑兑现节奏",
     "scope": {"segment": "AI算力芯片"}},
    {"thesis": "T3", "statement": "s_citic_cambricon_valuation", "role": "supports",
     "reason": "研报认为估值已部分反映出货预期，维持买入",
     "scope": {"segment": "AI算力芯片", "valuation": "PE"}},
    {"thesis": "T3", "statement": "s_guosen_cambricon_valuation", "role": "contradicts",
     "reason": "国信认为PE超300倍透支预期，估值存在回调风险，与兑现假设矛盾",
     "scope": {"segment": "AI算力芯片", "valuation": "PE"}},
]

ASSESSMENTS: list[dict] = [
    {
        "thesis": "T1", "conclusion": "supported",
        "rationale": "云厂商CapEx指引同比高增且SK海力士HBM/存储营收高增验证算力需求真实落地，证据一致支持命题",
        "gaps": [],
    },
    {
        "thesis": "T2", "conclusion": "insufficient_evidence",
        "rationale": "缺少从云厂商CapEx到代工端订单的直接传导证据，仅有公司整体收入与管理层定性表态，且研报观点分歧",
        "gaps": [
            "缺少云厂商CapEx向工业富联代工端订单传导的直接披露证据",
            "工业富联AI服务器分部数据未单独披露，无法验证传导弹性",
        ],
    },
    {
        "thesis": "T3", "conclusion": "contradicted",
        "rationale": "国信研报指出寒武纪PE超300倍透支出货预期且代工传导谨慎，与兑现并支撑估值的假设矛盾",
        "gaps": ["寒武纪2026年出货量缺乏直接定量披露"],
    },
]

# Human review of every AI assessment (gold-set 人工标签).  Reviews append
# ReviewDecision records; the original AI conclusion is never overwritten.
REVIEWER = "seed-human-reviewer"
REVIEWS: list[dict] = [
    {
        "thesis": "T1", "outcome": "confirmed", "conclusion": "supported",
        "reason": "人工确认：证据链自云厂商CapEx披露至存储营收高增，无直接矛盾，维持 supported",
    },
    {
        "thesis": "T2", "outcome": "confirmed", "conclusion": "insufficient_evidence",
        "reason": "人工维持：缺少云厂商CapEx→代工端订单的直接传导披露，且研报观点分歧，维持 insufficient_evidence",
    },
    {
        "thesis": "T3", "outcome": "confirmed", "conclusion": "contradicted",
        "reason": "人工维持：国信估值透支观点与兑现假设直接冲突，且公司尚未盈利，维持 contradicted",
    },
]

COMPANIES: list[dict] = [
    {"key": "cambricon", "code": "688256", "name": "寒武纪", "type": "listed"},
    {"key": "fii", "code": "601138", "name": "工业富联", "type": "listed"},
    {"key": "sk_hynix", "code": "000660.KS", "name": "SK海力士", "type": "listed"},
]

STOCKS: list[dict] = [
    {"company": "cambricon", "code": "688256.SH", "name": "寒武纪", "market": "SSE"},
    {"company": "fii", "code": "601138.SH", "name": "工业富联", "market": "SSE"},
    {"company": "sk_hynix", "code": "000660.KS", "name": "SK海力士", "market": "KRX"},
]

VALUATIONS: list[dict] = [
    {"stock": "cambricon", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("380.5"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
    {"stock": "cambricon", "as_of": date(2026, 6, 30), "metric": "PB",
     "value": Decimal("12.3"), "source": "wind", "definition": "总市值/归属股东权益"},
    {"stock": "fii", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("25.6"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
    {"stock": "fii", "as_of": date(2026, 6, 30), "metric": "PB",
     "value": Decimal("3.1"), "source": "wind", "definition": "总市值/归属股东权益"},
    {"stock": "sk_hynix", "as_of": date(2026, 6, 30), "metric": "PE_TTM",
     "value": Decimal("9.8"), "source": "wind",
     "definition": "总市值/近四月归母净利润"},
]

THEME_ROLES: list[dict] = [
    {"company": "cambricon", "role": "算力芯片受益方",
     "scope": {"segment": "AI算力芯片"}, "from": date(2026, 1, 1),
     "statement": "s_cambricon_shipment"},
    {"company": "fii", "role": "AI服务器代工方",
     "scope": {"segment": "AI服务器"}, "from": date(2026, 1, 1),
     "statement": "s_fii_ai_server_rev"},
    {"company": "sk_hynix", "role": "HBM/存储供应方",
     "scope": {"segment": "HBM/存储"}, "from": date(2026, 1, 1),
     "statement": "s_hynix_revenue"},
]

FUND_COMPANIES: list[dict] = [
    {"key": "fc_huaxia", "code": "FC001", "name": "华夏基金管理有限公司"},
    {"key": "fc_guotai", "code": "FC002", "name": "国泰基金管理有限公司"},
]

FUNDS: list[dict] = [
    {"key": "fund_a", "code": "008888", "name": "华夏国证半导体芯片ETF联接",
     "type": "指数", "scale": Decimal("5200000000"),
     "establish": date(2019, 12, 1), "mgmt": "fc_huaxia"},
    {"key": "fund_b", "code": "012345", "name": "国泰CES半导体行业混合",
     "type": "混合", "scale": Decimal("3200000000"),
     "establish": date(2020, 6, 15), "mgmt": "fc_guotai"},
]

HOLDINGS: list[dict] = [
    {"fund": "fund_a", "stock": "cambricon", "weight": Decimal("0.082"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_a", "stock": "fii", "weight": Decimal("0.065"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_b", "stock": "fii", "weight": Decimal("0.071"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    {"fund": "fund_b", "stock": "sk_hynix", "weight": Decimal("0.040"),
     "report_period": date(2026, 3, 31), "published_at": date(2026, 4, 22),
     "source": "fund-report-2026Q1"},
    # Retained stale disclosure (see failure-cases.md): report_period 2025H1,
    # superseded by the 2026Q1 disclosure above for the same stock+fund.
    {"fund": "fund_b", "stock": "fii", "weight": Decimal("0.055"),
     "report_period": date(2025, 6, 30), "published_at": date(2025, 7, 24),
     "source": "fund-report-2025H1"},
]

# ---------------------------------------------------------------------------
# Causal chain for the focus thesis (T3: 寒武纪兑现出货并支撑估值).
#
# A human-authored, reviewed causal chain describing how global AI compute
# demand transmits through domestic-chip substitution to Cambricon shipment,
# revenue, and finally valuation support.  Attached to T3 because the
# workbench renders the latest thesis's causal chain, and T3 (the Cambricon
# thesis, created last) is the latest thesis for the case.
# ---------------------------------------------------------------------------

CAUSAL_THESIS = "T3"
CAUSAL_CREATOR_TYPE = "human"
CAUSAL_REVIEW_STATE = "reviewed"

CAUSAL_STEPS: list[dict] = [
    {"seq": 1, "description": "全球 AI 算力需求爆发（大模型训练+推理）"},
    {"seq": 2, "description": "国产算力芯片需求提升（自主可控政策驱动）"},
    {"seq": 3, "description": "寒武纪思元系列芯片出货量增长"},
    {"seq": 4, "description": "寒武纪营收兑现且毛利率改善"},
    {"seq": 5, "description": "寒武纪估值获得业绩支撑"},
]

CAUSAL_EDGES: list[dict] = [
    {"from": 1, "to": 2, "rationale": "算力需求爆发传导至国产替代需求"},
    {"from": 2, "to": 3, "rationale": "国产需求提升推动寒武纪出货"},
    {"from": 3, "to": 4, "rationale": "出货量增长带动营收兑现"},
    {"from": 4, "to": 5, "rationale": "营收兑现支撑当前估值"},
]


# ---------------------------------------------------------------------------
# Fixture parsing
# ---------------------------------------------------------------------------


def _parse_fixture(path: Path) -> tuple[dict, bytes, list[tuple[dict, str]]]:
    """Return (metadata, raw_bytes, spans) for one fixture file.

    ``spans`` is a list of ``(locator, verbatim_text)`` parsed from
    ``[PAGE n][PARA m]`` markers.  The raw bytes cover the whole file so the
    content hash is stable and reproducible.
    """
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
    """Seed the frozen AI-compute slice into ``session`` (offline, reproducible).

    Writes exclusively through the provided session using the existing
    document/research/assessment/instrument services and repositories.  Does not
    commit; callers (tests, CLI) control the transaction.
    """
    document_service = DocumentService(DocumentRepository(session))
    research_service = ResearchService(ResearchRepository(session))
    assessment_service = AssessmentService(ResearchRepository(session))
    instruments = InstrumentRepository(session)
    research_repo = ResearchRepository(session)

    # 1. Freeze every fixture file into a DocumentVersion + SourceSpans.
    span_index: dict[tuple[str, int, int], object] = {}
    versions: dict[str, object] = {}
    for path in sorted(FIXTURES_DIR.glob("*.txt")):
        meta, raw, spans = _parse_fixture(path)
        file_key = meta.get("FILE", path.stem)
        source_url = meta.get(
            "SOURCE_URL", f"https://example.test/ai-compute/{file_key}"
        )
        version = document_service.freeze(
            raw=raw, source_url=source_url, published_at=_published_at(meta)
        )
        versions[file_key] = version
        for locator, verbatim in spans:
            span = document_service.add_span(
                document_version_id=version.id,
                locator=locator,
                verbatim_text=verbatim,
            )
            span_index[(file_key, locator["page"], locator["paragraph"])] = span

    if len(versions) < 6:
        raise RuntimeError(
            f"expected at least 6 frozen document versions, got {len(versions)}"
        )
    if len(span_index) < 30:
        raise RuntimeError(
            f"expected at least 30 source spans, got {len(span_index)}"
        )

    # 2. One ResearchCase + three Theses.
    case = research_service.add_case(
        title="AI 算力链",
        industry_topic="ai_compute",
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

    # 4. EvidenceLinks (supports / contradicts / contextualizes).
    links_by_thesis: dict[str, list] = {"T1": [], "T2": [], "T3": []}
    for spec in LINKS:
        link = research_service.link_evidence(
            theses[spec["thesis"]].id,
            statements[spec["statement"]].id,
            role=spec["role"],
            reason=spec["reason"],
            scope=spec["scope"],
        )
        links_by_thesis[spec["thesis"]].append(link)

    # 5. EvidenceSnapshot + AIAssessment per thesis.
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

    # 5b. Human review of every AI assessment (append-only ReviewDecision).
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
    #    Idempotent: skip when causal steps already exist for the thesis so
    #    re-running the seed on a populated ledger does not duplicate rows
    #    (CausalStep/CausalEdge are append-only and cannot be upserted).
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
        description="Seed the frozen AI-compute evidence slice (offline)."
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

    print("seeded AI compute case into", url)


if __name__ == "__main__":
    main()
