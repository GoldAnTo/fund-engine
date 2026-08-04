"""Acceptance tests for the reproducible Cambricon profitability case seed."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.ledger import (
    AIAssessment,
    CaseThemeTagEvent,
    Company,
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
    Thesis,
    Base,
)
from app.repositories.research import ResearchRepository
from app.scripts.cambricon_profitability_data import load_case_data
from app.scripts.seed_cambricon_profitability_case import (
    ASSESSMENT_GAPS,
    ASSESSMENT_RATIONALE,
    CASE_TITLE,
    CUTOFF,
    seed,
)
from app.services.assessment import AssessmentService
from app.services.research import ResearchService
from app.services.review import ReviewService
from app.services.themes import ThemeService


def _rows(session, model):
    return list(session.scalars(select(model)))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _counts(session) -> dict[str, int]:
    return {
        model.__name__: len(_rows(session, model))
        for model in (
            ResearchCase,
            Thesis,
            DocumentVersion,
            SourceSpan,
            SourceStatement,
            EvidenceLink,
            EvidenceSnapshot,
            AIAssessment,
            ReviewDecision,
            EvidenceReview,
            Company,
            Stock,
            ThemeRole,
            CaseThemeTagEvent,
        )
    }


def _clone_full_looking_legacy_case(
    source,
    target,
    *,
    thesis_overrides: dict[str, str] | None = None,
    legacy_semantics: bool = False,
) -> None:
    """Copy a complete seed without mutating its immutable source rows."""
    models = (
        DocumentVersion,
        SourceSpan,
        ResearchCase,
        Thesis,
        SourceStatement,
        EvidenceLink,
        EvidenceSnapshot,
        AIAssessment,
        Company,
        Stock,
        ThemeRole,
        CaseThemeTagEvent,
    )
    for model in models:
        for entity in source.scalars(select(model)):
            values = {
                column.name: getattr(entity, column.name)
                for column in model.__table__.columns
            }
            if model is Thesis and thesis_overrides:
                values.update(thesis_overrides)
            if (
                legacy_semantics
                and
                model is SourceStatement
                and values["normalized_text"].startswith("2025Q1至Q4归母净利润")
            ):
                values["normalized_text"] = "2025年归母净利润为2,059,228,538.67元（由官方分季度数据加总）"
            if legacy_semantics and model is ThemeRole:
                values["role"] = "国产算力芯片公司"
                values["scope"] = {"theme": "算力国产化", "boundary": "classification_only"}
            target.add(model(**values))
    target.flush()


def test_seed_creates_one_complete_provisional_profitability_case(session):
    result = seed(session)

    assert result.created is True
    assert _counts(session) == {
        "ResearchCase": 1,
        "Thesis": 1,
        "DocumentVersion": 3,
        "SourceSpan": 6,
        "SourceStatement": 6,
        "EvidenceLink": 8,
        "EvidenceSnapshot": 1,
        "AIAssessment": 1,
        "ReviewDecision": 0,
        "EvidenceReview": 0,
        "Company": 1,
        "Stock": 1,
        "ThemeRole": 1,
        "CaseThemeTagEvent": 1,
    }

    case = session.get(ResearchCase, result.case_id)
    thesis = session.get(Thesis, result.thesis_id)
    assessment = session.get(AIAssessment, result.assessment_id)
    assert case is not None and case.title == CASE_TITLE
    assert thesis is not None
    assert thesis.title == "会计利润口径的盈利拐点已经出现"
    assert thesis.statement == (
        "寒武纪自2024Q4至2025Q4连续五个季度单季度归母净利润为正，"
        "且2025年归母净利润与扣非归母净利润均为正"
    )
    assert thesis.support_condition == "连续五季度单季度归母净利润为正且2025全年归母、扣非均为正"
    assert thesis.falsification_condition == "任一季度归母净利润不为正，或2025全年归母/扣非任一不为正"
    assert thesis.next_verification_event == "复核2026年季度利润与经营现金流，判断拐点可持续性"
    assert thesis.creator_type == "ai"
    assert thesis.review_state == "draft"
    assert assessment is not None
    assert assessment.conclusion == "supported"
    assert assessment.displayed_as_provisional is True
    assert assessment.rationale == ASSESSMENT_RATIONALE
    assert assessment.gaps == ASSESSMENT_GAPS


def test_seed_preserves_source_precision_roles_and_complete_traceability(session):
    result = seed(session)
    data = load_case_data()

    versions = _rows(session, DocumentVersion)
    assert {version.content_sha256 for version in versions} == {
        hashlib.sha256(data.juyuan.raw_response.encode("utf-8")).hexdigest(),
        hashlib.sha256(data.annual_report.verbatim_text.encode("utf-8")).hexdigest(),
        hashlib.sha256(data.annual_report_2024.verbatim_text.encode("utf-8")).hexdigest(),
    }
    assert {version.source_url for version in versions} == {
        "gildata-juyuan://FinQuery/688256/profitability/2026-08-03",
        data.annual_report.source_url,
        data.annual_report_2024.source_url,
    }
    capture_time = datetime.fromisoformat(data.juyuan.fetched_at)
    assert all(_aware(version.acquired_at) == capture_time for version in versions)
    by_url = {version.source_url: version for version in versions}
    assert _aware(by_url[data.annual_report.source_url].published_at) == datetime.fromisoformat(
        data.annual_report.published_at
    )
    assert _aware(by_url[data.annual_report_2024.source_url].published_at) == datetime.fromisoformat(
        data.annual_report_2024.published_at
    )
    assert by_url["gildata-juyuan://FinQuery/688256/profitability/2026-08-03"].published_at is None

    snapshot = session.get(EvidenceSnapshot, session.get(AIAssessment, result.assessment_id).snapshot_id)
    assert snapshot is not None and snapshot.evidence_link_ids
    assert _aware(snapshot.cutoff) == CUTOFF
    links = [session.get(EvidenceLink, uuid.UUID(link_id)) for link_id in snapshot.evidence_link_ids]
    assert all(link is not None for link in links)
    assert {link.role for link in links} == {"supports", "contextualizes"}
    assert all(link.creator_type == "ai" and link.review_state == "machine_generated" for link in links)
    assert all(_aware(link.available_at) == capture_time for link in links)

    juyuan_links = [link for link in links if link.scope.get("source") == "Juyuan"]
    assert len(juyuan_links) == 1
    assert juyuan_links[0].role == "supports"
    assert juyuan_links[0].scope["precision"] == "rounded_亿元"
    assert "20.59亿元" in juyuan_links[0].reason
    assert not any(link.role == "contradicts" for link in links)
    assert any(
        link.role == "contextualizes" and link.scope.get("boundary") == "accounting_profit_only"
        for link in links
    )
    assert any(
        link.role == "contextualizes" and link.scope.get("metric") == "operating_cash_flow"
        for link in links
    )

    for link in links:
        statement = session.get(SourceStatement, link.source_statement_id)
        assert statement is not None and statement.normalized_text
        span = session.get(SourceSpan, statement.source_span_id)
        assert span is not None and span.verbatim_text and span.locator
        version = session.get(DocumentVersion, span.document_version_id)
        assert version is not None and version.source_url and version.content_sha256
        assert _aware(link.available_at) >= _aware(version.acquired_at)


def test_seed_keeps_source_statements_literal_and_derivations_only_in_links(session):
    result = seed(session)
    assessment = session.get(AIAssessment, result.assessment_id)
    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    links = [session.get(EvidenceLink, uuid.UUID(link_id)) for link_id in snapshot.evidence_link_ids]
    statements = {
        session.get(SourceStatement, link.source_statement_id).normalized_text
        for link in links
    }

    assert "2025Q1至Q4归母净利润分别为355,465,241.04元、682,617,327.53元、566,563,175.54元、454,582,794.56元" in statements
    assert "2025Q1至Q4扣非归母净利润分别为275,962,803.95元、636,604,043.12元、506,321,130.23元、351,046,180.38元" in statements
    assert "2025Q1至Q4经营活动产生的现金流量净额分别为-1,399,358,712.85元、2,310,509,034.58元、-940,455,133.44元、-469,093,325.30元" in statements
    assert "2025Q1至Q4营业收入分别为1,111,398,926.80元、1,769,244,544.29元、1,726,780,892.57元、1,889,771,835.02元" in statements
    assert "Juyuan原始响应以累计值、亿元返回2025Q1为3.55、2025H1为10.38、2025Q1-Q3为16.05、2025FY为20.59" in statements
    assert not any("加总" in statement or "相符" in statement for statement in statements)
    assert any(
        link.scope.get("calculation_formula")
        and "2025全年归母净利润" in link.reason
        for link in links
    )
    assert any(
        link.scope.get("calculation_formula")
        and "2025全年扣非归母净利润" in link.reason
        for link in links
    )
    assert any(
        link.scope.get("calculation_formula")
        and "经营现金流净额" in link.reason
        for link in links
    )
    assert any(
        link.scope.get("reconciliation") == "Juyuan rounded cumulative value cross-check"
        for link in links
    )


def test_seed_adds_confirmed_controlled_theme_and_only_cambricon_instrument(session):
    result = seed(session)

    assert ThemeService(ResearchRepository(session)).effective_tags_for_case(result.case_id) == {"算力国产化"}
    company = _rows(session, Company)[0]
    stock = _rows(session, Stock)[0]
    role = _rows(session, ThemeRole)[0]
    assert (company.code, company.name) == ("688256", "寒武纪")
    assert (stock.code, stock.company_id) == ("688256.SH", company.id)
    assert role.company_id == company.id and role.research_case_id == result.case_id
    assert role.source_statement_id is not None
    assert role.role == "盈利拐点验证对象"
    assert role.scope == {
        "boundary": "official quarterly profitability evidence only",
        "company_subject": "寒武纪",
    }
    assert "芯片" not in role.role
    statement = session.get(SourceStatement, role.source_statement_id)
    span = session.get(SourceSpan, statement.source_span_id)
    assert "寒武纪" in span.verbatim_text


def test_seed_is_idempotent_only_after_full_completeness_check(session):
    first = seed(session)
    before = _counts(session)
    second = seed(session)

    assert second == type(first)(first.case_id, first.thesis_id, first.assessment_id, False)
    assert _counts(session) == before


def test_seed_fails_closed_for_same_title_partial_case_without_repair(session):
    partial = ResearchService(ResearchRepository(session)).add_case(
        title=CASE_TITLE, industry_topic="ai_compute", created_by="test"
    )
    before = _counts(session)

    with pytest.raises(RuntimeError, match="partial|incomplete|same-title"):
        seed(session)

    assert session.get(ResearchCase, partial.id) is not None
    assert _counts(session) == before


def test_seed_rejects_a_full_looking_legacy_graph_with_broken_manifest(session):
    seed(session)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    target = sessionmaker(bind=engine, future=True)()
    try:
        _clone_full_looking_legacy_case(session, target, legacy_semantics=True)
        before = _counts(target)

        with pytest.raises(RuntimeError, match="partial|incomplete|same-title"):
            seed(target)

        assert _counts(target) == before
    finally:
        target.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("support_condition", "错误的支持条件"),
        ("falsification_condition", "错误的证伪条件"),
        ("next_verification_event", "错误的后续验证事件"),
    ],
)
def test_seed_rejects_full_looking_graph_with_thesis_manifest_drift(
    session, field, replacement
):
    seed(session)
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    target = sessionmaker(bind=engine, future=True)()
    try:
        _clone_full_looking_legacy_case(
            session, target, thesis_overrides={field: replacement}
        )
        before = _counts(target)

        with pytest.raises(RuntimeError, match="partial|incomplete|same-title"):
            seed(target)

        assert _counts(target) == before
    finally:
        target.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_seed_is_idempotent_after_legitimate_later_human_reviews(session):
    result = seed(session)
    assessment = session.get(AIAssessment, result.assessment_id)
    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    review_service = ReviewService(ResearchRepository(session))
    for link_id in snapshot.evidence_link_ids:
        link = session.get(EvidenceLink, uuid.UUID(link_id))
        review_service.review_link(
            link.id,
            outcome="confirmed",
            relation=link.role,
            factor_role="财务披露证据",
            scope_boundary="会计利润口径",
            reason="后续人工审核，不改变种子基础账本",
            reviewer="test-reviewer",
        )
    AssessmentService(ResearchRepository(session)).review(
        result.assessment_id,
        outcome="confirmed",
        conclusion="supported",
        reason="后续人工审核，不改变种子基础账本",
        reviewer="test-reviewer",
    )
    before = _counts(session)

    repeated = seed(session)

    assert repeated == type(result)(result.case_id, result.thesis_id, result.assessment_id, False)
    assert _counts(session) == before


def test_seed_does_not_expose_fixture_evidence_before_capture(session):
    result = seed(session)
    repository = ResearchRepository(session)
    before_capture = datetime.fromisoformat(load_case_data().juyuan.fetched_at).astimezone(UTC) - timedelta(microseconds=1)

    assert repository.visible_links(
        thesis_id=result.thesis_id, cutoff=before_capture
    ) == []
    assert len(repository.visible_links(thesis_id=result.thesis_id, cutoff=CUTOFF)) == 8
