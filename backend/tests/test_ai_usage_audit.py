from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ai.client import LLMClient, LLMMalformedResponseError
from app.ai.runs import record_run
from app.ai.usage import capture_usage, current_usage


def client_for(usage, content='{}'):
    response = SimpleNamespace(usage=usage, choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content=content, refusal=None))])
    create = MagicMock(return_value=response)
    return LLMClient(model_version='test', client=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))), create


def test_retry_records_unknown_transport_cost_and_reported_success(session, monkeypatch):
    client, create = client_for(SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18))
    create.side_effect = [TimeoutError('secret'), create.return_value]
    monkeypatch.setattr('app.ai.client.time.sleep', lambda _: None)
    with capture_usage():
        assert client.chat_json([]) == {}
        run = record_run(session, kind='extract', model_version='test', prompt_version='v1',
                         input_ref={'document_version_id':'source'}, output_summary='ok',
                         status='success', started_at=datetime.now(timezone.utc))
        session.commit()
        session.expire(run)
        attempts = run.usage['attempts']
        assert len(attempts) == 2
        assert attempts[0]['usage_state'] == 'unavailable'
        assert attempts[0]['total_tokens'] is None
        assert attempts[1]['total_tokens'] == 18
        assert attempts[1]['usage_state'] == 'reported'
        assert 'secret' not in str(run.usage)
    assert current_usage() is None


@pytest.mark.parametrize('usage', [None, SimpleNamespace(prompt_tokens=True, completion_tokens=1, total_tokens=2),
    SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=2)])
def test_unreliable_usage_is_not_zero(usage):
    client, _ = client_for(usage)
    with capture_usage():
        client.chat_json([])
        assert current_usage()['attempts'][0]['total_tokens'] is None


def test_malformed_output_still_records_consumed_tokens():
    client, _ = client_for(SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5), content=None)
    with capture_usage():
        with pytest.raises(LLMMalformedResponseError):
            client.chat_json([])
        assert current_usage()['attempts'][0]['total_tokens'] == 5


def test_nested_operations_do_not_share_usage():
    client, _ = client_for(SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    with capture_usage():
        client.chat_json([])
        with capture_usage():
            assert current_usage()['attempts'] == []
        assert len(current_usage()['attempts']) == 1


def test_failed_proposal_retains_usage_with_its_thesis(session, document_service, research_service, thesis, document):
    from sqlalchemy import select
    from app.ai.proposal import EvidenceProposer
    from app.models.ledger import AIRun
    span = document_service.add_span(document.id, {'page': 99}, 'GPU demand increased')
    research_service.add_statement(span.id, 'GPU demand increased', kind='disclosed_fact')
    client, _ = client_for(SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9))
    with pytest.raises(LLMMalformedResponseError):
        EvidenceProposer(client).propose(thesis.id, session)
    session.commit()
    run = session.scalar(select(AIRun).where(AIRun.kind == 'propose'))
    assert run.status == 'failed'
    assert run.input_ref['thesis_id'] == str(thesis.id)
    assert run.usage['attempts'][0]['total_tokens'] == 9
    assert current_usage() is None


def test_usage_migration_preserves_historical_rows_and_adopts_existing_column(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    path = Path(__file__).parents[1] / 'alembic/versions/0071_ai_run_usage.py'
    spec = importlib.util.spec_from_file_location('usage_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "audit.sqlite"}')
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE ai_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL)'))
            conn.execute(text("INSERT INTO ai_runs VALUES ('historical', 'failed')"))
            monkeypatch.setattr(migration, 'op', Operations(MigrationContext.configure(conn)))
            migration.upgrade()
            migration.upgrade()
            assert conn.execute(text('SELECT id, status, usage FROM ai_runs')).one() == ('historical', 'failed', None)
    finally:
        engine.dispose()


def test_late_response_keeps_reported_usage_even_when_result_is_rejected(monkeypatch):
    from app.ai.client import LLMProviderError
    client, _ = client_for(SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5))
    clock = iter([0, 0, 181])
    monkeypatch.setattr('app.ai.client.time.monotonic', lambda: next(clock))
    with capture_usage():
        with pytest.raises(LLMProviderError):
            client.chat_json([])
        assert current_usage()['attempts'][0]['total_tokens'] == 5


def test_engine_audit_context_is_nested_and_does_not_leak(session):
    import uuid
    from app.ai.runs import research_audit_context
    case_id, run_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    def write():
        return record_run(session, kind='assess', model_version='test', prompt_version='v1',
                          input_ref={'thesis_id': 't'}, output_summary='ok', status='success',
                          started_at=datetime.now(timezone.utc))
    with research_audit_context(case_id=case_id, run_id=run_id):
        outer = write()
        with pytest.raises(RuntimeError):
            with research_audit_context(case_id=case_id, run_id=run_id, task_id=task_id):
                inner = write()
                raise RuntimeError('exit test')
        restored = write()
    unrelated = write()
    assert outer.input_ref == restored.input_ref == {'thesis_id': 't', 'research_case_id': str(case_id), 'research_run_id': str(run_id)}
    assert inner.input_ref == {**outer.input_ref, 'research_task_id': str(task_id)}
    assert unrelated.input_ref == {'thesis_id': 't'}


def test_standalone_acquisition_does_not_inherit_an_unrelated_run(session):
    import uuid
    from app.ai.runs import research_audit_context
    case_id, run_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with research_audit_context(case_id=case_id, run_id=run_id):
        with research_audit_context(case_id=case_id, run_id=None, acquisition_job_id=job_id):
            audit = record_run(session, kind='extract', model_version='fixture', prompt_version='v1',
                               input_ref={}, output_summary='fixture', status='success',
                               started_at=datetime.now(timezone.utc))
    assert audit.input_ref == {'research_case_id': str(case_id), 'acquisition_job_id': str(job_id)}
