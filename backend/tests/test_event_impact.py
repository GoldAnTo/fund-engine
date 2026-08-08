from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    EvidenceLink,
    ImmutableLedgerError,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    Thesis,
)
from app.services.event_impact import (
    EventImpactResearchService,
    ResolvedImpactCompany,
)
from app.errors import ValidationFailedError
from app.models.events import DomainEvent
from app.models.operational import ResearchRun, ResearchTask
from app.services.auto_research import AutoResearchService
from app.repositories.auto_research import AutoResearchRepository


NOW = datetime(2026, 8, 8, tzinfo=UTC)
MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0019_event_impact_traces.py"
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


def _hypothesis(session, research_case_id, scope_version_id) -> EventImpactHypothesis:
    hypothesis = EventImpactHypothesis(
        research_case_id=research_case_id,
        scope_version_id=scope_version_id,
        statement="资本开支增加会提高中国供应商订单",
        classification="candidate",
        rank=1,
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
    observation = session.scalar(select(CompanyImpactObservation))
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
    assert observation is not None
    assert observation.source_statement_id == statement.id
    assert observation.kind == "relation"
    assert observation.status == "verified"
    assert observation.as_of_date == date(2026, 8, 7)


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
    monkeypatch.setattr(
        "app.services.event_impact._before_refresh_claim_lock",
        lambda: refresh_barrier.wait(timeout=5),
    )
    refresh_errors: list[BaseException] = []

    def refresh() -> None:
        db = SessionLocal()
        try:
            EventImpactResearchService(db).refresh(
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
    assert len(list(session.scalars(select(CompanyImpactObservation)))) == 1


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
def test_refresh_reconciles_legacy_company_identity_with_alias(
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
    assert alias is not None and alias.canonical_identity == "acme supplier"


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

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect))

    def create_table(self, *args) -> None:
        self.tables.append(args)

    def create_index(self, *args) -> None:
        self.indexes.append(args)

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def drop_index(self, *args, **kwargs) -> None:
        self.dropped_indexes.append(args)

    def drop_table(self, *args, **kwargs) -> None:
        self.dropped_tables.append(args)


def _load_migration():
    spec = importlib.util.spec_from_file_location("event_impact_migration", MIGRATION_PATH)
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
