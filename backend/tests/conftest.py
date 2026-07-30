import os
from datetime import UTC, date, datetime
from decimal import Decimal

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


@pytest.fixture
def research_repository(session):
    from app.repositories.research import ResearchRepository

    return ResearchRepository(session)


@pytest.fixture
def research_service(research_repository):
    from app.services.research import ResearchService

    return ResearchService(research_repository)


@pytest.fixture
def research_case(research_service):
    return research_service.add_case(
        title="AI compute demand", industry_topic="ai_compute", created_by="tester"
    )


@pytest.fixture
def thesis(research_service, research_case):
    return research_service.add_thesis(
        research_case.id, statement="GPU demand will grow", created_by="tester"
    )


@pytest.fixture
def statement(research_service, span):
    return research_service.add_statement(
        span.id, "预计需求增长", kind="research_opinion"
    )


@pytest.fixture
def assessment_service(research_repository):
    from app.services.assessment import AssessmentService

    return AssessmentService(research_repository)


@pytest.fixture
def snapshot(assessment_service, thesis, statement, research_service):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    return assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=UTC)
    )


@pytest.fixture
def ai_assessment(assessment_service, snapshot):
    return assessment_service.create_ai_assessment(
        snapshot.id, conclusion="supported", rationale="evidence supports", gaps=[]
    )


@pytest.fixture
def future_link(research_service, thesis, statement):
    return research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="future orders",
        scope={"segment": "DC"},
        available_at=datetime(2026, 12, 31, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Instrument and exposure fixtures (Task 5)
# ---------------------------------------------------------------------------


@pytest.fixture
def instrument_repository(session):
    from app.repositories.instruments import InstrumentRepository

    return InstrumentRepository(session)


@pytest.fixture
def exposure_service(instrument_repository):
    from app.services.exposure import ExposureService

    return ExposureService(instrument_repository)


@pytest.fixture
def company(instrument_repository):
    return instrument_repository.add_company(
        code="000001", name="Plain Corp", type="listed"
    )


@pytest.fixture
def stock(instrument_repository, company):
    return instrument_repository.add_stock(
        company_id=company.id, code="000001.SZ", name="Plain Corp", market="SZSE"
    )


@pytest.fixture
def fund_company(instrument_repository):
    return instrument_repository.add_fund_company(
        code="FC001", name="Alpha Fund Management"
    )


@pytest.fixture
def fund(instrument_repository, fund_company):
    return instrument_repository.add_fund(
        code="001001",
        name="Alpha Growth Fund",
        fund_type="equity",
        management_company_id=fund_company.id,
        scale=Decimal("1000000000"),
        establish_date=date(2015, 1, 1),
    )


@pytest.fixture
def mapped_stock(instrument_repository):
    """A stock whose company carries an active ThemeRole."""
    mapped_company = instrument_repository.add_company(
        code="600519", name="Mapped Corp", type="listed"
    )
    instrument_repository.add_theme_role(
        company_id=mapped_company.id,
        role="beneficiary",
        scope={"segment": "AI compute"},
        applicable_from=date(2026, 1, 1),
    )
    return instrument_repository.add_stock(
        company_id=mapped_company.id,
        code="600519.SH",
        name="Mapped Corp",
        market="SSE",
    )


@pytest.fixture
def holding_disclosure(instrument_repository, fund, mapped_stock):
    return instrument_repository.add_holding_disclosure(
        fund_id=fund.id,
        stock_id=mapped_stock.id,
        weight=Decimal("0.082"),
        report_period=date(2026, 3, 31),
        published_at=date(2026, 4, 22),
        source="fund-report-2026Q1",
    )


@pytest.fixture
def future_disclosure(instrument_repository, fund, mapped_stock):
    return instrument_repository.add_holding_disclosure(
        fund_id=fund.id,
        stock_id=mapped_stock.id,
        weight=Decimal("0.091"),
        report_period=date(2026, 6, 30),
        published_at=date(2026, 7, 15),
        source="fund-report-2026Q2",
    )


def pytest_collection_modifyitems(config, items):
    if USE_PG:
        return
    skip_pg = pytest.mark.skip(reason="requires PostgreSQL (set TEST_DATABASE_URL)")
    for item in items:
        if "pg_only" in item.keywords:
            item.add_marker(skip_pg)
