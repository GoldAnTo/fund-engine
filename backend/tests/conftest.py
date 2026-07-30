import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

PG_URL = os.getenv("TEST_DATABASE_URL")
USE_PG = bool(PG_URL)


@pytest.fixture(scope="session")
def engine():
    if USE_PG:
        eng = create_engine(PG_URL, future=True)
    else:
        eng = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    from app.models.ledger import Base

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine) -> Session:
    SessionLocal = sessionmaker(bind=engine, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def document_service(session):
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    return DocumentService(DocumentRepository(session))


@pytest.fixture
def span(document_service):
    version = document_service.freeze(
        raw=b"page one", source_url="https://example.test/a"
    )
    return document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 0},
        verbatim_text="original span text",
    )


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def pytest_collection_modifyitems(config, items):
    if USE_PG:
        return
    skip_pg = pytest.mark.skip(reason="requires PostgreSQL (set TEST_DATABASE_URL)")
    for item in items:
        if "pg_only" in item.keywords:
            item.add_marker(skip_pg)
