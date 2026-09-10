"""Frozen 0075 DDL and raw-SQL integrity tests on disposable databases only."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from alembic import command
from app.models import research_team as model

TABLES = ('research_teams', 'professional_tasks', 'professional_dependencies', 'professional_outputs', 'professional_attempts', 'professional_events', 'professional_requests', 'professional_reviews')
IMMUTABLE = TABLES[2:]


def config(url):
    result = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
    result.set_main_option('sqlalchemy.url', url)
    return result


def test_0075_is_a_frozen_successor_of_0074():
    revisions = {r.revision: r for r in ScriptDirectory.from_config(config('sqlite://')).walk_revisions()}
    assert '0075' in revisions
    assert revisions['0075'].down_revision == '0074'
    source = Path(revisions['0075'].path).read_text()
    assert 'app.models' not in source
    assert 'create_all' not in source
    assert 'op.create_table(' in source


@pytest.fixture(scope='module', params=['sqlite', 'postgresql'])
def database(request, tmp_path_factory):
    dialect = request.param
    schema = None
    if dialect == 'sqlite':
        url = f"sqlite:///{tmp_path_factory.mktemp('team-migration') / 'audit.db'}"
    else:
        # Deliberately separate from TEST_DATABASE_URL and every app default.
        url = os.getenv('TEAM_MIGRATION_AUDIT_PG_URL')
        if not url:
            pytest.skip('requires explicitly disposable TEAM_MIGRATION_AUDIT_PG_URL')
        schema = 'professional_audit_' + uuid.uuid4().hex
        admin = sa.create_engine(url)
        with admin.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        url += ('&' if '?' in url else '?') + f'options=-csearch_path={schema}'
    cfg = config(url)
    command.upgrade(cfg, '0074')
    engine = sa.create_engine(url)
    if dialect == 'sqlite':
        sa.event.listen(engine, 'connect', lambda dbapi, _: dbapi.execute('PRAGMA foreign_keys=ON'))
    with engine.connect() as conn:
        previous = set(sa.inspect(conn).get_table_names())
        assert not set(TABLES) & previous
    command.upgrade(cfg, '0075')
    try:
        yield engine
    finally:
        command.downgrade(cfg, '0074')
        with engine.connect() as conn:
            assert set(sa.inspect(conn).get_table_names()) == previous
            assert conn.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0074'
        engine.dispose()
        if schema:
            with admin.begin() as conn:
                conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


@pytest.fixture
def seeded(database):
    from tests.test_research_gateway_service import _gateway, _send
    with database.connect() as conn:
        outer = conn.begin()
        if conn.dialect.name == 'sqlite':
            conn.exec_driver_sql('BEGIN')
        session = Session(bind=conn)
        first = _send(_gateway(session), key='team-audit-first')
        second = _send(_gateway(session), key='team-audit-second')
        session.flush()
        first_id, second_id = uuid.UUID(str(first.run_spec_id)), uuid.UUID(str(second.run_spec_id))
        tasks = list(session.scalars(sa.select(model.ProfessionalTask).where(model.ProfessionalTask.run_spec_id == first_id).order_by(model.ProfessionalTask.role)))
        foreign = session.scalar(sa.select(model.ProfessionalTask).where(model.ProfessionalTask.run_spec_id == second_id))
        output = model.ProfessionalOutput(task_id=tasks[0].id, run_spec_id=first_id, content={}, evidence_manifest=[], dependency_output_ids=[], input_sha256='a' * 64)
        foreign_output = model.ProfessionalOutput(task_id=foreign.id, run_spec_id=second_id, content={}, evidence_manifest=[], dependency_output_ids=[], input_sha256='b' * 64)
        session.add_all([output, foreign_output])
        session.flush()
        session.add_all([
            model.ProfessionalEvent(run_spec_id=first_id, task_id=tasks[0].id, sequence=9999, kind='audit'),
            model.ProfessionalAttempt(task_id=tasks[0].id, task_attempt=1, call_id='audit-call', attempt=1, details={}),
            model.ProfessionalRequest(run_spec_id=first_id, key_sha256='a' * 64, payload_sha256='b' * 64, kind='audit', receipt={}),
            model.ProfessionalReview(run_spec_id=first_id, revision=1, decision='approved', comment='', reviewed_by='alice', output_ids=[str(output.id)]),
        ])
        session.flush()
        try:
            yield conn, tasks, foreign, output, foreign_output
        finally:
            session.close()
            outer.rollback()


def rejected(conn, sql, params=None):
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(sa.text(sql), params or {})


@pytest.mark.parametrize('table', IMMUTABLE)
@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_raw_sql_append_only(seeded, table, operation):
    conn = seeded[0]
    column = 'task_id' if table == 'professional_dependencies' else 'id'
    sql = f'UPDATE {table} SET {column}={column}' if operation == 'UPDATE' else f'DELETE FROM {table}'
    rejected(conn, sql)


@pytest.mark.parametrize('table', ['research_teams', 'professional_tasks'])
def test_raw_sql_delete_protection(seeded, table):
    rejected(seeded[0], f'DELETE FROM {table}')


@pytest.mark.parametrize('column,value', [('id', "'00000000000000000000000000000000'"), ('run_spec_id', "'00000000000000000000000000000000'"), ('revision', 'revision+1'), ('role', "'industry'"), ('instruction', "'rewritten'")])
def test_task_definition_is_frozen(seeded, column, value):
    rejected(seeded[0], f'UPDATE professional_tasks SET {column}={value}')


def test_task_runtime_and_team_counters_are_mutable(seeded):
    conn = seeded[0]
    conn.execute(sa.text("UPDATE professional_tasks SET status='running', attempt=attempt+1, reason_code='claimed'"))
    conn.execute(sa.text("UPDATE research_teams SET status='paused', revision=revision+1, event_sequence=event_sequence+1"))
    assert conn.scalar(sa.text("SELECT count(*) FROM professional_tasks WHERE status='running'")) == 8


@pytest.mark.parametrize('table,field,value', [('professional_tasks','role',"'unknown'"), ('professional_tasks','status',"'complete'"), ('professional_tasks','attempt','-1'), ('research_teams','status',"'unknown'"), ('research_teams','revision','0'), ('research_teams','event_sequence','-1')])
def test_invalid_counters_and_enums_fail(seeded, table, field, value):
    rejected(seeded[0], f'UPDATE {table} SET {field}={value}')


def test_cross_run_foreign_keys_and_json_output_references_fail(seeded):
    conn, tasks, foreign, _, foreign_output = seeded
    values = {"task_id": tasks[1].id, "run_spec_id": tasks[1].run_spec_id, "content": {}, "evidence_manifest": [], "dependency_output_ids": [], "input_sha256": 'c'*64, "created_at": datetime.now(UTC)}
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(model.ProfessionalDependency.__table__.insert().values(task_id=tasks[0].id, parent_task_id=foreign.id, run_spec_id=tasks[0].run_spec_id))
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(model.ProfessionalOutput.__table__.insert().values(id=uuid.uuid4(), **{**values, 'run_spec_id': foreign.run_spec_id}))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalOutput.__table__.insert().values(id=uuid.uuid4(), **{**values, 'dependency_output_ids': [str(foreign_output.id)]}))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalReview.__table__.insert().values(id=uuid.uuid4(), run_spec_id=tasks[0].run_spec_id, revision=1, decision='approved', comment='', reviewed_by='alice', output_ids=[str(foreign_output.id)], created_at=datetime.now(UTC)))


def test_empty_database_upgrade_head_and_downgrade(tmp_path):
    cfg = config(f"sqlite:///{tmp_path / 'fresh.db'}")
    command.upgrade(cfg, 'head')
    engine = sa.create_engine(cfg.get_main_option('sqlalchemy.url'))
    with engine.connect() as conn:
        assert conn.scalar(sa.text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(cfg).get_current_head()
        assert set(TABLES) <= set(sa.inspect(conn).get_table_names())
    command.downgrade(cfg, '0074')
    with engine.connect() as conn:
        assert not set(TABLES) & set(sa.inspect(conn).get_table_names())
    engine.dispose()


@pytest.mark.parametrize('database', ['sqlite'], indirect=True)
@pytest.mark.parametrize('table', TABLES)
def test_sqlite_replace_cannot_bypass_history_guards(seeded, table):
    conn = seeded[0]
    conn.execute(sa.text('PRAGMA recursive_triggers=OFF'))
    rejected(conn, f'INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1')


@pytest.mark.parametrize('field,value', [('role','unknown'), ('status','complete'), ('revision',0), ('attempt',-1)])
def test_task_insert_checks(seeded, field, value):
    conn, tasks, *_ = seeded
    values = {"id": uuid.uuid4(), "run_spec_id": tasks[0].run_spec_id, "role": 'industry', "revision": 2}
    values[field] = value
    with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
        conn.execute(model.ProfessionalTask.__table__.insert().values(**values))


def test_unique_constraints_and_event_scope(seeded):
    conn, tasks, foreign, output, _ = seeded
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalTask.__table__.insert().values(id=uuid.uuid4(), run_spec_id=tasks[0].run_spec_id, revision=1, role=tasks[0].role))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalEvent.__table__.insert().values(id=uuid.uuid4(), run_spec_id=tasks[0].run_spec_id, task_id=foreign.id, sequence=9998, kind='audit'))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalOutput.__table__.insert().values(id=uuid.uuid4(), task_id=output.task_id, run_spec_id=output.run_spec_id, content={}, evidence_manifest=[], dependency_output_ids=[], input_sha256='c'*64))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalEvent.__table__.insert().values(id=uuid.uuid4(), run_spec_id=tasks[0].run_spec_id, sequence=9999, kind='audit'))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalRequest.__table__.insert().values(id=uuid.uuid4(), run_spec_id=tasks[0].run_spec_id, key_sha256='a'*64, payload_sha256='c'*64, kind='audit', receipt={}))


def test_late_attempt_audit_is_not_bound_to_current_task_attempt(seeded):
    conn, tasks, *_ = seeded
    conn.execute(sa.text('UPDATE professional_tasks SET attempt=3'))
    conn.execute(model.ProfessionalAttempt.__table__.insert().values(id=uuid.uuid4(), task_id=tasks[0].id, task_attempt=1, call_id='late-audit', attempt=1, details={}))
    with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
        conn.execute(model.ProfessionalAttempt.__table__.insert().values(id=uuid.uuid4(), task_id=tasks[0].id, task_attempt=1, call_id='late-audit', attempt=1, details={}))


def test_metadata_tables_protect_definition_and_orm_delete(cmd_session):
    from app.models.ledger import ImmutableLedgerError
    from tests.test_research_team import _spec
    spec = _spec(cmd_session)
    task = cmd_session.scalar(sa.select(model.ProfessionalTask).where(model.ProfessionalTask.run_spec_id == spec.id))
    task.instruction = 'mutated definition'
    with pytest.raises(sa.exc.DBAPIError):
        cmd_session.flush()
    cmd_session.rollback()
    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(sa.delete(model.ResearchTeam))
    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(sa.delete(model.ProfessionalTask))


def test_postgres_empty_database_upgrade_head_and_downgrade():
    url = os.getenv('TEAM_MIGRATION_AUDIT_PG_URL')
    if not url:
        pytest.skip('requires explicitly disposable TEAM_MIGRATION_AUDIT_PG_URL')
    schema = 'professional_fresh_' + uuid.uuid4().hex
    admin = sa.create_engine(url)
    with admin.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    migration_url = url + ('&' if '?' in url else '?') + f'options=-csearch_path={schema}'
    engine = sa.create_engine(migration_url)
    try:
        command.upgrade(config(migration_url), 'head')
        with engine.connect() as conn:
            assert conn.scalar(sa.text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(config(migration_url)).get_current_head()
            assert set(TABLES) <= set(sa.inspect(conn).get_table_names())
        command.downgrade(config(migration_url), '0074')
        with engine.connect() as conn:
            assert not set(TABLES) & set(sa.inspect(conn).get_table_names())
            assert conn.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0074'
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
@pytest.mark.parametrize('tamper', ['missing', 'noop'])
def test_bootstrap_rejects_missing_or_altered_professional_guard_before_repairs(
    tmp_path,
    dialect,
    tamper,
):
    from app.db_migrations import UnmanagedDatabaseSchemaError, upgrade_database_to_head

    schema = None
    admin = None
    if dialect == 'sqlite':
        url = f"sqlite:///{tmp_path / 'missing-professional-guard.db'}"
    else:
        url = os.getenv('TEAM_MIGRATION_AUDIT_PG_URL')
        if not url:
            pytest.skip('requires explicitly disposable TEAM_MIGRATION_AUDIT_PG_URL')
        schema = 'professional_missing_guard_' + uuid.uuid4().hex
        admin = sa.create_engine(url)
        with admin.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        url += ('&' if '?' in url else '?') + f'options=-csearch_path={schema}'

    cfg = config(url)
    command.upgrade(cfg, 'head')
    engine = sa.create_engine(url)
    noop_sql = (
        "CREATE TRIGGER team_no_update_professional_events "
        "BEFORE UPDATE ON professional_events BEGIN SELECT 1; END"
    )
    try:
        with engine.begin() as conn:
            if dialect == 'sqlite':
                conn.exec_driver_sql('DROP TRIGGER team_no_update_professional_events')
                if tamper == 'noop':
                    conn.exec_driver_sql(noop_sql)
            elif tamper == 'missing':
                conn.execute(sa.text('DROP TRIGGER team_no_update_professional_events ON professional_events'))
            else:
                conn.execute(
                    sa.text(
                        "CREATE OR REPLACE FUNCTION "
                        "team_no_update_professional_events() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
                    )
                )

        with pytest.raises(
            UnmanagedDatabaseSchemaError,
            match='canonical professional team guard',
        ):
            upgrade_database_to_head(url)

        with engine.connect() as conn:
            assert conn.scalar(sa.text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(cfg).get_current_head()
            if dialect == 'sqlite':
                assert conn.scalar(
                    sa.text(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = 'team_no_update_professional_events'"
                    )
                ) == (None if tamper == 'missing' else noop_sql)
            else:
                assert conn.scalar(
                    sa.text(
                        "SELECT to_regclass('professional_events')"
                    )
                ) is not None
                assert conn.scalar(
                    sa.text(
                        "SELECT 1 FROM pg_trigger AS trigger_row "
                        "JOIN pg_class AS table_row "
                        "ON table_row.oid = trigger_row.tgrelid "
                        "JOIN pg_namespace AS namespace "
                        "ON namespace.oid = table_row.relnamespace "
                        "WHERE namespace.nspname = :schema "
                        "AND trigger_row.tgname = "
                        "'team_no_update_professional_events'"
                    ),
                    {"schema": schema},
                ) == (None if tamper == 'missing' else 1)
                if tamper == 'noop':
                    assert 'RETURN NEW' in conn.scalar(
                        sa.text(
                            "SELECT pg_get_functiondef(function_row.oid) "
                            "FROM pg_proc AS function_row "
                            "JOIN pg_namespace AS namespace "
                            "ON namespace.oid = function_row.pronamespace "
                            "WHERE namespace.nspname = :schema "
                            "AND function_row.proname = "
                            "'team_no_update_professional_events'"
                        ),
                        {"schema": schema},
                    )
    finally:
        engine.dispose()
        if schema and admin is not None:
            with admin.begin() as conn:
                conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
