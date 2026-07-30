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

NEO4J_URL = os.getenv("NEO4J_URL")
USE_NEO4J = bool(NEO4J_URL)


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
def seeded_session(session) -> Session:
    """A session pre-seeded with the frozen AI-compute evidence slice."""
    from app.scripts.seed_ai_compute_case import seed

    seed(session)
    return session


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
    if not USE_PG:
        skip_pg = pytest.mark.skip(reason="requires PostgreSQL (set TEST_DATABASE_URL)")
        for item in items:
            if "pg_only" in item.keywords:
                item.add_marker(skip_pg)
    if not USE_NEO4J:
        skip_neo4j = pytest.mark.skip(reason="requires a live Neo4j (set NEO4J_URL)")
        for item in items:
            if "neo4j_only" in item.keywords:
                item.add_marker(skip_neo4j)


# ---------------------------------------------------------------------------
# Workbench read-API fixtures (Task 6)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(session):
    """A TestClient wired to the in-memory test session via get_db override."""
    from app.db import get_db
    from app.main import app

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def workbench_case(
    document_service,
    research_service,
    assessment_service,
    instrument_repository,
):
    """A complete, wired-up case for workbench read-API tests.

    Wires document -> span -> statement -> case -> thesis -> evidence link ->
    snapshot -> AI assessment, plus company (theme role on the case) -> stock ->
    valuation snapshot, and fund -> holding disclosure on the theme stock.
    """
    from dataclasses import dataclass

    version = document_service.freeze(
        raw=b"workbench source", source_url="https://example.test/wb"
    )
    span = document_service.add_span(
        document_version_id=version.id,
        locator={"page": 32, "table_row": 4},
        verbatim_text="财报第 32 页，表格第 4 行：CapEx 同比增长 40%",
    )
    statement = research_service.add_statement(
        span.id,
        "CapEx 同比增长 40%",
        kind="disclosed_fact",
        observed_period=date(2026, 3, 31),
    )
    case = research_service.add_case(
        title="AI compute demand", industry_topic="ai_compute", created_by="tester"
    )
    thesis = research_service.add_thesis(
        case.id, statement="GPU demand will grow", created_by="tester"
    )
    link = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=UTC)
    )
    ai_assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="evidence supports",
        gaps=["缺少下游需求传导证据"],
    )
    company = instrument_repository.add_company(
        code="600519", name="Mapped Corp", type="listed"
    )
    instrument_repository.add_theme_role(
        company_id=company.id,
        role="beneficiary",
        scope={"segment": "AI compute"},
        research_case_id=case.id,
        applicable_from=date(2026, 1, 1),
    )
    stock = instrument_repository.add_stock(
        company_id=company.id, code="600519.SH", name="Mapped Corp", market="SSE"
    )
    valuation = instrument_repository.add_valuation_snapshot(
        stock_id=stock.id,
        as_of_date=date(2026, 6, 30),
        metric_name="PE_TTM",
        metric_value=Decimal("45.2"),
        source="wind",
        definition="总市值/近四月归母净利润",
    )
    fund_company = instrument_repository.add_fund_company(
        code="FC001", name="Alpha Fund Management"
    )
    fund = instrument_repository.add_fund(
        code="001001",
        name="Alpha Growth Fund",
        fund_type="equity",
        management_company_id=fund_company.id,
        scale=Decimal("1000000000"),
        establish_date=date(2015, 1, 1),
    )
    disclosure = instrument_repository.add_holding_disclosure(
        fund_id=fund.id,
        stock_id=stock.id,
        weight=Decimal("0.082"),
        report_period=date(2026, 3, 31),
        published_at=date(2026, 4, 22),
        source="fund-report-2026Q1",
    )

    @dataclass
    class WorkbenchFixture:
        case: object
        thesis: object
        statement: object
        link: object
        snapshot: object
        ai_assessment: object
        company: object
        stock: object
        valuation: object
        fund: object
        disclosure: object

    return WorkbenchFixture(
        case=case,
        thesis=thesis,
        statement=statement,
        link=link,
        snapshot=snapshot,
        ai_assessment=ai_assessment,
        company=company,
        stock=stock,
        valuation=valuation,
        fund=fund,
        disclosure=disclosure,
    )


# ---------------------------------------------------------------------------
# Graph-projection fixtures (Task 6, neo4j_only)
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_fixture(workbench_case):
    """Ledger data with a known evidence-link count for projection assertions."""
    from dataclasses import dataclass

    @dataclass
    class LedgerFixture:
        evidence_link_count: int

    return LedgerFixture(evidence_link_count=1)


@pytest.fixture
def projector(session):
    """A ProjectionService backed by a live Neo4j (neo4j_only tests only)."""
    from neo4j import GraphDatabase

    from app.services.projection import ProjectionService

    uri = os.getenv("NEO4J_URL")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "evidence-graph")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield ProjectionService(driver, session)
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Release-gate fixtures (Task 9)
# ---------------------------------------------------------------------------


class _SeededDatabase:
    """Wrapper around a seeded session for destructive data-mutation tests.

    Mutations use the raw DBAPI connection underlying the session's
    ``Connection`` (``session.connection().connection``) to bypass the
    SQLAlchemy ``before_execute`` append-only event guard, which would
    otherwise reject DELETE/UPDATE on immutable ledger tables.  Using the
    session's own DBAPI connection (rather than ``engine.raw_connection()``)
    ensures mutations are visible to subsequent ORM queries within the same
    uncommitted transaction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def dbapi_cursor(self):
        """Return a cursor on the session's underlying DBAPI connection."""
        return self._session.connection().connection.cursor()

    def delete_one_source_span(self) -> None:
        """Delete a source span that is part of the assessment traceability chain.

        Finds a span referenced by a SourceStatement and deletes it directly
        via DBAPI, bypassing the append-only guard.  Subsequent ORM queries
        will see the span as missing, breaking the assessment->span chain.
        """
        from sqlalchemy import select

        from app.models.ledger import SourceStatement

        statement = self._session.scalars(select(SourceStatement).limit(1)).first()
        assert statement is not None, "seeded database has no source statements"
        span_id_hex = statement.source_span_id.hex

        cur = self.dbapi_cursor()
        cur.execute("DELETE FROM source_spans WHERE id = ?", (span_id_hex,))
        assert cur.rowcount == 1, f"expected to delete 1 span, deleted {cur.rowcount}"
        self._session.expire_all()

    def insert_undated_disclosure(self) -> None:
        """Insert a HoldingDisclosure with NULL published_at via raw DBAPI.

        SQLite enforces NOT NULL at the column level, so the
        ``holding_disclosures`` table is recreated with ``published_at``
        nullable (preserving all existing rows), then a new row with
        ``published_at = NULL`` is inserted.  Individual ``execute()`` calls
        are used instead of ``executescript()`` (which would COMMIT the
        session's uncommitted seed data).  The service-layer validation
        that requires ``published_at`` is bypassed entirely.
        """
        from sqlalchemy import select

        from app.models.ledger import Fund, Stock

        import uuid

        fund = self._session.scalars(select(Fund).limit(1)).first()
        stock = self._session.scalars(select(Stock).limit(1)).first()
        assert fund is not None and stock is not None

        cur = self.dbapi_cursor()
        # Recreate the table with published_at nullable, preserving all data.
        # Use individual execute() calls — executescript() would COMMIT.
        cur.execute(
            "CREATE TABLE holding_disclosures_new ("
            "id CHAR(32) NOT NULL, "
            "fund_id CHAR(32) NOT NULL, "
            "stock_id CHAR(32) NOT NULL, "
            "weight NUMERIC NOT NULL, "
            "report_period DATE NOT NULL, "
            "published_at DATETIME, "
            "acquired_at DATETIME NOT NULL, "
            "source VARCHAR(128) NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "PRIMARY KEY (id), "
            "FOREIGN KEY(fund_id) REFERENCES funds (id), "
            "FOREIGN KEY(stock_id) REFERENCES stocks (id))"
        )
        cur.execute(
            "INSERT INTO holding_disclosures_new SELECT * FROM holding_disclosures"
        )
        cur.execute("DROP TABLE holding_disclosures")
        cur.execute(
            "ALTER TABLE holding_disclosures_new RENAME TO holding_disclosures"
        )
        cur.execute(
            "INSERT INTO holding_disclosures "
            "(id, fund_id, stock_id, weight, report_period, published_at, "
            "acquired_at, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                fund.id.hex,
                stock.id.hex,
                0.05,
                "2026-03-31",
                None,
                "2026-01-01 00:00:00.000000",
                "test-undated",
                "2026-01-01 00:00:00.000000",
            ),
        )
        self._session.expire_all()


@pytest.fixture
def release_gate(seeded_session):
    """A ReleaseGate backed by the seeded in-memory session (no projector)."""
    from scripts.verify_ai_compute_slice import ReleaseGate

    return ReleaseGate(seeded_session)


@pytest.fixture
def seeded_database(seeded_session):
    """A seeded-session wrapper for destructive data-mutation tests."""
    return _SeededDatabase(seeded_session)
