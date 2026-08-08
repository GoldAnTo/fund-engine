from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

import app.models as models
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyImpactRelationReview,
    EventImpactHypothesis,
)
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Company, ImmutableLedgerError, Stock


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
