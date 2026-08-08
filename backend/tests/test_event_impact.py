from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models as models
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyImpactRelationReview,
    CompanyIdentityAlias,
    EventImpactRefreshClaim,
    EventImpactHypothesis,
    EventImpactHypothesisAssessment,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    EvidenceLink,
    ImmutableLedgerError,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    Thesis,
    ValuationSnapshot,
)
from app.services.china_market_data import LedgerChinaMarketData
from app.services.event_impact import (
    EventImpactResearchService,
    ResolvedImpactCompany,
)
from app.errors import ValidationFailedError
from app.models.events import DomainEvent
from app.models.operational import EventResearchLifecycle, ResearchRun, ResearchTask
from app.services.auto_research import AutoResearchService
from app.repositories.auto_research import AutoResearchRepository
from app.services.event_conclusion import EventConclusionService


NOW = datetime(2026, 8, 8, tzinfo=UTC)
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0019_event_impact_traces.py"
)
MIGRATION_0020_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0020_event_impact_refresh_claims.py"
)
MIGRATION_0021_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0021_company_identity_aliases.py"
)
MIGRATION_0022_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0022_china_market_data_indexes.py"
)
MIGRATION_0023_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0023_event_impact_assessments.py"
)


def _scope(session, research_case, *, version: int = 1) -> EventResearchScopeVersion:
    scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=version,
        changed_by="tester",
        change_summary="fixture scope",
        created_at=NOW,
    )
    session.add(scope)
    session.commit()
    return scope


def _company(session, *, code: str, company_type: str) -> Company:
    company = Company(code=code, name=code, type=company_type, created_at=NOW)
    session.add(company)
    session.commit()
    return company


def _hypothesis(
    session,
    research_case_id,
    scope_version_id,
    *,
    statement: str = "资本开支增加会提高中国供应商订单",
    rank: int = 1,
) -> EventImpactHypothesis:
    hypothesis = EventImpactHypothesis(
        research_case_id=research_case_id,
        scope_version_id=scope_version_id,
        statement=statement,
        classification="candidate",
        rank=rank,
        score_components={"event_relevance": 0.5},
        explanation="等待验证",
        created_at=NOW,
    )
    session.add(hypothesis)
    session.commit()
    return hypothesis


def _relation(session, hypothesis, scope, company) -> CompanyImpactRelation:
    relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=scope.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        status="candidate",
        source_statement_id=None,
        created_at=NOW,
    )
    session.add(relation)
    session.commit()
    return relation


def _verified_relation(session, hypothesis, scope, company) -> CompanyImpactRelation:
    relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=scope.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        status="verified",
        source_statement_id=None,
        created_at=NOW,
    )
    session.add(relation)
    session.flush()
    return relation


def _verified_impact_observation(session, relation, kind: str) -> None:
    session.add(
        CompanyImpactObservation(
            relation_id=relation.id,
            kind=kind,
            status="verified",
            source_statement_id=None,
            valuation_snapshot_id=None,
            summary=f"verified {kind} evidence",
            as_of_date=date(2026, 8, 8),
            created_at=NOW,
        )
    )


def _formal_scope_coverage(session, research_case, scope, factor: str) -> None:
    """Create the pre-existing reviewed formal coverage required by drafts."""
    thesis = Thesis(
        research_case_id=research_case.id,
        statement=factor,
        created_by="tester",
        created_at=NOW,
    )
    session.add(thesis)
    session.flush()
    for ordinal in (1, 2):
        document = DocumentVersion(
            content_sha256=uuid.uuid5(
                uuid.NAMESPACE_URL, f"formal-coverage/{factor}/{ordinal}"
            ).hex,
            source_url=f"https://example.test/formal/{ordinal}",
            available_at=NOW,
            acquired_at=NOW,
            parser_version="html-v1",
            parse_state="success",
        )
        session.add(document)
        session.flush()
        session.add(
            CaseDocumentVersion(
                research_case_id=research_case.id,
                document_version_id=document.id,
                linked_at=NOW,
            )
        )
        span = SourceSpan(
            document_version_id=document.id,
            locator={"ordinal": ordinal},
            verbatim_text=f"formal evidence {ordinal}",
        )
        session.add(span)
        session.flush()
        statement = SourceStatement(
            source_span_id=span.id,
            kind="fact",
            normalized_text=f"formal evidence {ordinal}",
            observed_period=date(2026, 8, 8),
            created_at=NOW,
        )
        session.add(statement)
        session.flush()
        link = EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason="formal fixture evidence",
            scope={},
            available_at=NOW,
            creator_type="human",
            review_state="reviewed",
            created_at=NOW,
        )
        session.add(link)
        session.flush()
        session.add(
            EventResearchScopeEvidenceAssignment(
                scope_version_id=scope.id,
                evidence_link_id=link.id,
                factor_statement=factor,
                disposition="mapped",
                created_at=NOW,
            )
        )
    session.commit()


def test_classify_competes_current_scope_hypotheses_without_mutating_history(
    session, research_case
) -> None:
    scope = _event_scope(
        session,
        research_case,
        factors=["完整传导", "市场替代解释"],
    )
    company = _company(session, code="COMPLETE-CO", company_type="listed")
    complete = _hypothesis(
        session,
        research_case.id,
        scope.id,
        statement="完整传导",
        rank=2,
    )
    market_only = _hypothesis(
        session,
        research_case.id,
        scope.id,
        statement="市场替代解释",
        rank=1,
    )
    complete_relation = _verified_relation(session, complete, scope, company)
    market_relation = _verified_relation(session, market_only, scope, company)
    stock = Stock(
        company_id=company.id,
        code="600001.SH",
        name="Complete Co",
        market="SSE",
        created_at=NOW,
    )
    fund = Fund(
        code="000001",
        name="China fund",
        fund_type="equity",
        scale=None,
        establish_date=None,
        management_company_id=None,
        created_at=NOW,
    )
    session.add_all([stock, fund])
    session.flush()
    session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=Decimal("0.12"),
            report_period=date(2026, 7, 31),
            published_at=NOW,
            acquired_at=NOW,
            source="fixture holding",
            created_at=NOW,
        )
    )
    for kind in ("event", "relation", "operating", "market", "peer_control"):
        _verified_impact_observation(session, complete_relation, kind)
    for kind in ("event", "relation", "market"):
        _verified_impact_observation(session, market_relation, kind)
    session.commit()

    assessments = EventImpactResearchService(session).classify(research_case.id)
    session.commit()

    assert [(row.hypothesis_id, row.classification, row.rank) for row in assessments] == [
        (complete.id, "key", 1),
        (market_only.id, "alternative", 2),
    ]
    assert assessments[0].score_components == {
        "event": 1,
        "company": 1,
        "operating": 1,
        "market": 1,
        "peer_control": 1,
        "fund_coverage": 1,
    }
    # The original candidate remains an immutable trace rather than being
    # overwritten by the new assessment row.
    session.refresh(complete)
    assert complete.classification == "candidate"
    assert complete.rank == 2
    assert complete.score_components == {"event_relevance": 0.5}

    later_assessments = EventImpactResearchService(session).classify(research_case.id)
    session.commit()
    assert later_assessments[0].id != assessments[0].id
    assert [
        row.classification
        for row in session.scalars(
            select(EventImpactHypothesisAssessment)
            .where(EventImpactHypothesisAssessment.hypothesis_id == complete.id)
            .order_by(EventImpactHypothesisAssessment.created_at)
        )
    ] == ["key", "key"]

    _formal_scope_coverage(session, research_case, scope, "完整传导")
    _formal_scope_coverage(session, research_case, scope, "市场替代解释")
    draft = EventConclusionService(session).create_draft(research_case.id)
    assert draft.scope_version_id == scope.id


def test_market_only_impact_assessment_blocks_draft_despite_formal_coverage(
    session, research_case
) -> None:
    factor = "市场替代解释"
    scope = _event_scope(session, research_case, factors=[factor])
    company = _company(session, code="MARKET-ONLY", company_type="listed")
    hypothesis = _hypothesis(
        session, research_case.id, scope.id, statement=factor
    )
    relation = _verified_relation(session, hypothesis, scope, company)
    for kind in ("event", "relation", "market"):
        _verified_impact_observation(session, relation, kind)
    session.commit()
    _formal_scope_coverage(session, research_case, scope, factor)

    assessment = EventImpactResearchService(session).classify(research_case.id)[0]
    assert assessment.classification == "alternative"
    with pytest.raises(ValidationFailedError, match="impact coverage is insufficient"):
        EventConclusionService(session).create_draft(research_case.id)


def test_candidate_relation_observation_cannot_make_a_key_factor(
    session, research_case
) -> None:
    factor = "待审核传导"
    scope = _event_scope(session, research_case, factors=[factor])
    company = _company(session, code="CANDIDATE-ONLY", company_type="listed")
    hypothesis = _hypothesis(
        session, research_case.id, scope.id, statement=factor
    )
    relation = _relation(session, hypothesis, scope, company)
    for kind in ("event", "relation", "operating", "market", "peer_control"):
        _verified_impact_observation(session, relation, kind)
    session.commit()

    assessment = EventImpactResearchService(session).classify(research_case.id)[0]

    assert assessment.score_components["company"] == 0
    assert assessment.classification == "alternative"


def _classification_select_count(session, research_case, relation_count: int) -> int:
    factor = f"bulk classification {relation_count}"
    scope = _event_scope(session, research_case, factors=[factor])
    company = _company(
        session, code=f"BULK-CLASSIFY-{relation_count}", company_type="listed"
    )
    stock = Stock(
        company_id=company.id,
        code=f"6000{relation_count:02d}.SH",
        name="Bulk classify stock",
        market="SSE",
        created_at=NOW,
    )
    session.add(stock)
    for rank in range(1, relation_count + 1):
        hypothesis = _hypothesis(
            session,
            research_case.id,
            scope.id,
            statement=f"{factor}-{rank}",
            rank=rank,
        )
        relation = _verified_relation(session, hypothesis, scope, company)
        for kind in ("event", "operating", "market", "peer_control"):
            _verified_impact_observation(session, relation, kind)
    session.commit()

    selects = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    engine = session.get_bind()
    sa.event.listen(engine, "before_cursor_execute", count_selects)
    try:
        EventImpactResearchService(session).classify(research_case.id)
    finally:
        sa.event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_classify_fund_coverage_queries_do_not_scale_per_relation(
    session, research_service
) -> None:
    one_relation_case = research_service.add_case(
        title="one relation", industry_topic="impact", created_by="tester"
    )
    many_relations_case = research_service.add_case(
        title="many relations", industry_topic="impact", created_by="tester"
    )

    one_relation_selects = _classification_select_count(
        session, one_relation_case, relation_count=1
    )
    many_relation_selects = _classification_select_count(
        session, many_relations_case, relation_count=4
    )

    assert many_relation_selects <= one_relation_selects + 2


def test_classify_keeps_fund_coverage_with_its_own_hypothesis(
    session, research_case
) -> None:
    scope = _event_scope(session, research_case, factors=["covered", "uncovered"])
    covered_company = _company(session, code="FUND-COVERED", company_type="listed")
    uncovered_company = _company(session, code="FUND-UNCOVERED", company_type="unlisted")
    covered_hypothesis = _hypothesis(
        session, research_case.id, scope.id, statement="covered", rank=1
    )
    uncovered_hypothesis = _hypothesis(
        session, research_case.id, scope.id, statement="uncovered", rank=2
    )
    covered_relation = _verified_relation(
        session, covered_hypothesis, scope, covered_company
    )
    _verified_relation(session, uncovered_hypothesis, scope, uncovered_company)
    stock = Stock(
        company_id=covered_company.id,
        code="600188.SH",
        name="Covered stock",
        market="SSE",
        created_at=NOW,
    )
    fund = Fund(
        code="000188",
        name="Covered fund",
        fund_type="equity",
        scale=None,
        establish_date=None,
        management_company_id=None,
        created_at=NOW,
    )
    session.add_all([stock, fund])
    session.flush()
    session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=Decimal("0.08"),
            report_period=date(2026, 7, 31),
            published_at=NOW,
            acquired_at=NOW,
            source="fixture holding",
            created_at=NOW,
        )
    )
    _verified_impact_observation(session, covered_relation, "event")
    session.commit()

    assessments = EventImpactResearchService(session).classify(research_case.id)

    components = {assessment.hypothesis_id: assessment.score_components for assessment in assessments}
    assert components[covered_hypothesis.id]["fund_coverage"] == 1
    assert components[uncovered_hypothesis.id]["fund_coverage"] == 0


def test_conclusion_uses_latest_assessment_not_a_superseded_key(
    session, research_case
) -> None:
    factor = "重新评估的传导"
    scope = _event_scope(session, research_case, factors=[factor])
    hypothesis = _hypothesis(session, research_case.id, scope.id, statement=factor)
    session.add(
        EventImpactHypothesisAssessment(
            hypothesis_id=hypothesis.id,
            research_case_id=research_case.id,
            scope_version_id=scope.id,
            classification="key",
            rank=1,
            score_components={"event": 1, "company": 1, "operating": 1, "market": 1, "peer_control": 1, "fund_coverage": 1},
            explanation="earlier complete evidence",
            created_at=NOW,
        )
    )
    session.flush()
    session.add(
        EventImpactHypothesisAssessment(
            hypothesis_id=hypothesis.id,
            research_case_id=research_case.id,
            scope_version_id=scope.id,
            classification="alternative",
            rank=1,
            score_components={"event": 1, "company": 1, "operating": 0, "market": 1, "peer_control": 0, "fund_coverage": 0},
            explanation="new evidence no longer clears the key threshold",
            created_at=NOW + timedelta(seconds=1),
        )
    )
    session.commit()
    _formal_scope_coverage(session, research_case, scope, factor)

    with pytest.raises(ValidationFailedError, match="impact coverage is insufficient"):
        EventConclusionService(session).create_draft(research_case.id)


def test_no_current_scope_key_marks_lifecycle_exhausted_without_draft(
    session, research_case
) -> None:
    _event_scope(session, research_case, factors=["没有候选"])
    lifecycle = EventResearchLifecycle(
        research_case_id=research_case.id,
        status="researching",
        active_run_id=None,
        current_round=1,
        status_summary="研究中",
        current_gap=None,
        next_human_action=None,
        updated_at=NOW,
    )
    session.add(lifecycle)
    session.commit()

    assert EventImpactResearchService(session).classify(research_case.id) == []
    session.commit()

    session.refresh(lifecycle)
    assert lifecycle.status == "exhausted"
    assert lifecycle.current_gap is not None
    assert lifecycle.current_gap.startswith("不能确定关键因素")
    assert EventConclusionService(session).latest(research_case.id) is None


def test_impact_assessments_are_append_only(session, research_case) -> None:
    scope = _event_scope(session, research_case, factors=["immutable assessment"])
    hypothesis = _hypothesis(
        session, research_case.id, scope.id, statement="immutable assessment"
    )
    assessment = EventImpactHypothesisAssessment(
        hypothesis_id=hypothesis.id,
        research_case_id=research_case.id,
        scope_version_id=scope.id,
        classification="background",
        rank=1,
        score_components={"event": 0},
        explanation="fixture assessment",
        created_at=NOW,
    )
    session.add(assessment)
    session.commit()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(EventImpactHypothesisAssessment)
            .where(EventImpactHypothesisAssessment.id == assessment.id)
            .values(rank=2)
        )


@dataclass
class _FakeImpactResolver:
    results_by_factor: dict[str, list[ResolvedImpactCompany]]

    def resolve(self, *, factor_statement: str, statements):
        return self.results_by_factor.get(factor_statement, [])


def _event_scope(
    session, research_case, *, factors: list[str], version: int = 1
) -> EventResearchScopeVersion:
    session.add(
        EventResearchBrief(
            research_case_id=research_case.id,
            raw_input="event fixture",
            source_url="https://investor.tsmc.com/event",
            event_title="Event fixture",
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            research_question="What is the transmission path?",
            extraction_state="human_confirmed",
            created_at=NOW,
        )
    )
    scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=version,
        changed_by="tester",
        change_summary="fixture event scope",
        created_at=NOW,
    )
    session.add(scope)
    session.flush()
    session.add_all(
        EventResearchScopeFactor(
            scope_version_id=scope.id,
            statement=factor,
            description=None,
            position=position,
        )
        for position, factor in enumerate(factors, start=1)
    )
    session.commit()
    return scope


def _case_statement(
    session,
    research_case,
    *,
    source_url: str = "https://investor.tsmc.com/releases/q2",
    observed_period: date | None = date(2026, 8, 7),
) -> SourceStatement:
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url=source_url,
        available_at=NOW,
        acquired_at=NOW,
        parser_version="html-v1",
        parse_state="success",
    )
    thesis = Thesis(
        research_case_id=research_case.id,
        statement="fixture evidence thesis",
        created_by="tester",
        created_at=NOW,
    )
    session.add_all([document, thesis])
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=research_case.id,
            document_version_id=document.id,
            linked_at=NOW,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="Supplier confirmed additional orders.",
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="Supplier confirmed additional orders.",
        observed_period=observed_period,
        created_at=NOW,
    )
    session.add(statement)
    session.flush()
    session.add(
        EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason="fixture evidence",
            scope={},
            available_at=NOW,
            creator_type="human",
            review_state="reviewed",
            created_at=NOW,
        )
    )
    session.commit()
    return statement


def _candidate(
    *,
    company_name: str = "Acme Supplier",
    company_type: str = "listed",
    source_statement_id: uuid.UUID | None,
) -> ResolvedImpactCompany:
    return ResolvedImpactCompany(
        company_name=company_name,
        type=company_type,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        source_statement_id=source_statement_id,
    )


def _listed_relation_with_stock(session, research_case):
    scope = _scope(session, research_case)
    company = _company(session, code="ASHARE-CO", company_type="listed")
    stock = Stock(
        company_id=company.id,
        code="600001.SH",
        name="A-share Co",
        market="SSE",
        created_at=NOW,
    )
    session.add(stock)
    session.commit()
    hypothesis = _hypothesis(session, research_case.id, scope.id)
    relation = _relation(session, hypothesis, scope, company)
    return relation, company, stock


def _snapshot(session, stock, *, metric_name: str, value: str = "1"):
    snapshot = ValuationSnapshot(
        stock_id=stock.id,
        as_of_date=date(2026, 8, 8),
        metric_name=metric_name,
        metric_value=Decimal(value),
        source="ledger-fixture",
        definition=f"fixture {metric_name}",
        created_at=NOW,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def test_collect_data_uses_ledger_operating_market_and_peer_evidence(
    session, research_case
) -> None:
    relation, _company_row, stock = _listed_relation_with_stock(session, research_case)
    _snapshot(session, stock, metric_name="REVENUE_YOY")
    _snapshot(session, stock, metric_name="EVENT_RETURN_1D")
    _snapshot(session, stock, metric_name="PEER_RETURN_1D")

    EventImpactResearchService(
        session, market_data=LedgerChinaMarketData(session)
    ).collect_data(relation.id, as_of=date(2026, 8, 8))
    session.commit()

    statuses = {
        row.kind: row.status
        for row in session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id
            )
        )
    }
    assert statuses == {
        "operating": "verified",
        "market": "verified",
        "peer_control": "verified",
    }


def test_collect_data_keeps_unlisted_relation_outside_stock_and_fund_layers(
    session, research_case
) -> None:
    scope = _scope(session, research_case)
    company = _company(session, code="PRIVATE-ODM", company_type="unlisted_supplier")
    hypothesis = _hypothesis(session, research_case.id, scope.id)
    relation = _relation(session, hypothesis, scope, company)
    session.add(
        CompanyImpactObservation(
            relation_id=relation.id,
            kind="relation",
            status="verified",
            source_statement_id=None,
            valuation_snapshot_id=None,
            summary="source-backed supplier relation",
            as_of_date=date(2026, 8, 8),
            created_at=NOW,
        )
    )
    session.commit()

    EventImpactResearchService(
        session, market_data=LedgerChinaMarketData(session)
    ).collect_data(relation.id, as_of=date(2026, 8, 8))
    session.commit()

    assert {
        row.kind
        for row in session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id
            )
        )
    } == {"relation", "operating"}


def test_collect_data_without_ledger_metrics_records_insufficient_not_fabricated(
    session, research_case
) -> None:
    relation, _company_row, _stock = _listed_relation_with_stock(session, research_case)

    result = EventImpactResearchService(session).collect_data(
        relation.id, as_of=date(2026, 8, 8)
    )
    session.commit()

    observations = list(
        session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id
            )
        )
    )
    assert result.insufficient_kinds == ("operating", "market", "peer_control")
    assert {(row.kind, row.status, row.valuation_snapshot_id) for row in observations} == {
        ("operating", "insufficient", None),
        ("market", "insufficient", None),
        ("peer_control", "insufficient", None),
    }

    snapshot = _snapshot(session, _stock, metric_name="REVENUE_YOY")
    EventImpactResearchService(session).collect_data(relation.id, as_of=date(2026, 8, 8))
    session.commit()
    operating = list(
        session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id,
                CompanyImpactObservation.kind == "operating",
            )
        )
    )
    assert {(row.status, row.valuation_snapshot_id) for row in operating} == {
        ("insufficient", None),
        ("verified", snapshot.id),
    }


def test_collect_data_appends_one_auditable_observation_per_new_metric_snapshot(
    session, research_case
) -> None:
    relation, _company_row, stock = _listed_relation_with_stock(session, research_case)
    revenue = _snapshot(session, stock, metric_name="REVENUE_YOY")
    margin = _snapshot(session, stock, metric_name="GROSS_MARGIN")

    service = EventImpactResearchService(session)
    service.collect_data(relation.id, as_of=date(2026, 8, 8))
    service.collect_data(relation.id, as_of=date(2026, 8, 8))
    session.commit()
    operating = list(
        session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id,
                CompanyImpactObservation.kind == "operating",
                CompanyImpactObservation.status == "verified",
            )
        )
    )
    assert {row.valuation_snapshot_id for row in operating} == {revenue.id, margin.id}

    guidance = _snapshot(session, stock, metric_name="ORDER_GUIDANCE")
    service.collect_data(relation.id, as_of=date(2026, 8, 8))
    session.commit()
    assert {
        row.valuation_snapshot_id
        for row in session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id,
                CompanyImpactObservation.kind == "operating",
                CompanyImpactObservation.status == "verified",
            )
        )
    } == {revenue.id, margin.id, guidance.id}


def test_collect_data_excludes_foreign_listing_of_the_same_company(
    session, research_case
) -> None:
    relation, company, _china_stock = _listed_relation_with_stock(session, research_case)
    foreign_stock = Stock(
        company_id=company.id,
        code="000660.KS",
        name="Foreign listing",
        market="KRX",
        created_at=NOW,
    )
    session.add(foreign_stock)
    session.commit()
    _snapshot(session, foreign_stock, metric_name="REVENUE_YOY")
    _snapshot(session, foreign_stock, metric_name="EVENT_RETURN_1D")
    _snapshot(session, foreign_stock, metric_name="PEER_RETURN_1D")

    EventImpactResearchService(session).collect_data(
        relation.id, as_of=date(2026, 8, 8)
    )
    session.commit()

    assert {
        (row.kind, row.status)
        for row in session.scalars(
            select(CompanyImpactObservation).where(
                CompanyImpactObservation.relation_id == relation.id
            )
        )
    } == {
        ("operating", "insufficient"),
        ("market", "insufficient"),
        ("peer_control", "insufficient"),
    }


def test_fund_exposure_marks_partial_visible_holdings_not_computable(
    session, research_case
) -> None:
    relation, company, first_stock = _listed_relation_with_stock(session, research_case)
    second_stock = Stock(
        company_id=company.id,
        code="600002.SH",
        name="A-share Co second listing",
        market="SSE",
        created_at=NOW,
    )
    session.add(second_stock)
    session.flush()
    verified_relation = CompanyImpactRelation(
        hypothesis_id=relation.hypothesis_id,
        scope_version_id=relation.scope_version_id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="verified fixture",
        status="verified",
        source_statement_id=None,
        created_at=NOW,
    )
    fund = Fund(
        code="000001",
        name="Partial holding fund",
        fund_type="equity",
        management_company_id=None,
        scale=None,
        establish_date=None,
        created_at=NOW,
    )
    session.add_all([verified_relation, fund])
    session.flush()
    session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=first_stock.id,
            weight=Decimal("3.5"),
            report_period=date(2026, 6, 30),
            published_at=NOW,
            acquired_at=NOW,
            source="fund-report-fixture",
            created_at=NOW,
        )
    )
    session.commit()

    coverage = EventImpactResearchService(session).fund_exposure(
        verified_relation.id, as_of=date(2026, 8, 8)
    )

    assert len(coverage) == 1
    assert coverage[0].computable is False
    assert coverage[0].exposure is None
    assert coverage[0].coverage_status == "partial"
    assert coverage[0].coverage_ratio == Decimal("0.5")


def test_fund_exposure_excludes_non_china_fund_disclosures(
    session, research_case
) -> None:
    relation, company, stock = _listed_relation_with_stock(session, research_case)
    verified_relation = CompanyImpactRelation(
        hypothesis_id=relation.hypothesis_id,
        scope_version_id=relation.scope_version_id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="verified fixture",
        status="verified",
        source_statement_id=None,
        created_at=NOW,
    )
    china_fund = Fund(
        code="000002",
        name="China public fund",
        fund_type="equity",
        management_company_id=None,
        scale=None,
        establish_date=None,
        created_at=NOW,
    )
    foreign_fund = Fund(
        code="HK-ETF-001",
        name="Foreign fund",
        fund_type="equity",
        management_company_id=None,
        scale=None,
        establish_date=None,
        created_at=NOW,
    )
    session.add_all([verified_relation, china_fund, foreign_fund])
    session.flush()
    for fund in (china_fund, foreign_fund):
        session.add(
            HoldingDisclosure(
                fund_id=fund.id,
                stock_id=stock.id,
                weight=Decimal("2.5"),
                report_period=date(2026, 6, 30),
                published_at=NOW,
                acquired_at=NOW,
                source="fund-report-fixture",
                created_at=NOW,
            )
        )
    session.commit()

    coverage = EventImpactResearchService(session).fund_exposure(
        verified_relation.id, as_of=date(2026, 8, 8)
    )

    assert [item.fund_id for item in coverage] == [china_fund.id]


def test_refresh_appends_current_scope_candidate_relation_from_admissible_case_source(
    session, research_case
) -> None:
    factor = "capital expenditure raises supplier orders"
    scope = _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    service = EventImpactResearchService(
        session,
        _FakeImpactResolver({factor: [_candidate(source_statement_id=statement.id)]}),
    )

    result = service.refresh(research_case.id)
    session.commit()

    hypothesis = session.scalar(select(EventImpactHypothesis))
    relation = session.scalar(select(CompanyImpactRelation))
    observations = list(
        session.scalars(
            select(CompanyImpactObservation).order_by(CompanyImpactObservation.kind)
        )
    )
    assert result.hypotheses_created == 1
    assert result.relations_created == 1
    assert hypothesis is not None and hypothesis.scope_version_id == scope.id
    assert hypothesis.research_case_id == research_case.id
    assert hypothesis.classification == "candidate"
    assert hypothesis.rank == 1
    assert hypothesis.score_components == {
        "refresh_key": f"scope:{scope.id}:initial"
    }
    assert relation is not None and relation.source_statement_id == statement.id
    assert relation.scope_version_id == scope.id
    assert {
        (observation.kind, observation.status, observation.source_statement_id, observation.as_of_date)
        for observation in observations
    } == {
        ("event", "verified", statement.id, date(2026, 8, 7)),
        ("relation", "verified", statement.id, date(2026, 8, 7)),
    }


def test_source_backed_refresh_can_become_key_after_verified_relation_and_ledger_data(
    session, research_case
) -> None:
    factor = "source-backed supplier impact"
    scope = _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    service = EventImpactResearchService(
        session,
        _FakeImpactResolver({factor: [_candidate(source_statement_id=statement.id)]}),
    )

    service.refresh(research_case.id)
    session.commit()
    hypothesis = session.scalar(select(EventImpactHypothesis))
    candidate_relation = session.scalar(select(CompanyImpactRelation))
    assert hypothesis is not None and candidate_relation is not None
    verified_relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=scope.id,
        affected_company_id=candidate_relation.affected_company_id,
        relation_kind=candidate_relation.relation_kind,
        direction=candidate_relation.direction,
        mechanism=candidate_relation.mechanism,
        status="verified",
        source_statement_id=statement.id,
        created_at=NOW,
    )
    stock = Stock(
        company_id=candidate_relation.affected_company_id,
        code="600099.SH",
        name="Source-backed supplier",
        market="SSE",
        created_at=NOW,
    )
    fund = Fund(
        code="000099",
        name="China fixture fund",
        fund_type="equity",
        scale=None,
        establish_date=None,
        management_company_id=None,
        created_at=NOW,
    )
    session.add_all([verified_relation, stock, fund])
    session.flush()
    for metric_name in ("REVENUE_YOY", "EVENT_RETURN_1D", "PEER_RETURN_1D"):
        _snapshot(session, stock, metric_name=metric_name)
    session.add(
        HoldingDisclosure(
            fund_id=fund.id,
            stock_id=stock.id,
            weight=Decimal("0.10"),
            report_period=date(2026, 7, 31),
            published_at=NOW,
            acquired_at=NOW,
            source="fixture holding",
            created_at=NOW,
        )
    )
    session.commit()

    service.collect_data(verified_relation.id, as_of=date(2026, 8, 8))
    assessment = service.classify(research_case.id)[0]

    assert assessment.classification == "key"
    assert assessment.score_components == {
        "event": 1,
        "company": 1,
        "operating": 1,
        "market": 1,
        "peer_control": 1,
        "fund_coverage": 1,
    }


def test_refresh_calls_provider_without_a_database_transaction(
    session, research_case
) -> None:
    factor = "provider transaction boundary"
    scope = _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    case_id, scope_id, statement_id = research_case.id, scope.id, statement.id
    session.rollback()
    transaction_states: list[bool] = []

    class TransactionCheckingResolver:
        def resolve(self, *, factor_statement, statements):
            transaction_states.append(session.in_transaction())
            # Source values are detached snapshots, so accessing them cannot
            # auto-begin a transaction in the provider callback.
            assert statements[0].id == statement_id
            assert statements[0].normalized_text
            assert not session.in_transaction()
            return []

    EventImpactResearchService(session, TransactionCheckingResolver()).refresh(
        case_id, scope_version_id=scope_id
    )
    session.commit()

    assert transaction_states == [False]


def test_refresh_provider_failure_never_commits_the_callers_outer_transaction(
    session, research_case
) -> None:
    factor = "outer transaction boundary"
    scope = _event_scope(session, research_case, factors=[factor])
    case_id, scope_id = research_case.id, scope.id
    session.rollback()
    staged_title = "must remain uncommitted"
    session.add(
        ResearchCase(
            title=staged_title,
            industry_topic="staged",
            created_by="tester",
            created_at=NOW,
        )
    )

    class FailingResolver:
        def resolve(self, *, factor_statement, statements):
            raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        EventImpactResearchService(session, FailingResolver()).refresh(
            case_id, scope_version_id=scope_id
        )
    session.rollback()

    assert session.scalar(
        select(ResearchCase).where(ResearchCase.title == staged_title)
    ) is None


def test_refresh_keeps_unlisted_supplier_without_creating_stock(session, research_case) -> None:
    factor = "domestic supplier benefits"
    _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)

    unlisted_candidate = ResolvedImpactCompany(
        company_name="Private ODM",
        type="unlisted_supplier",
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        source_statement_id=statement.id,
    )
    EventImpactResearchService(
        session,
        _FakeImpactResolver(
            {
                factor: [unlisted_candidate]
            }
        ),
    ).refresh(research_case.id)
    session.commit()

    company = session.scalar(select(Company).where(Company.name == "Private ODM"))
    assert company is not None and company.type == "unlisted_supplier"
    assert list(session.scalars(select(Stock).where(Stock.company_id == company.id))) == []


def test_refresh_rejects_foreign_and_invalid_source_statement_ids(
    session, research_case, research_service
) -> None:
    factor = "supplier impact"
    _event_scope(session, research_case, factors=[factor])
    foreign_case = research_service.add_case(
        title="foreign", industry_topic="other", created_by="tester"
    )
    foreign_statement = _case_statement(session, foreign_case)
    invalid_statement = _case_statement(
        session, research_case, source_url="https://example.com/invalid"
    )
    company_ids_before = set(session.scalars(select(Company.id)))

    result = EventImpactResearchService(
        session,
        _FakeImpactResolver(
            {
                factor: [
                    _candidate(
                        company_name="Foreign Co",
                        source_statement_id=foreign_statement.id,
                    ),
                    _candidate(
                        company_name="Invalid Co",
                        source_statement_id=invalid_statement.id,
                    ),
                ]
            }
        ),
    ).refresh(research_case.id)
    session.commit()

    assert result.source_rejected_count == 2
    assert result.relations_created == 0
    assert set(session.scalars(select(Company.id))) == company_ids_before
    assert list(session.scalars(select(CompanyImpactRelation))) == []
    assert list(session.scalars(select(CompanyImpactObservation))) == []


def test_refresh_keeps_sourceless_candidate_unresolved_without_observation(
    session, research_case
) -> None:
    factor = "supplier impact"
    scope = _event_scope(session, research_case, factors=[factor])

    result = EventImpactResearchService(
        session,
        _FakeImpactResolver({factor: [_candidate(source_statement_id=None)]}),
    ).refresh(research_case.id)
    session.commit()

    hypotheses = list(session.scalars(select(EventImpactHypothesis)))
    unresolved = next(
        row for row in hypotheses if row.classification == "unresolved"
    )
    factor_candidate = next(
        row for row in hypotheses if row.classification == "candidate"
    )
    assert result.unresolved_candidate_count == 1
    assert result.hypotheses_created == 2
    assert result.relations_created == 0
    assert unresolved.research_case_id == research_case.id
    assert unresolved.scope_version_id == scope.id
    assert "Acme Supplier" in unresolved.statement
    assert "supplier" in unresolved.statement
    assert unresolved.score_components == {
        "refresh_key": f"scope:{scope.id}:initial",
        "source": 0,
    }
    assert "source_statement_id is missing" in unresolved.explanation
    assert factor_candidate.statement == factor
    assert list(session.scalars(select(CompanyImpactObservation))) == []
    assert list(session.scalars(select(CompanyImpactRelation))) == []
    assert list(session.scalars(select(Company))) == []


def test_refresh_uses_requested_prior_scope_and_rejects_foreign_scope(
    session, research_case, research_service
) -> None:
    first_factor = "first factor"
    first_scope = _event_scope(session, research_case, factors=[first_factor])
    second_scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=2,
        changed_by="tester",
        change_summary="successor scope",
        created_at=NOW,
    )
    session.add(second_scope)
    session.flush()
    session.add(
        EventResearchScopeFactor(
            scope_version_id=second_scope.id,
            statement="second factor",
            description=None,
            position=1,
        )
    )
    session.commit()
    resolver = _FakeImpactResolver({first_factor: []})

    result = EventImpactResearchService(session, resolver).refresh(
        research_case.id, scope_version_id=first_scope.id
    )

    assert result.hypotheses_created == 1
    assert session.scalar(select(EventImpactHypothesis)).scope_version_id == first_scope.id
    other_case = research_service.add_case(
        title="other", industry_topic="other", created_by="tester"
    )
    other_scope = _event_scope(session, other_case, factors=["other factor"])
    with pytest.raises(ValidationFailedError, match="does not belong"):
        EventImpactResearchService(session, resolver).refresh(
            research_case.id, scope_version_id=other_scope.id
        )


def test_schedule_refresh_is_idempotent_for_one_exact_scope(session, research_case) -> None:
    scope = _event_scope(session, research_case, factors=["factor"])
    run = ResearchRun(
        research_case_id=research_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=1,
        budget=1,
        budget_used=0,
        scope_thesis_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(run)
    session.commit()
    service = EventImpactResearchService(session)

    service.schedule_refresh(research_case.id, scope.id, run.id)
    service.schedule_refresh(research_case.id, scope.id, run.id)
    session.commit()

    assert len(list(session.scalars(select(EventImpactRefreshClaim)))) == 1
    tasks = list(session.scalars(select(ResearchTask)))
    assert len(tasks) == 1
    assert tasks[0].task_type == "impact_refresh"
    assert str(scope.id) in tasks[0].query


@pytest.mark.pg_only
def test_postgres_concurrent_refresh_schedule_creates_one_claim_and_task(
    engine, monkeypatch
) -> None:
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        case = ResearchCase(
            title="impact schedule race",
            industry_topic="event",
            created_by="tester",
            created_at=NOW,
        )
        bootstrap.add(case)
        bootstrap.flush()
        bootstrap.add(
            EventResearchBrief(
                research_case_id=case.id,
                raw_input="fixture",
                source_url=None,
                event_title="fixture",
                company_name=None,
                ticker=None,
                event_at=None,
                market_reaction=None,
                research_question="fixture",
                extraction_state="human_confirmed",
                created_at=NOW,
            )
        )
        scope = EventResearchScopeVersion(
            research_case_id=case.id,
            version=1,
            changed_by="tester",
            change_summary="fixture",
            created_at=NOW,
        )
        run = ResearchRun(
            research_case_id=case.id,
            status="queued",
            stage="planning",
            round=0,
            max_rounds=1,
            budget=1,
            budget_used=0,
            scope_thesis_ids=[],
            created_at=NOW,
            updated_at=NOW,
        )
        bootstrap.add_all([scope, run])
        bootstrap.flush()
        bootstrap.add(
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement="concurrent factor",
                description=None,
                position=1,
            )
        )
        bootstrap.commit()
        case_id, scope_id, run_id = case.id, scope.id, run.id
    finally:
        bootstrap.close()

    barrier = Barrier(2)
    claim_insert_barrier = Barrier(2)
    monkeypatch.setattr(
        "app.services.event_impact._before_refresh_claim_insert",
        lambda: claim_insert_barrier.wait(timeout=5),
    )
    errors: list[BaseException] = []

    def schedule() -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            EventImpactResearchService(db).schedule_refresh(case_id, scope_id, run_id)
            db.commit()
        except BaseException as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first, second = Thread(target=schedule), Thread(target=schedule)
    first.start(); second.start()
    first.join(timeout=10); second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    verify = SessionLocal()
    try:
        assert verify.scalar(select(sa.func.count()).select_from(EventImpactRefreshClaim)) == 1
        assert verify.scalar(select(sa.func.count()).select_from(ResearchTask)) == 1
    finally:
        verify.close()

    refresh_barrier = Barrier(2)
    provider_barrier = Barrier(2)
    monkeypatch.setattr(
        "app.services.event_impact._before_refresh_claim_lock",
        lambda: refresh_barrier.wait(timeout=5),
    )
    refresh_errors: list[BaseException] = []

    def refresh() -> None:
        db = SessionLocal()
        try:
            class ConcurrentResolver:
                def resolve(self, *, factor_statement, statements):
                    assert not db.in_transaction()
                    provider_barrier.wait(timeout=5)
                    return []

            EventImpactResearchService(db, ConcurrentResolver()).refresh(
                case_id, scope_version_id=scope_id
            )
            db.commit()
        except BaseException as exc:
            refresh_errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first, second = Thread(target=refresh), Thread(target=refresh)
    first.start(); second.start()
    first.join(timeout=10); second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert refresh_errors == []
    verify = SessionLocal()
    try:
        assert verify.scalar(select(sa.func.count()).select_from(EventImpactHypothesis)) == 1
    finally:
        verify.close()


@pytest.mark.pg_only
def test_postgres_concurrent_cases_reuse_one_canonical_company_without_leaks(
    engine, monkeypatch
) -> None:
    """The unique canonical key + savepoint recovery handles two case writers."""
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        case_ids: list[uuid.UUID] = []
        scope_ids: list[uuid.UUID] = []
        statement_ids: list[uuid.UUID] = []
        for number in (1, 2):
            case = ResearchCase(
                title=f"company race {number}",
                industry_topic="event",
                created_by="tester",
                created_at=NOW,
            )
            bootstrap.add(case)
            bootstrap.commit()
            scope = _event_scope(
                bootstrap, case, factors=[f"company race factor {number}"]
            )
            statement = _case_statement(bootstrap, case)
            case_ids.append(case.id)
            scope_ids.append(scope.id)
            statement_ids.append(statement.id)
    finally:
        bootstrap.close()

    before_insert = Barrier(2)
    monkeypatch.setattr(
        "app.services.event_impact._before_company_insert",
        lambda: before_insert.wait(timeout=5),
    )
    errors: list[BaseException] = []

    def refresh(index: int) -> None:
        db = SessionLocal()
        try:
            factor = f"company race factor {index + 1}"
            resolver = _FakeImpactResolver(
                {
                    factor: [
                        _candidate(
                            company_name=(
                                "Ａｃｍｅ　Supplier" if index == 0 else "acme supplier"
                            ),
                            source_statement_id=statement_ids[index],
                        )
                    ]
                }
            )
            EventImpactResearchService(db, resolver).refresh(
                case_ids[index], scope_version_id=scope_ids[index]
            )
            db.commit()
        except BaseException as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first, second = Thread(target=refresh, args=(0,)), Thread(target=refresh, args=(1,))
    first.start(); second.start()
    first.join(timeout=15); second.join(timeout=15)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []

    verify = SessionLocal()
    try:
        assert verify.scalar(select(sa.func.count()).select_from(Company)) == 1
        for case_id, scope_id in zip(case_ids, scope_ids):
            assert verify.scalar(
                select(sa.func.count()).select_from(EventImpactHypothesis).where(
                    EventImpactHypothesis.research_case_id == case_id,
                    EventImpactHypothesis.scope_version_id == scope_id,
                )
            ) == 1
            assert verify.scalar(
                select(sa.func.count()).select_from(CompanyImpactRelation)
                .join(EventImpactHypothesis)
                .where(
                    EventImpactHypothesis.research_case_id == case_id,
                    CompanyImpactRelation.scope_version_id == scope_id,
                )
            ) == 1
    finally:
        verify.close()


@pytest.mark.pg_only
def test_postgres_concurrent_collect_data_appends_each_snapshot_once(
    engine, monkeypatch
) -> None:
    SessionLocal = sessionmaker(bind=engine, future=True)
    bootstrap = SessionLocal()
    try:
        case = ResearchCase(
            title="impact data race",
            industry_topic="event",
            created_by="tester",
            created_at=NOW,
        )
        bootstrap.add(case)
        bootstrap.commit()
        relation, _company_row, stock = _listed_relation_with_stock(bootstrap, case)
        snapshot = _snapshot(bootstrap, stock, metric_name="REVENUE_YOY")
        relation_id, snapshot_id = relation.id, snapshot.id
    finally:
        bootstrap.close()

    start_barrier = Barrier(2)
    monkeypatch.setattr(
        "app.services.event_impact._before_impact_data_relation_lock",
        lambda: start_barrier.wait(timeout=5),
    )
    errors: list[BaseException] = []

    def collect() -> None:
        db = SessionLocal()
        try:
            EventImpactResearchService(db).collect_data(
                relation_id, as_of=date(2026, 8, 8)
            )
            db.commit()
        except BaseException as exc:
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first, second = Thread(target=collect), Thread(target=collect)
    first.start(); second.start()
    first.join(timeout=10); second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []

    verify = SessionLocal()
    try:
        observations = list(
            verify.scalars(
                select(CompanyImpactObservation).where(
                    CompanyImpactObservation.relation_id == relation_id
                )
            )
        )
        assert [row.valuation_snapshot_id for row in observations if row.status == "verified"] == [snapshot_id]
        assert len(observations) == 3
    finally:
        verify.close()


def test_refresh_retry_is_idempotent_for_scope_initial_key(session, research_case) -> None:
    factor = "supplier impact"
    scope = _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    service = EventImpactResearchService(
        session,
        _FakeImpactResolver({factor: [_candidate(source_statement_id=statement.id)]}),
    )

    first = service.refresh(research_case.id, scope_version_id=scope.id)
    second = service.refresh(research_case.id, scope_version_id=scope.id)
    session.commit()

    assert first.hypotheses_created == 1
    assert second.hypotheses_created == 0
    assert second.relations_created == 0
    assert len(list(session.scalars(select(EventImpactHypothesis)))) == 1
    assert len(list(session.scalars(select(CompanyImpactRelation)))) == 1
    assert {
        observation.kind
        for observation in session.scalars(select(CompanyImpactObservation))
    } == {"event", "relation"}


def test_refresh_reuses_canonical_company_identity(session, research_case) -> None:
    factor = "supplier impact"
    _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    existing = Company(
        code="ACME SUPPLIER",
        name="ACME SUPPLIER",
        type="listed",
        created_at=NOW,
    )
    session.add(existing)
    session.commit()

    EventImpactResearchService(
        session,
        _FakeImpactResolver(
            {
                factor: [
                    _candidate(
                        company_name="Ａｃｍｅ　Supplier",
                        source_statement_id=statement.id,
                    ),
                    _candidate(
                        company_name="acme supplier",
                        source_statement_id=statement.id,
                    ),
                ]
            }
        ),
    ).refresh(research_case.id)
    session.commit()

    companies = list(session.scalars(select(Company)))
    relations = list(session.scalars(select(CompanyImpactRelation)))
    assert [company.id for company in companies] == [existing.id]
    assert {relation.affected_company_id for relation in relations} == {existing.id}


@pytest.mark.parametrize(
    ("legacy_name", "candidate_name"),
    [
        ("ACME SUPPLIER", "acme supplier"),
        ("Ａｃｍｅ　Supplier", "Ａｃｍｅ　Supplier"),
    ],
)
def test_refresh_uses_persisted_legacy_company_identity_alias(
    session, research_case, legacy_name, candidate_name
) -> None:
    factor = "supplier impact"
    _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    legacy_id = uuid.uuid4()
    session.execute(
        Company.__table__.insert().values(
            id=legacy_id,
            code=legacy_name,
            name=legacy_name,
            type="listed",
            canonical_identity=None,
            created_at=NOW,
        )
    )
    identity = "acme supplier"
    session.add(
        CompanyIdentityAlias(
            company_id=legacy_id,
            company_type="listed",
            canonical_identity=identity,
            created_at=NOW,
        )
    )
    session.commit()

    EventImpactResearchService(
        session,
        _FakeImpactResolver(
            {
                factor: [
                    _candidate(
                        company_name=candidate_name,
                        source_statement_id=statement.id,
                    )
                ]
            }
        ),
    ).refresh(research_case.id)
    session.commit()

    assert [company.id for company in session.scalars(select(Company))] == [legacy_id]
    alias = session.scalar(
        select(CompanyIdentityAlias).where(CompanyIdentityAlias.company_id == legacy_id)
    )
    assert alias is not None and alias.canonical_identity == identity


def test_refresh_company_lookup_never_scans_null_legacy_rows(session, research_case) -> None:
    factor = "supplier impact"
    _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    seen: list[str] = []

    def record_sql(_conn, _cursor, sql, _parameters, _context, _executemany):
        if "companies" in sql:
            seen.append(sql)

    sa.event.listen(session.bind, "before_cursor_execute", record_sql)
    try:
        EventImpactResearchService(
            session,
            _FakeImpactResolver(
                {factor: [_candidate(source_statement_id=statement.id)]}
            ),
        ).refresh(research_case.id)
        session.commit()
    finally:
        sa.event.remove(session.bind, "before_cursor_execute", record_sql)

    company_sql = "\n".join(seen).lower()
    assert "canonical_identity is null" not in company_sql
    assert "lower(trim(companies.name))" not in company_sql


def test_impact_refresh_task_executes_with_injected_resolver(session, research_case) -> None:
    factor = "supplier impact"
    scope = _event_scope(session, research_case, factors=[factor])
    statement = _case_statement(session, research_case)
    resolver = _FakeImpactResolver(
        {factor: [_candidate(source_statement_id=statement.id)]}
    )
    worker = AutoResearchService(session, impact_resolver=resolver)
    run = worker.start(
        research_case.id,
        max_rounds=1,
        budget=10,
        thesis_ids=[],
        scope_version_id=scope.id,
    )

    worker.execute(run)
    session.commit()

    task = next(
        task for task in worker.repo.tasks_for_run(run.id)
        if task.task_type == "impact_refresh"
    )
    assert task.status == "done"
    assert task.result is not None and task.result["relations_created"] == 1
    assert session.scalar(select(EventImpactHypothesis)).scope_version_id == scope.id


def test_impact_refresh_task_is_created_before_assessment_tasks(session, research_case) -> None:
    scope = _event_scope(session, research_case, factors=["supplier impact"])
    _case_statement(session, research_case)  # contributes a normal thesis task set

    run = AutoResearchService(session).start(
        research_case.id,
        max_rounds=1,
        budget=10,
        scope_version_id=scope.id,
    )

    tasks = AutoResearchRepository(session).tasks_for_run(run.id)
    assert tasks[0].task_type == "impact_refresh"
    assert any(task.task_type == "result" for task in tasks[1:])


def test_cancelled_old_scope_impact_task_cannot_write_before_successor_runs(
    session, research_case
) -> None:
    first_factor = "old supplier impact"
    first_scope = _event_scope(session, research_case, factors=[first_factor])
    statement = _case_statement(session, research_case)
    resolver = _FakeImpactResolver(
        {first_factor: [_candidate(source_statement_id=statement.id)]}
    )
    worker = AutoResearchService(session, impact_resolver=resolver)
    old_run = worker.start(
        research_case.id,
        max_rounds=1,
        budget=10,
        thesis_ids=[],
        scope_version_id=first_scope.id,
    )
    assert AutoResearchRepository(session).cancel_run(old_run)

    worker.execute(old_run)
    session.commit()

    assert list(session.scalars(select(EventImpactHypothesis))) == []
    assert all(
        task.status == "cancelled"
        for task in worker.repo.tasks_for_run(old_run.id)
        if task.task_type == "impact_refresh"
    )

    successor_scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=2,
        changed_by="tester",
        change_summary="successor",
        created_at=NOW,
    )
    session.add(successor_scope)
    session.flush()
    session.add(
        EventResearchScopeFactor(
            scope_version_id=successor_scope.id,
            statement="successor factor",
            description=None,
            position=1,
        )
    )
    session.commit()
    successor = worker.start(
        research_case.id,
        max_rounds=1,
        budget=10,
        thesis_ids=[],
        scope_version_id=successor_scope.id,
    )
    assert any(
        str(successor_scope.id) in task.query
        for task in worker.repo.tasks_for_run(successor.id)
        if task.task_type == "impact_refresh"
    )


def test_refresh_appends_new_scope_rows_without_mutating_prior_scope_rows(
    session, research_case
) -> None:
    first_factor = "first factor"
    first_scope = _event_scope(session, research_case, factors=[first_factor])
    statement = _case_statement(session, research_case)
    resolver = _FakeImpactResolver(
        {
            first_factor: [_candidate(source_statement_id=statement.id)],
            "second factor": [
                _candidate(company_name="Second Co", source_statement_id=statement.id)
            ],
        }
    )
    service = EventImpactResearchService(session, resolver)
    service.refresh(research_case.id)
    session.commit()
    first_hypothesis = session.scalar(
        select(EventImpactHypothesis).where(
            EventImpactHypothesis.scope_version_id == first_scope.id
        )
    )
    assert first_hypothesis is not None

    second_scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=2,
        changed_by="tester",
        change_summary="new scope",
        created_at=NOW,
    )
    session.add(second_scope)
    session.flush()
    session.add(
        EventResearchScopeFactor(
            scope_version_id=second_scope.id,
            statement="second factor",
            description=None,
            position=1,
        )
    )
    session.commit()

    service.refresh(research_case.id)
    session.commit()

    hypotheses = list(
        session.scalars(
            select(EventImpactHypothesis).order_by(
                EventImpactHypothesis.created_at, EventImpactHypothesis.id
            )
        )
    )
    assert [(row.scope_version_id, row.statement) for row in hypotheses] == [
        (first_scope.id, first_factor),
        (second_scope.id, "second factor"),
    ]
    assert first_hypothesis.classification == "candidate"
    assert first_hypothesis.scope_version_id == first_scope.id


def test_impact_relation_is_scope_bound_and_append_only(session, research_case) -> None:
    scope = _scope(session, research_case)
    company = _company(session, code="SUPPLIER-001", company_type="unlisted_supplier")
    hypothesis = _hypothesis(session, research_case.id, scope.id)

    relation = _relation(session, hypothesis, scope, company)

    assert relation.scope_version_id == scope.id
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(CompanyImpactRelation)
            .where(CompanyImpactRelation.id == relation.id)
            .values(status="verified")
        )


def test_relation_derives_hypothesis_scope_and_rejects_mismatch(session, research_case) -> None:
    scope = _scope(session, research_case)
    conflicting_scope = _scope(session, research_case, version=2)
    company = _company(session, code="SCOPE-COMPANY", company_type="unlisted_supplier")
    hypothesis = _hypothesis(session, research_case.id, scope.id)

    derived_relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        status="candidate",
        source_statement_id=None,
        created_at=NOW,
    )
    session.add(derived_relation)
    session.commit()

    assert derived_relation.scope_version_id == scope.id

    mismatched_relation = CompanyImpactRelation(
        hypothesis_id=hypothesis.id,
        scope_version_id=conflicting_scope.id,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        status="candidate",
        source_statement_id=None,
        created_at=NOW,
    )
    session.add(mismatched_relation)
    with pytest.raises(ValueError, match="scope_version_id must match"):
        session.commit()
    session.rollback()


def test_new_hypothesis_and_relation_commit_atomically(session, research_case) -> None:
    scope = _scope(session, research_case)
    company = _company(session, code="ATOMIC-COMPANY", company_type="unlisted_supplier")
    hypothesis = EventImpactHypothesis(
        research_case_id=research_case.id,
        scope_version_id=scope.id,
        statement="资本开支增加会提高中国供应商订单",
        classification="candidate",
        rank=1,
        score_components={"event_relevance": 0.5},
        explanation="等待验证",
        created_at=NOW,
    )
    relation = CompanyImpactRelation(
        hypothesis=hypothesis,
        affected_company_id=company.id,
        relation_kind="supplier",
        direction="benefits",
        mechanism="订单传导",
        status="candidate",
        source_statement_id=None,
        created_at=NOW,
    )

    session.add_all([hypothesis, relation])
    session.commit()

    assert relation.hypothesis_id == hypothesis.id
    assert relation.scope_version_id == scope.id


def test_hypothesis_rejects_scope_from_another_case(
    session, research_case, research_service
) -> None:
    other_case = research_service.add_case(
        title="Other case", industry_topic="other", created_by="tester"
    )
    other_scope = _scope(session, other_case)
    hypothesis = EventImpactHypothesis(
        research_case_id=research_case.id,
        scope_version_id=other_scope.id,
        statement="cross-case scope must not persist",
        classification="candidate",
        rank=1,
        score_components={},
        explanation="invalid ownership",
        created_at=NOW,
    )

    session.add(hypothesis)
    with pytest.raises(ValueError, match="scope_version_id must belong"):
        session.commit()
    session.rollback()


def test_unlisted_company_can_be_relation_target_without_stock(session, research_case) -> None:
    scope = _scope(session, research_case)
    company = _company(session, code="UNLISTED-ODM", company_type="unlisted_supplier")
    hypothesis = _hypothesis(session, research_case.id, scope.id)

    relation = _relation(session, hypothesis, scope, company)

    assert relation.affected_company_id == company.id
    assert list(session.scalars(select(Stock).where(Stock.company_id == company.id))) == []


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (
            EventImpactHypothesis,
            {
                "statement": "bad hypothesis",
                "classification": "invalid",
                "rank": 1,
                "score_components": {},
                "explanation": "bad value",
            },
        ),
        (
            CompanyImpactRelation,
            {
                "relation_kind": "invalid",
                "direction": "benefits",
                "mechanism": "bad value",
                "status": "candidate",
            },
        ),
        (
            CompanyImpactRelationReview,
            {
                "outcome": "invalid",
                "reason": "bad value",
                "reviewer": "tester",
            },
        ),
        (
            CompanyImpactObservation,
            {
                "kind": "invalid",
                "status": "candidate",
                "summary": "bad value",
            },
        ),
    ],
)
def test_impact_constraints_reject_invalid_values(
    session, research_case, model, kwargs
) -> None:
    scope = _scope(session, research_case)
    company = _company(session, code="CONSTRAINT-COMPANY", company_type="unlisted")
    hypothesis = _hypothesis(session, research_case.id, scope.id)
    relation = _relation(session, hypothesis, scope, company)

    if model is EventImpactHypothesis:
        row = model(
            research_case_id=research_case.id,
            scope_version_id=scope.id,
            created_at=NOW,
            **kwargs,
        )
    elif model is CompanyImpactRelation:
        row = model(
            hypothesis_id=hypothesis.id,
            scope_version_id=scope.id,
            affected_company_id=company.id,
            source_statement_id=None,
            created_at=NOW,
            **kwargs,
        )
    elif model is CompanyImpactRelationReview:
        row = model(relation_id=relation.id, created_at=NOW, **kwargs)
    else:
        row = model(
            relation_id=relation.id,
            source_statement_id=None,
            valuation_snapshot_id=None,
            as_of_date=None,
            created_at=NOW,
            **kwargs,
        )

    session.add(row)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_models_package_import_registers_impact_tables() -> None:
    assert models.event_impact is not None
    assert {
        "event_impact_hypotheses",
        "event_impact_hypothesis_assessments",
        "company_impact_relations",
        "company_impact_relation_reviews",
        "company_impact_observations",
    } <= set(models.Base.metadata.tables)


class _OperationsRecorder:
    def __init__(self, dialect: str) -> None:
        self._dialect = dialect
        self.tables: list[tuple] = []
        self.indexes: list[tuple] = []
        self.executed: list[str] = []
        self.dropped_indexes: list[tuple] = []
        self.dropped_tables: list[tuple] = []
        self.added_columns: list[tuple] = []
        self.dropped_columns: list[tuple] = []

    def get_bind(self):
        return SimpleNamespace(
            dialect=SimpleNamespace(name=self._dialect),
            execute=lambda _statement, _params=None: SimpleNamespace(mappings=lambda: []),
        )

    def add_column(self, *args) -> None:
        self.added_columns.append(args)

    def drop_column(self, *args) -> None:
        self.dropped_columns.append(args)

    def create_table(self, *args) -> None:
        self.tables.append(args)

    def create_index(self, *args, **kwargs) -> None:
        self.indexes.append(args + ((kwargs or None),))

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def drop_index(self, *args, **kwargs) -> None:
        self.dropped_indexes.append(args)

    def drop_table(self, *args, **kwargs) -> None:
        self.dropped_tables.append(args)


def _load_migration(path=MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location("event_impact_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(table_args: tuple) -> set[str]:
    return {item.name for item in table_args[1:] if isinstance(item, sa.Column)}


def _checks(table_args: tuple) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table_args[1:]
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_impact_model_indexes_match_the_migration_contract() -> None:
    impact_tables = (
        EventImpactHypothesis.__table__,
        EventImpactHypothesisAssessment.__table__,
        CompanyImpactRelation.__table__,
        CompanyImpactRelationReview.__table__,
        CompanyImpactObservation.__table__,
    )

    assert {
        index.name: tuple(column.name for column in index.columns)
        for table in impact_tables
        for index in table.indexes
    } == {
        "ix_event_impact_hypotheses_case_scope_rank": (
            "research_case_id",
            "scope_version_id",
            "rank",
        ),
        "ix_event_impact_assessments_case_scope_rank": (
            "research_case_id",
            "scope_version_id",
            "rank",
        ),
        "ix_event_impact_assessments_hypothesis_created": (
            "hypothesis_id",
            "created_at",
        ),
        "ix_company_impact_relations_hypothesis_company": (
            "hypothesis_id",
            "affected_company_id",
        ),
        "ix_company_impact_relation_reviews_relation_created": (
            "relation_id",
            "created_at",
        ),
        "ix_company_impact_observations_relation_kind_status": (
            "relation_id",
            "kind",
            "status",
        ),
    }


def test_china_market_data_model_indexes_match_the_migration_contract() -> None:
    ledger_tables = (Stock.__table__, ValuationSnapshot.__table__, HoldingDisclosure.__table__)

    assert {
        index.name: tuple(column.name for column in index.columns)
        for table in ledger_tables
        for index in table.indexes
    } == {
        "ix_stocks_company_market": ("company_id", "market"),
        "ix_valuation_snapshots_stock_metric_as_of": (
            "stock_id",
            "metric_name",
            "as_of_date",
        ),
        "ix_holding_disclosures_stock_published_report": (
            "stock_id",
            "published_at",
            "report_period",
        ),
    }


def test_impact_migration_creates_indexed_immutable_tables_on_postgres() -> None:
    migration = _load_migration()
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    tables = {args[0]: args for args in operations.tables}
    assert migration.revision == "0019"
    assert migration.down_revision == "0018"
    assert _columns(tables["event_impact_hypotheses"]) == {
        "id", "research_case_id", "scope_version_id", "statement", "classification",
        "rank", "score_components", "explanation", "created_at",
    }
    assert _checks(tables["event_impact_hypotheses"]) == {
        "classification IN ('candidate', 'key', 'alternative', 'background', 'unresolved')"
    }
    assert _checks(tables["company_impact_relations"]) == {
        "relation_kind IN ('supplier', 'customer', 'competitor', 'partner', 'industry_peer')",
        "direction IN ('benefits', 'harms', 'mixed', 'unknown')",
        "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
    }
    assert _checks(tables["company_impact_relation_reviews"]) == {
        "outcome IN ('accepted', 'rejected', 'needs_more')"
    }
    assert _checks(tables["company_impact_observations"]) == {
        "kind IN ('event', 'relation', 'operating', 'market', 'peer_control', 'fund')",
        "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
    }
    assert {(name, tuple(columns)) for name, _, columns, *rest in operations.indexes} == {
        ("ix_event_impact_hypotheses_case_scope_rank", ("research_case_id", "scope_version_id", "rank")),
        ("ix_company_impact_relations_hypothesis_company", ("hypothesis_id", "affected_company_id")),
        ("ix_company_impact_observations_relation_kind_status", ("relation_id", "kind", "status")),
        ("ix_company_impact_relation_reviews_relation_created", ("relation_id", "created_at")),
    }
    assert operations.executed == [
        f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        for table in migration._IMMUTABLE_TABLES
        for action in ("update", "delete")
    ]


def test_impact_migration_skips_postgres_triggers_on_sqlite() -> None:
    migration = _load_migration()
    operations = _OperationsRecorder("sqlite")
    migration.op = operations

    migration.upgrade()

    assert len(operations.tables) == 4
    assert operations.executed == []


def test_impact_migration_downgrade_drops_triggers_indexes_and_tables() -> None:
    migration = _load_migration()
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.downgrade()

    assert operations.executed == [
        f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};"
        for table in reversed(migration._IMMUTABLE_TABLES)
        for action in ("update", "delete")
    ]
    assert operations.dropped_indexes == [
        ("ix_company_impact_observations_relation_kind_status",),
        ("ix_company_impact_relation_reviews_relation_created",),
        ("ix_company_impact_relations_hypothesis_company",),
        ("ix_event_impact_hypotheses_case_scope_rank",),
    ]
    assert operations.dropped_tables == [
        ("company_impact_observations",),
        ("company_impact_relation_reviews",),
        ("company_impact_relations",),
        ("event_impact_hypotheses",),
    ]


def test_0020_migration_claim_and_company_identity_contract_on_postgres() -> None:
    migration = _load_migration(MIGRATION_0020_PATH)
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0020"
    assert migration.down_revision == "0019"
    assert operations.added_columns[0][0] == "companies"
    assert operations.added_columns[0][1].name == "canonical_identity"
    assert operations.indexes == [
        (
            "uq_companies_type_canonical_identity",
            "companies",
            ["type", "canonical_identity"],
            {"unique": True},
        )
    ]
    claims = next(args for args in operations.tables if args[0] == "event_impact_refresh_claims")
    assert _columns(claims) == {
        "id", "research_case_id", "scope_version_id", "run_id", "refresh_key", "created_at"
    }
    assert "no_update_event_impact_refresh_claims" in operations.executed[0]
    assert "no_delete_event_impact_refresh_claims" in operations.executed[1]

    migration.downgrade()
    assert operations.dropped_columns == [("companies", "canonical_identity")]
    assert operations.dropped_tables[-1] == ("event_impact_refresh_claims",)


def test_0021_migration_alias_contract_on_postgres() -> None:
    migration = _load_migration(MIGRATION_0021_PATH)
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0021"
    assert migration.down_revision == "0020"
    aliases = next(args for args in operations.tables if args[0] == "company_identity_aliases")
    assert _columns(aliases) == {
        "id", "company_id", "company_type", "canonical_identity", "created_at"
    }
    assert "no_update_company_identity_aliases" in operations.executed[0]
    assert "no_delete_company_identity_aliases" in operations.executed[1]

    migration.downgrade()
    assert operations.dropped_tables[-1] == ("company_identity_aliases",)


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_0022_migration_creates_and_drops_china_market_data_indexes(dialect: str) -> None:
    migration = _load_migration(MIGRATION_0022_PATH)
    operations = _OperationsRecorder(dialect)
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0022"
    assert migration.down_revision == "0021"
    assert {
        (name, table, tuple(columns), options)
        for name, table, columns, options in operations.indexes
    } == {
        (
            "ix_valuation_snapshots_stock_metric_as_of",
            "valuation_snapshots",
            ("stock_id", "metric_name", "as_of_date"),
            None,
        ),
        (
            "ix_holding_disclosures_stock_published_report",
            "holding_disclosures",
            ("stock_id", "published_at", "report_period"),
            None,
        ),
        ("ix_stocks_company_market", "stocks", ("company_id", "market"), None),
    }

    migration.downgrade()

    assert operations.dropped_indexes == [
        ("ix_stocks_company_market",),
        ("ix_holding_disclosures_stock_published_report",),
        ("ix_valuation_snapshots_stock_metric_as_of",),
    ]


def test_0022_sqlite_upgrade_and_downgrade_create_the_ledger_indexes() -> None:
    """Use SQLite DDL to protect the migration path used in local development."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE stocks (id VARCHAR(36) PRIMARY KEY, company_id VARCHAR(36) NOT NULL, "
                "market VARCHAR(64) NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE valuation_snapshots (id VARCHAR(36) PRIMARY KEY, stock_id VARCHAR(36) "
                "NOT NULL, metric_name VARCHAR(64) NOT NULL, as_of_date DATE NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE holding_disclosures (id VARCHAR(36) PRIMARY KEY, stock_id VARCHAR(36) "
                "NOT NULL, published_at DATETIME NOT NULL, report_period DATE NOT NULL)"
            )
        )
        migration = _load_migration(MIGRATION_0022_PATH)
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            index["name"]: tuple(index["column_names"])
            for table in ("stocks", "valuation_snapshots", "holding_disclosures")
            for index in inspector.get_indexes(table)
        } == {
            "ix_stocks_company_market": ("company_id", "market"),
            "ix_valuation_snapshots_stock_metric_as_of": (
                "stock_id",
                "metric_name",
                "as_of_date",
            ),
            "ix_holding_disclosures_stock_published_report": (
                "stock_id",
                "published_at",
                "report_period",
            ),
        }

        migration.downgrade()
        assert all(
            not sa.inspect(connection).get_indexes(table)
            for table in ("stocks", "valuation_snapshots", "holding_disclosures")
        )


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_0023_migration_persists_immutable_impact_assessments(dialect: str) -> None:
    migration = _load_migration(MIGRATION_0023_PATH)
    operations = _OperationsRecorder(dialect)
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0023"
    assert migration.down_revision == "0022"
    assessment = next(
        args
        for args in operations.tables
        if args[0] == "event_impact_hypothesis_assessments"
    )
    assert _columns(assessment) == {
        "id",
        "hypothesis_id",
        "research_case_id",
        "scope_version_id",
        "classification",
        "rank",
        "score_components",
        "explanation",
        "created_at",
    }
    assert _checks(assessment) == {
        "classification IN ('key', 'alternative', 'background', 'unresolved')"
    }
    assert {
        (name, table, tuple(columns))
        for name, table, columns, _options in operations.indexes
    } == {
        (
            "ix_event_impact_assessments_case_scope_rank",
            "event_impact_hypothesis_assessments",
            ("research_case_id", "scope_version_id", "rank"),
        ),
        (
            "ix_event_impact_assessments_hypothesis_created",
            "event_impact_hypothesis_assessments",
            ("hypothesis_id", "created_at"),
        ),
    }
    if dialect == "postgresql":
        assert operations.executed == [
            "CREATE TRIGGER no_update_event_impact_hypothesis_assessments BEFORE UPDATE "
            "ON event_impact_hypothesis_assessments FOR EACH ROW EXECUTE FUNCTION "
            "reject_mutable_ledger();",
            "CREATE TRIGGER no_delete_event_impact_hypothesis_assessments BEFORE DELETE "
            "ON event_impact_hypothesis_assessments FOR EACH ROW EXECUTE FUNCTION "
            "reject_mutable_ledger();",
        ]
    else:
        assert operations.executed == []

    migration.downgrade()

    assert operations.dropped_indexes == [
        ("ix_event_impact_assessments_hypothesis_created",),
        ("ix_event_impact_assessments_case_scope_rank",),
    ]
    assert operations.dropped_tables == [("event_impact_hypothesis_assessments",)]


def test_0023_sqlite_upgrade_and_downgrade_create_assessment_indexes() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE research_cases (id VARCHAR(36) PRIMARY KEY)")
        )
        connection.execute(
            sa.text(
                "CREATE TABLE event_research_scope_versions (id VARCHAR(36) PRIMARY KEY)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE event_impact_hypotheses (id VARCHAR(36) PRIMARY KEY)"
            )
        )
        migration = _load_migration(MIGRATION_0023_PATH)
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        assert {
            index["name"]: tuple(index["column_names"])
            for index in sa.inspect(connection).get_indexes(
                "event_impact_hypothesis_assessments"
            )
        } == {
            "ix_event_impact_assessments_case_scope_rank": (
                "research_case_id",
                "scope_version_id",
                "rank",
            ),
            "ix_event_impact_assessments_hypothesis_created": (
                "hypothesis_id",
                "created_at",
            ),
        }

        migration.downgrade()
        assert "event_impact_hypothesis_assessments" not in sa.inspect(
            connection
        ).get_table_names()


def test_0021_sqlite_upgrade_backfills_nfkc_aliases_for_legacy_companies() -> None:
    """Exercise the actual SQLite bind path used by developer/test upgrades."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE companies ("
                "id VARCHAR(36) PRIMARY KEY, type VARCHAR(64) NOT NULL, "
                "name VARCHAR(512) NOT NULL, canonical_identity VARCHAR(512))"
            )
        )
        company_id = str(uuid.uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO companies (id, type, name, canonical_identity) "
                "VALUES (:id, 'listed', :name, NULL)"
            ),
            {"id": company_id, "name": "Ａｃｍｅ　Supplier"},
        )
        migration = _load_migration(MIGRATION_0021_PATH)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        alias = connection.execute(
            sa.text(
                "SELECT company_id, company_type, canonical_identity "
                "FROM company_identity_aliases"
            )
        ).mappings().one()

    assert alias == {
        "company_id": company_id,
        "company_type": "listed",
        "canonical_identity": "acme supplier",
    }
