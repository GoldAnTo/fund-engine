"""Seed the immutable, provisional Cambricon profitability turning-point case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import (
    AIAssessment,
    Company,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
    Thesis,
)
from app.repositories.documents import DocumentRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.scripts.cambricon_profitability_data import CaseData, load_case_data
from app.services.assessment import AssessmentService
from app.services.ingest import DocumentService
from app.services.research import ResearchService
from app.services.themes import ThemeService


CASE_TITLE = "寒武纪 2025 年盈利拐点"
CREATED_BY = "codex-case-draft"
CAPTURED_AT = datetime.fromisoformat("2026-08-03T15:21:55+08:00")
# SQLite drops offsets; persist UTC-normalized values so its read-side
# compatibility helpers retain the actual capture instant.
CUTOFF = datetime(2026, 8, 3, 15, 59, 59, tzinfo=timezone.utc)
ASSESSMENT_RATIONALE = (
    "冻结数据支持会计利润口径的盈利拐点：2024Q4至2025Q4连续五个季度归母净利润为正，"
    "2025全年归母与扣非归母净利润均为正。该判断不证明国产算力需求是唯一原因，也不证明盈利可持续；"
    "2025全年经营现金流净额为负，需要后续验证回款与现金转化。"
)
ASSESSMENT_GAPS = [
    "需求到利润的可审计传导证据不足",
    "盈利持续性仍需后续季度与经营现金流验证",
]


@dataclass(frozen=True)
class DocumentManifest:
    key: str
    content_sha256: str
    source_url: str
    published_at: datetime | None


@dataclass(frozen=True)
class SourceStatementSpec:
    key: str
    document_key: str
    span_key: str
    kind: str
    normalized_text: str


@dataclass(frozen=True)
class EvidenceLinkSpec:
    statement_key: str
    role: str
    reason: str
    scope_json: str

    def scope(self) -> dict:
        return json.loads(self.scope_json)


STATEMENT_SPECS = (
    SourceStatementSpec(
        "2024_parent",
        "official_2024",
        "2024_parent",
        "disclosed_fact",
        "2024Q4归属于上市公司股东的净利润为272,152,952.65元",
    ),
    SourceStatementSpec(
        "2025_parent",
        "official_2025",
        "2025_parent",
        "disclosed_fact",
        "2025Q1至Q4归母净利润分别为355,465,241.04元、682,617,327.53元、566,563,175.54元、454,582,794.56元",
    ),
    SourceStatementSpec(
        "2025_adjusted",
        "official_2025",
        "2025_adjusted",
        "disclosed_fact",
        "2025Q1至Q4扣非归母净利润分别为275,962,803.95元、636,604,043.12元、506,321,130.23元、351,046,180.38元",
    ),
    SourceStatementSpec(
        "2025_cashflow",
        "official_2025",
        "2025_cashflow",
        "disclosed_fact",
        "2025Q1至Q4经营活动产生的现金流量净额分别为-1,399,358,712.85元、2,310,509,034.58元、-940,455,133.44元、-469,093,325.30元",
    ),
    SourceStatementSpec(
        "2025_revenue",
        "official_2025",
        "2025_revenue",
        "disclosed_fact",
        "2025Q1至Q4营业收入分别为1,111,398,926.80元、1,769,244,544.29元、1,726,780,892.57元、1,889,771,835.02元",
    ),
    SourceStatementSpec(
        "juyuan",
        "juyuan",
        "juyuan",
        "disclosed_fact",
        "Juyuan原始响应以累计值、亿元返回2025Q1为3.55、2025H1为10.38、2025Q1-Q3为16.05、2025FY为20.59",
    ),
)

LINK_SPECS = (
    EvidenceLinkSpec("2024_parent", "supports", "官方2024年报第11页显示2024Q4归母净利润为正，构成五季度序列的起点", '{"metric":"parent_profit","period":"2024Q4","source":"official"}'),
    EvidenceLinkSpec("2025_parent", "supports", "官方2025年报第10页显示Q1至Q4单季度归母净利润均为正", '{"metric":"parent_profit","period":"2025Q1-Q4","source":"official"}'),
    EvidenceLinkSpec("2025_parent", "supports", "2025全年归母净利润 = 355,465,241.04 + 682,617,327.53 + 566,563,175.54 + 454,582,794.56 = 2,059,228,538.67元，为正", '{"calculation_formula":"355465241.04 + 682617327.53 + 566563175.54 + 454582794.56 = 2059228538.67","metric":"parent_profit","period":"2025FY","precision":"exact_yuan","source":"official"}'),
    EvidenceLinkSpec("2025_adjusted", "supports", "2025全年扣非归母净利润 = 275,962,803.95 + 636,604,043.12 + 506,321,130.23 + 351,046,180.38 = 1,769,934,157.68元，为正", '{"calculation_formula":"275962803.95 + 636604043.12 + 506321130.23 + 351046180.38 = 1769934157.68","metric":"adjusted_parent_profit","period":"2025FY","precision":"exact_yuan","source":"official"}'),
    EvidenceLinkSpec("juyuan", "supports", "Juyuan原始响应仅以累计值、四舍五入后的20.59亿元对2025全年归母净利润作独立佐证；与官方精确金额2,059,228,538.67元按亿元四舍五入一致", '{"metric":"parent_profit","period":"2025FY","precision":"rounded_亿元","reconciliation":"Juyuan rounded cumulative value cross-check","source":"Juyuan"}'),
    EvidenceLinkSpec("2025_cashflow", "contextualizes", "2025全年经营现金流净额 = -1,399,358,712.85 + 2,310,509,034.58 - 940,455,133.44 - 469,093,325.30 = -498,398,137.01元，提示需要验证回款与现金转化，但不反驳会计利润口径的窄命题", '{"calculation_formula":"-1399358712.85 + 2310509034.58 - 940455133.44 - 469093325.30 = -498398137.01","metric":"operating_cash_flow","period":"2025FY","source":"official"}'),
    EvidenceLinkSpec("2025_parent", "contextualizes", "本链接仅适用于会计利润口径；官方利润数据本身不能证明国产算力需求是唯一原因或盈利可持续", '{"boundary":"accounting_profit_only","source":"official"}'),
    EvidenceLinkSpec("2025_revenue", "supports", "2025全年营业收入 = 1,111,398,926.80 + 1,769,244,544.29 + 1,726,780,892.57 + 1,889,771,835.02 = 6,497,196,198.68元，由四季度相加得到", '{"calculation_formula":"1111398926.80 + 1769244544.29 + 1726780892.57 + 1889771835.02 = 6497196198.68","metric":"operating_revenue","period":"2025FY","precision":"exact_yuan","source":"official"}'),
)

COMPANY_SPEC = ("688256", "寒武纪", "listed")
STOCK_SPEC = ("688256.SH", "寒武纪", "SSE")
THEME_ROLE = "盈利拐点验证对象"
THEME_ROLE_SCOPE_JSON = '{"boundary":"official quarterly profitability evidence only","company_subject":"寒武纪"}'
THEME_ROLE_STATEMENT_KEY = "2025_parent"


@dataclass(frozen=True)
class SeedResult:
    case_id: uuid.UUID
    thesis_id: uuid.UUID
    assessment_id: uuid.UUID
    created: bool


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _capture_time(data: CaseData) -> datetime:
    captured_at = datetime.fromisoformat(data.juyuan.fetched_at)
    if captured_at != CAPTURED_AT:
        raise RuntimeError("Cambricon fixture capture timestamp no longer matches the seed manifest")
    return captured_at.astimezone(timezone.utc)


def _document_manifest(data: CaseData) -> dict[str, DocumentManifest]:
    return {
        "juyuan": DocumentManifest(
            "juyuan",
            hashlib.sha256(data.juyuan.raw_response.encode("utf-8")).hexdigest(),
            "gildata-juyuan://FinQuery/688256/profitability/2026-08-03",
            None,
        ),
        "official_2025": DocumentManifest(
            "official_2025",
            hashlib.sha256(data.annual_report.verbatim_text.encode("utf-8")).hexdigest(),
            data.annual_report.source_url,
            datetime.fromisoformat(data.annual_report.published_at).astimezone(timezone.utc),
        ),
        "official_2024": DocumentManifest(
            "official_2024",
            hashlib.sha256(data.annual_report_2024.verbatim_text.encode("utf-8")).hexdigest(),
            data.annual_report_2024.source_url,
            datetime.fromisoformat(data.annual_report_2024.published_at).astimezone(timezone.utc),
        ),
    }


def _one(items: list, description: str):
    if len(items) != 1:
        raise RuntimeError(f"same-title case is partial or incomplete: expected one {description}")
    return items[0]


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _theme_role_scope() -> dict:
    return json.loads(THEME_ROLE_SCOPE_JSON)


def _complete_existing(session: Session, case: ResearchCase, data: CaseData) -> SeedResult:
    thesis = _one(
        list(session.scalars(select(Thesis).where(Thesis.research_case_id == case.id))),
        "thesis",
    )
    if (
        case.created_by != CREATED_BY
        or
        thesis.title != "会计利润口径的盈利拐点已经出现"
        or thesis.statement
        != "寒武纪自2024Q4至2025Q4连续五个季度单季度归母净利润为正，且2025年归母净利润与扣非归母净利润均为正"
        or thesis.creator_type != "ai"
        or thesis.review_state != "draft"
    ):
        raise RuntimeError("same-title case is partial or incomplete: thesis manifest mismatch")
    if (
        thesis.support_condition != "连续五季度单季度归母净利润为正且2025全年归母、扣非均为正"
        or thesis.falsification_condition != "任一季度归母净利润不为正，或2025全年归母/扣非任一不为正"
        or thesis.next_verification_event != "复核2026年季度利润与经营现金流，判断拐点可持续性"
    ):
        raise RuntimeError("same-title case is partial or incomplete: thesis manifest drift")
    snapshot = _one(
        list(session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.thesis_id == thesis.id))),
        "evidence snapshot",
    )
    assessment = _one(
        list(session.scalars(select(AIAssessment).where(AIAssessment.snapshot_id == snapshot.id))),
        "AI assessment",
    )
    if (
        _aware(snapshot.cutoff) != _aware(CUTOFF)
        or not snapshot.evidence_link_ids
        or assessment.conclusion != "supported"
        or assessment.rationale != ASSESSMENT_RATIONALE
        or assessment.gaps != ASSESSMENT_GAPS
        or not assessment.displayed_as_provisional
    ):
        raise RuntimeError("same-title case is partial or incomplete: assessment manifest mismatch")

    links = [session.get(EvidenceLink, uuid.UUID(value)) for value in snapshot.evidence_link_ids]
    if len(links) != len(LINK_SPECS) or any(link is None for link in links):
        raise RuntimeError("same-title case is partial or incomplete: snapshot links mismatch")
    document_manifest = _document_manifest(data)
    document_key_by_hash = {
        spec.content_sha256: key for key, spec in document_manifest.items()
    }
    expected_statements = {
        (spec.document_key, spec.kind, spec.normalized_text): spec
        for spec in STATEMENT_SPECS
    }
    document_ids: set[uuid.UUID] = set()
    statement_keys_by_id: dict[uuid.UUID, str] = {}
    for link in links:
        if (
            link.creator_type != "ai"
            or link.review_state != "machine_generated"
            or _aware(link.available_at) != _capture_time(data)
        ):
            raise RuntimeError("same-title case is partial or incomplete: link manifest mismatch")
        statement = session.get(SourceStatement, link.source_statement_id)
        if statement is None or not statement.normalized_text:
            raise RuntimeError("same-title case is partial or incomplete: missing source statement")
        span = session.get(SourceSpan, statement.source_span_id)
        if span is None or not span.verbatim_text or not span.locator:
            raise RuntimeError("same-title case is partial or incomplete: missing source span")
        version = session.get(DocumentVersion, span.document_version_id)
        if version is None or not version.source_url or not version.content_sha256:
            raise RuntimeError("same-title case is partial or incomplete: missing document version")
        document_key = document_key_by_hash.get(version.content_sha256)
        if document_key is None:
            raise RuntimeError("same-title case is partial or incomplete: source hashes mismatch")
        statement_spec = expected_statements.get(
            (document_key, statement.kind, statement.normalized_text)
        )
        if statement_spec is None:
            raise RuntimeError("same-title case is partial or incomplete: source statement manifest mismatch")
        existing_key = statement_keys_by_id.setdefault(statement.id, statement_spec.key)
        if existing_key != statement_spec.key:
            raise RuntimeError("same-title case is partial or incomplete: source statement reuse mismatch")
        document_ids.add(version.id)
    if (
        len(statement_keys_by_id) != len(STATEMENT_SPECS)
        or set(statement_keys_by_id.values()) != {spec.key for spec in STATEMENT_SPECS}
    ):
        raise RuntimeError("same-title case is partial or incomplete: source statement set mismatch")
    versions = [session.get(DocumentVersion, document_id) for document_id in document_ids]
    if len(versions) != len(document_manifest) or {
        version.content_sha256 for version in versions
    } != set(document_key_by_hash):
        raise RuntimeError("same-title case is partial or incomplete: source hashes mismatch")
    if any(
        _aware(version.acquired_at) != _capture_time(data)
        or version.source_url != document_manifest[document_key_by_hash[version.content_sha256]].source_url
        or (
            document_manifest[document_key_by_hash[version.content_sha256]].published_at is not None
            and _aware(version.published_at)
            != document_manifest[document_key_by_hash[version.content_sha256]].published_at
        )
        or (
            document_manifest[document_key_by_hash[version.content_sha256]].published_at is None
            and version.published_at is not None
        )
        for version in versions
    ):
        raise RuntimeError("same-title case is partial or incomplete: acquisition timestamp mismatch")

    actual_links = {
        (
            statement_keys_by_id[link.source_statement_id],
            link.role,
            link.reason,
            _canonical_json(link.scope),
        )
        for link in links
    }
    expected_links = {
        (spec.statement_key, spec.role, spec.reason, _canonical_json(spec.scope()))
        for spec in LINK_SPECS
    }
    if actual_links != expected_links:
        raise RuntimeError("same-title case is partial or incomplete: evidence link manifest mismatch")

    company = _one(
        list(session.scalars(select(Company).where(Company.code == COMPANY_SPEC[0]))),
        "Cambricon company",
    )
    stock = _one(
        list(session.scalars(select(Stock).where(Stock.code == STOCK_SPEC[0]))),
        "Cambricon stock",
    )
    roles = list(session.scalars(select(ThemeRole).where(ThemeRole.research_case_id == case.id)))
    expected_statement_ids = {
        key: statement_id
        for statement_id, key in statement_keys_by_id.items()
    }
    if (
        (company.code, company.name, company.type) != COMPANY_SPEC
        or (stock.code, stock.name, stock.market) != STOCK_SPEC
        or stock.company_id != company.id
        or len(roles) != 1
        or roles[0].company_id != company.id
        or roles[0].role != THEME_ROLE
        or roles[0].scope != _theme_role_scope()
        or roles[0].source_statement_id != expected_statement_ids[THEME_ROLE_STATEMENT_KEY]
    ):
        raise RuntimeError("same-title case is partial or incomplete: instrument manifest mismatch")
    if ThemeService(ResearchRepository(session)).effective_tags_for_case(case.id) != {"算力国产化"}:
        raise RuntimeError("same-title case is partial or incomplete: theme tag mismatch")
    return SeedResult(case.id, thesis.id, assessment.id, False)


def _insert_documents(document_repo: DocumentRepository, document_service: DocumentService, data: CaseData) -> dict[str, object]:
    capture_time = _capture_time(data)
    manifest = _document_manifest(data)
    contents = {
        "juyuan": (data.juyuan.raw_response.encode("utf-8"), "Juyuan FinQuery 原始响应"),
        "official_2025": (data.annual_report.verbatim_text.encode("utf-8"), data.annual_report.title),
        "official_2024": (data.annual_report_2024.verbatim_text.encode("utf-8"), data.annual_report_2024.title),
    }
    versions: dict[str, object] = {}
    for key, identity in manifest.items():
        raw, title = contents[key]
        versions[key] = document_repo.insert_version(
            content_sha256=identity.content_sha256,
            source_url=identity.source_url,
            published_at=identity.published_at,
            available_at=capture_time,
            acquired_at=capture_time,
            parser_version="cambricon-profitability-fixture-v1",
            supersedes_id=None,
            title=title,
            byte_size=len(raw),
            language="zh",
        )

    return {
        "juyuan": document_service.add_span(
            versions["juyuan"].id,
            {"source": "raw_response", "provider": data.juyuan.provider, "tool": data.juyuan.tool},
            data.juyuan.raw_response,
        ),
        "2025_parent": document_service.add_span(
            versions["official_2025"].id,
            {"page": data.annual_report.page, "section": "2025年分季度主要财务数据", "metric": "归母净利润"},
            data.annual_report.verbatim_text,
        ),
        "2025_adjusted": document_service.add_span(
            versions["official_2025"].id,
            {"page": data.annual_report.page, "section": "2025年分季度主要财务数据", "metric": "扣非归母净利润"},
            data.annual_report.verbatim_text,
        ),
        "2025_cashflow": document_service.add_span(
            versions["official_2025"].id,
            {"page": data.annual_report.page, "section": "2025年分季度主要财务数据", "metric": "经营活动产生的现金流量净额"},
            data.annual_report.verbatim_text,
        ),
        "2025_revenue": document_service.add_span(
            versions["official_2025"].id,
            {"page": data.annual_report.page, "section": "2025年分季度主要财务数据", "metric": "营业收入"},
            data.annual_report.verbatim_text,
        ),
        "2024_parent": document_service.add_span(
            versions["official_2024"].id,
            {"page": data.annual_report_2024.page, "section": "2024年分季度主要财务数据", "metric": "归母净利润"},
            data.annual_report_2024.verbatim_text,
        ),
    }


def seed(session: Session) -> SeedResult:
    """Write the complete immutable case, or fail closed on a same-title partial case."""
    data = load_case_data()
    same_title_cases = list(session.scalars(select(ResearchCase).where(ResearchCase.title == CASE_TITLE)))
    if same_title_cases:
        if len(same_title_cases) != 1:
            raise RuntimeError("same-title case is partial or incomplete: duplicate cases")
        return _complete_existing(session, same_title_cases[0], data)

    document_repo = DocumentRepository(session)
    document_service = DocumentService(document_repo)
    research_repo = ResearchRepository(session)
    research_service = ResearchService(research_repo)
    assessment_service = AssessmentService(research_repo)
    instruments = InstrumentRepository(session)
    spans = _insert_documents(document_repo, document_service, data)

    case = research_service.add_case(
        title=CASE_TITLE,
        industry_topic="ai_compute",
        created_by=CREATED_BY,
        research_object="寒武纪（688256）",
        phenomenon="会计利润由亏转盈",
        core_question="会计利润口径的盈利拐点是否已经出现",
    )
    thesis = research_service.add_thesis(
        case.id,
        statement="寒武纪自2024Q4至2025Q4连续五个季度单季度归母净利润为正，且2025年归母净利润与扣非归母净利润均为正",
        title="会计利润口径的盈利拐点已经出现",
        support_condition="连续五季度单季度归母净利润为正且2025全年归母、扣非均为正",
        falsification_condition="任一季度归母净利润不为正，或2025全年归母/扣非任一不为正",
        next_verification_event="复核2026年季度利润与经营现金流，判断拐点可持续性",
        created_by=CREATED_BY,
        creator_type="ai",
        review_state="draft",
    )

    statements = {
        spec.key: research_service.add_statement(
            spans[spec.span_key].id,
            spec.normalized_text,
            kind=spec.kind,
        )
        for spec in STATEMENT_SPECS
    }
    for spec in LINK_SPECS:
        research_service.link_evidence(
            thesis.id,
            statements[spec.statement_key].id,
            role=spec.role,
            reason=spec.reason,
            scope=spec.scope(),
            available_at=_capture_time(data),
        )

    snapshot = assessment_service.freeze_snapshot(thesis.id, cutoff=CUTOFF)
    if len(snapshot.evidence_link_ids) != len(LINK_SPECS):
        raise RuntimeError("Cambricon snapshot did not capture every expected evidence link")
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale=ASSESSMENT_RATIONALE,
        gaps=ASSESSMENT_GAPS,
    )
    company = instruments.add_company(
        code=COMPANY_SPEC[0], name=COMPANY_SPEC[1], type=COMPANY_SPEC[2]
    )
    instruments.add_stock(
        company_id=company.id,
        code=STOCK_SPEC[0],
        name=STOCK_SPEC[1],
        market=STOCK_SPEC[2],
    )
    instruments.add_theme_role(
        company_id=company.id,
        research_case_id=case.id,
        role=THEME_ROLE,
        scope=_theme_role_scope(),
        applicable_from=None,
        source_statement_id=statements[THEME_ROLE_STATEMENT_KEY].id,
    )
    ThemeService(research_repo).apply_theme_tags(case=case, desired=["算力国产化"], proposed_by="human")
    return SeedResult(case.id, thesis.id, assessment.id, True)


def main() -> None:
    engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db"), future=True)
    from app.models.ledger import Base

    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as session:
        result = seed(session)
        session.commit()
    print(json.dumps({
        "created": result.created,
        "case_id": str(result.case_id),
        "thesis_id": str(result.thesis_id),
        "assessment_id": str(result.assessment_id),
        "conclusion_url": f"http://localhost:5173/conclusion/{result.case_id}",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
