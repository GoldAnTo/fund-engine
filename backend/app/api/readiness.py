"""Deployment readiness, separate from process liveness and worker progress."""
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(tags=['health'])


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[2] / 'alembic'))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


@router.get('/ready', responses={503: {'description': 'Database or schema is not ready'}})
def readiness(db: Session = Depends(get_db)):
    checks = {'database': 'unavailable', 'schema': 'unavailable'}
    try:
        db.execute(text('SELECT 1'))
        checks['database'] = 'ok'
        actual = frozenset(db.execute(text('SELECT version_num FROM alembic_version')).scalars())
        checks['schema'] = 'current' if actual == expected_heads() and actual else 'outdated'
    except SQLAlchemyError:
        # Upstream messages can contain connection strings. Return fixed states.
        db.rollback()
    ready = checks == {'database': 'ok', 'schema': 'current'}
    return JSONResponse(
        {'status': 'ready' if ready else 'not_ready', 'checks': checks},
        status_code=200 if ready else 503,
        headers={'Cache-Control': 'no-store'},
    )
