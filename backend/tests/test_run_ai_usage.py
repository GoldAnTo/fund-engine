from datetime import datetime, timezone
import uuid
import pytest

from app.models.ledger import AIRun, ResearchCase, CaseTenantAdmission, DocumentVersion
from app.models.operational import ResearchRun


def test_run_usage_sums_only_attributed_records_and_keeps_unknowns(api_client, session, monkeypatch):
    cmd_client, cmd_session = api_client, session
    now = datetime.now(timezone.utc)
    case = ResearchCase(title='usage', industry_topic='i', created_by='u', created_at=now)
    cmd_session.add(case); cmd_session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url='https://example.test', available_at=now, acquired_at=now, parser_version='test')
    cmd_session.add(document); cmd_session.flush()
    cmd_session.add(CaseTenantAdmission(initial_document_version_id=document.id, research_case_id=case.id, tenant_id='test-team', admitted_by='test', admitted_at=now))
    run = ResearchRun(research_case_id=case.id, status='queued', stage='queued', round=1, max_rounds=1, budget=10, budget_used=0, created_at=now, updated_at=now)
    cmd_session.add(run); cmd_session.flush()
    known = {'outcome': 'success', 'usage_state': 'reported', 'prompt_tokens': 10, 'completion_tokens': 8, 'total_tokens': 18}
    def audit(ref, usage):
        cmd_session.add(AIRun(kind='assess', model_version='test', prompt_version='v1', input_ref=ref, usage=usage, output_summary='private text', status='success', started_at=now, finished_at=now))
    ref = {'research_run_id': str(run.id), 'research_case_id': str(case.id)}
    audit(ref, {'schema_version': 'llm_usage.v1', 'attempts': [known, {'usage_state': 'unavailable'}]})
    audit(ref, None)
    audit({**ref, 'research_run_id': str(uuid.uuid4())}, {'schema_version': 'llm_usage.v1', 'attempts': [known]})
    audit({'thesis_id': str(uuid.uuid4())}, {'schema_version': 'llm_usage.v1', 'attempts': [known]})
    cmd_session.commit()
    response = cmd_client.get(f'/api/v1/research-runs/{run.id}/ai-usage')
    assert response.status_code == 200
    data = response.json()
    assert data['operation_count'] == 2
    assert data['reported_attempt_count'] == 1
    assert data['unavailable_attempt_count'] == 1
    assert data['unavailable_operation_count'] == 1
    assert data['reported_total_tokens'] == 18
    assert data['recorded_total_tokens'] is None
    assert data['coverage'] == 'recorded_attributed_operations_only'
    assert 'private text' not in response.text
    case_response = cmd_client.get(f'/api/v1/research-cases/{case.id}/ai-usage')
    assert case_response.status_code == 200
    case_usage = case_response.json()
    assert case_usage['case_id'] == str(case.id)
    assert case_usage['operation_count'] == 3
    assert case_usage['reported_total_tokens'] == 36
    assert case_usage['recorded_total_tokens'] is None
    assert 'private text' not in case_response.text

    monkeypatch.setenv('RESEARCH_TENANT_TOKENS', '{"test-tenant-token":"test-team","other-team-token":"other-team"}')
    assert cmd_client.get(f'/api/v1/research-runs/{run.id}/ai-usage', headers={'Authorization': 'Bearer other-team-token'}).status_code == 404
    assert cmd_client.get(f'/api/v1/research-cases/{case.id}/ai-usage', headers={'Authorization': 'Bearer other-team-token'}).status_code == 404


@pytest.mark.parametrize('records,total,unknown_operations,unknown_attempts', [
    ([], None, 0, 0),
    ([None], None, 1, 0),
    ([{'schema_version': 'future', 'attempts': []}], None, 1, 0),
    ([{'schema_version': 'llm_usage.v1', 'attempts': []}], 0, 0, 0),
    ([{'schema_version': 'llm_usage.v1', 'attempts': [{'usage_state': 'reported', 'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}]}], 5, 0, 0),
    ([{'schema_version': 'llm_usage.v1', 'attempts': [{'usage_state': 'reported', 'prompt_tokens': True, 'completion_tokens': 3, 'total_tokens': 4}]}], None, 0, 1),
    ([{'schema_version': 'llm_usage.v1', 'attempts': [{'usage_state': 'reported', 'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 4}]}], None, 0, 1),
    ([{'schema_version': 'llm_usage.v1', 'attempts': [None]}], None, 0, 1),
])
def test_usage_summary_preserves_missing_and_invalid_values(records, total, unknown_operations, unknown_attempts):
    from app.queries.run_ai_usage import summarize_usage
    result = summarize_usage(records)
    assert result['recorded_total_tokens'] == total
    assert result['unavailable_operation_count'] == unknown_operations
    assert result['unavailable_attempt_count'] == unknown_attempts


def test_sqlite_usage_query_uses_scope_index():
    from sqlalchemy import create_engine, select
    from app.ai.scope_columns import AuditCaseRef, AuditRunRef
    engine = create_engine('sqlite:///:memory:')
    AIRun.__table__.create(engine)
    try:
        with engine.connect() as connection:
            for predicates in [(AuditCaseRef(AIRun.input_ref) == 'case',),
                               (AuditCaseRef(AIRun.input_ref) == 'case', AuditRunRef(AIRun.input_ref) == 'run')]:
                query = select(AIRun.usage).where(*predicates).compile(engine)
                plan = connection.exec_driver_sql('EXPLAIN QUERY PLAN ' + str(query), tuple(query.params[k] for k in query.positiontup)).all()
                assert any('USING INDEX ix_ai_runs_research_scope' in row[-1] for row in plan), plan
    finally:
        engine.dispose()


def test_scope_index_migration_can_upgrade_existing_database(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    path = Path(__file__).parents[1] / 'alembic/versions/0072_ai_run_scope_index.py'
    spec = importlib.util.spec_from_file_location('scope_index_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "audit.db"}')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE ai_runs (input_ref JSON NOT NULL)')
            monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            migration.upgrade()
            assert any(row[1] == 'ix_ai_runs_research_scope' for row in connection.exec_driver_sql("PRAGMA index_list('ai_runs')"))
            migration.downgrade()
            assert not list(connection.exec_driver_sql("PRAGMA index_list('ai_runs')"))
    finally:
        engine.dispose()


@pytest.mark.pg_only
def test_postgres_usage_query_uses_scope_index(session):
    cmd_session = session
    from sqlalchemy import select, text
    from app.ai.scope_columns import AuditCaseRef, AuditRunRef
    if cmd_session.bind.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL execution plan')
    cmd_session.execute(text("""
        INSERT INTO ai_runs (id, kind, model_version, prompt_version, input_ref, output_summary, status, started_at)
        SELECT md5('usage-index-plan-' || value::text)::uuid, 'assess', 'fixture', 'fixture',
               json_build_object('research_case_id', 'case-' || value::text, 'research_run_id', 'run-' || value::text),
               '', 'success', now()
        FROM generate_series(1, 5000) AS value
    """))
    cmd_session.execute(text('ANALYZE ai_runs'))
    for predicates in [(AuditCaseRef(AIRun.input_ref) == 'case-2500',),
                       (AuditCaseRef(AIRun.input_ref) == 'case-2500', AuditRunRef(AIRun.input_ref) == 'run-2500')]:
        query = select(AIRun.usage).where(*predicates).compile(cmd_session.bind, compile_kwargs={'literal_binds': True})
        plan = cmd_session.execute(text('EXPLAIN (FORMAT JSON) ' + str(query))).scalar_one()
        assert 'ix_ai_runs_research_scope' in str(plan), plan
