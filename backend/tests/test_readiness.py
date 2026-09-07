"""Readiness checks deployment prerequisites without revealing connection details."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def readiness_client():
    from app.main import app
    from app.db import get_db
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with Session(engine) as session:
        app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(app) as client:
                yield client, session
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_readiness_requires_current_migration(readiness_client):
    client, session = readiness_client
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'alembic'))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads
    session.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
    session.execute(text("INSERT INTO alembic_version VALUES ('outdated')"))
    session.commit()
    assert client.get('/ready').status_code == 503
    session.execute(text('DELETE FROM alembic_version'))
    for head in heads:
        session.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': head})
    session.commit()
    response = client.get('/ready')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == {'status': 'ready', 'checks': {'database': 'ok', 'schema': 'current'}}


def test_missing_migration_is_not_ready_but_process_is_alive(readiness_client):
    client, _ = readiness_client
    assert client.get('/ready').status_code == 503
    assert client.get('/health').status_code == 200


def test_database_error_is_sanitized(readiness_client, monkeypatch):
    from sqlalchemy.exc import OperationalError
    client, session = readiness_client
    def fail(*args, **kwargs):
        raise OperationalError('postgres://user:private-password@private-host', {}, RuntimeError('unsafe detail'))
    monkeypatch.setattr(session, 'execute', fail)
    response = client.get('/ready')
    assert response.status_code == 503
    assert response.json()['checks']['database'] == 'unavailable'
    assert 'private' not in response.text and 'unsafe' not in response.text
    assert client.get('/health').status_code == 200


@pytest.mark.parametrize('versions', [[], ['unexpected-branch']])
def test_missing_or_extra_database_heads_are_not_ready(readiness_client, versions):
    client, session = readiness_client
    from app.api.readiness import expected_heads
    session.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
    # Empty set is missing schema; current heads plus one unknown branch are
    # not the exact deployed migration graph either.
    for head in ([*expected_heads(), *versions] if versions else []):
        session.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': head})
    session.commit()
    response = client.get('/ready')
    assert response.status_code == 503
    assert response.json()['checks']['schema'] == 'outdated'
    assert response.headers['cache-control'] == 'no-store'
