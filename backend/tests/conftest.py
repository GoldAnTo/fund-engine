import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from secrets import compare_digest

# Tests must never see a developer's local .env credentials.  Force test mode
# before importing application modules and discard every ambient setting that
# could construct a live provider.  Database service URLs are intentionally
# preserved so opt-in PostgreSQL and Neo4j integration tests still work.
os.environ["APP_ENV"] = "test"
for provider_env_name in (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "LLM_SEED",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "GILDATA_TOKEN",
):
    os.environ.pop(provider_env_name, None)

import pytest
from fastapi import Depends, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.security.principal import ResearchPrincipal
from app.db import get_db
from app.models.identity import ResearchUser

PG_URL = os.getenv("TEST_DATABASE_URL")
USE_PG = bool(PG_URL)

NEO4J_URL = os.getenv("NEO4J_URL")
USE_NEO4J = bool(NEO4J_URL)


@dataclass(frozen=True, slots=True)
class _LegacyTestResearchPrincipal(ResearchPrincipal):
    """Principal-shaped test double that preserves old audit assertions."""

    legacy_actor_id: str

    @property
    def actor(self) -> str:
        return f"user:{self.legacy_actor_id}"

    @property
    def server_actor(self) -> str:
        return self.actor


def _legacy_test_research_principal(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Test-only adapter for legacy opaque-token route fixtures.

    Production authentication never imports or calls this helper. It keeps old
    API tests isolated while they are migrated to generated OIDC credentials.
    """
    from app.errors import AuthenticationRequiredError, PermissionDeniedError

    if authorization is None:
        raise AuthenticationRequiredError("research credentials are required")
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if scheme.casefold() != "bearer" or not separator or not token:
        raise AuthenticationRequiredError("research bearer credentials are required")
    try:
        configured = json.loads(os.getenv("RESEARCH_TENANT_TOKENS", ""))
    except json.JSONDecodeError:
        configured = {}
    if not isinstance(configured, dict):
        configured = {}
    for expected_token, value in configured.items():
        if not isinstance(expected_token, str) or not compare_digest(
            token, expected_token
        ):
            continue
        tenant_id: str | None = None
        roles: list[str] = []
        actor_id: str | None = None
        if isinstance(value, str):
            tenant_id = value.strip()
        elif isinstance(value, dict):
            raw_tenant_id = value.get("tenant_id")
            raw_roles = value.get("roles", [])
            raw_actor_id = value.get("actor_id")
            if isinstance(raw_tenant_id, str):
                tenant_id = raw_tenant_id.strip()
            if isinstance(raw_roles, list) and all(
                isinstance(role, str) and role.strip() for role in raw_roles
            ):
                roles = [role.strip() for role in raw_roles]
            if isinstance(raw_actor_id, str) and raw_actor_id.strip():
                actor_id = raw_actor_id.strip()
        if not tenant_id:
            break
        # Opaque-token fixtures predate user-level grants and intentionally
        # model the one migration identity that may still see admitted legacy
        # Cases.  Production has no opaque-token path; new authorization tests
        # override this adapter with explicit principals and grants.
        if "tenant_administrator" not in roles:
            roles.append("tenant_administrator")
        subject = actor_id or f"token:{expected_token}"
        user_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"test-research-principal:{tenant_id}:{subject}"
        )
        now = datetime.now(UTC)
        user = db.get(ResearchUser, user_id)
        if user is None:
            user = ResearchUser(
                id=user_id,
                issuer="https://test-identity.invalid/realms/research",
                subject=subject,
                tenant_id=tenant_id,
                display_name=actor_id or tenant_id,
                normalized_email=None,
                active=True,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
        elif not user.active or user.tenant_id != tenant_id:
            raise PermissionDeniedError("test research user is not permitted")
        else:
            user.last_seen_at = now
            user.updated_at = now
        db.flush()
        # Migrate legacy opaque-token fixtures to the same explicit-grant
        # model used by production OIDC users.  This compatibility adapter is
        # test-only: zero-grant Cases admitted to this token's tenant become
        # owned by the deterministic test user before the route authorizes.
        # Cases that already have any grant remain private and untouched.
        from sqlalchemy import exists, select

        from app.models.identity import CaseAccessGrant
        from app.models.ledger import CaseTenantAdmission

        any_grant = exists(
            select(CaseAccessGrant.id).where(
                CaseAccessGrant.research_case_id
                == CaseTenantAdmission.research_case_id
            )
        )
        legacy_case_ids = list(
            db.scalars(
                select(CaseTenantAdmission.research_case_id).where(
                    CaseTenantAdmission.tenant_id == tenant_id,
                    ~any_grant,
                )
            )
        )
        for legacy_case_id in legacy_case_ids:
            db.add(
                CaseAccessGrant(
                    research_case_id=legacy_case_id,
                    user_id=user_id,
                    role="owner",
                    granted_by_principal_id="test:opaque-token-migration",
                    reason="test fixture migration to explicit Case ownership",
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()
        return _LegacyTestResearchPrincipal(
            user_id=user_id,
            issuer="https://test-identity.invalid/realms/research",
            subject=subject,
            tenant_id=tenant_id,
            display_name=actor_id or tenant_id,
            roles=frozenset(roles),
            expires_at=now + timedelta(hours=1),
            legacy_actor_id=actor_id or tenant_id,
        )
    raise PermissionDeniedError("research tenant is not permitted")


@pytest.fixture(autouse=True)
def _legacy_research_authentication_override():
    """Keep all opaque-token compatibility inside the test suite."""
    from app.api.v1.tenant_context import require_research_actor
    from app.main import app

    app.dependency_overrides[require_research_actor] = _legacy_test_research_principal
    try:
        yield
    finally:
        if (
            app.dependency_overrides.get(require_research_actor)
            is _legacy_test_research_principal
        ):
            app.dependency_overrides.pop(require_research_actor, None)


@pytest.fixture
def production_oidc_authentication():
    """Bypass the legacy override when a test exercises production OIDC."""
    from app.api.v1.tenant_context import require_research_actor
    from app.main import app

    previous = app.dependency_overrides.pop(require_research_actor, None)
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[require_research_actor] = previous


def _truncate_postgresql_tables(engine, base) -> None:
    """Reset test rows without dropping Alembic-managed append-only triggers."""
    table_names = ", ".join(
        f'"{table.name}"' for table in reversed(base.metadata.sorted_tables)
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
        )


@pytest.fixture(scope="session")
def engine():
    from app.models.ledger import Base

    if USE_PG:
        eng = create_engine(PG_URL, future=True)
        # 表与 append-only 触发器由 Alembic migration 管理；测试前 TRUNCATE
        # 清残留数据，保留结构与触发器（drop_all 会删触发器，故不用）。
        _truncate_postgresql_tables(eng, Base)
    else:
        eng = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
    yield eng
    if not USE_PG:
        Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine) -> Session:
    from app.models.ledger import Base

    SessionLocal = sessionmaker(bind=engine, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        # Services legitimately commit at external-provider boundaries.  A
        # rollback cannot undo those writes on the session-scoped SQLite
        # StaticPool, so rebuild its disposable schema between tests.  The
        # PostgreSQL fixture deliberately keeps Alembic-managed tables and
        # append-only triggers intact.
        if USE_PG:
            _truncate_postgresql_tables(engine, Base)
        else:
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine)


@pytest.fixture
def seeded_session(session) -> Session:
    """A session pre-seeded with the frozen AI-compute evidence slice."""
    from app.scripts.seed_ai_compute_case import seed
    from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, ResearchCase
    from sqlalchemy import select

    seed(session)
    # Event-slice protected Case reads need an explicit tenant admission even
    # for the legacy frozen fixture.  Do not teach route tests to rely on an
    # implicit "test" tenant.
    case = session.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    document_id = session.scalar(
        select(CaseDocumentVersion.document_version_id)
        .where(CaseDocumentVersion.research_case_id == case.id)
        .order_by(CaseDocumentVersion.linked_at)
    )
    assert document_id is not None
    session.add(CaseTenantAdmission(
        research_case_id=case.id,
        tenant_id="test-team",
        initial_document_version_id=document_id,
        admitted_by="test-fixture",
        admitted_at=case.created_at,
    ))
    session.flush()
    return session


@pytest.fixture
def document_service(session):
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    return DocumentService(DocumentRepository(session))


@pytest.fixture
def document(document_service):
    """A frozen document version available now."""
    suffix = uuid.uuid4().hex
    return document_service.freeze(
        raw=f"page one {suffix}".encode(),
        source_url=f"https://example.test/a/{suffix}",
    )


@pytest.fixture
def span(document, document_service):
    """A source span attached to the ``document`` version."""
    return document_service.add_span(
        document_version_id=document.id,
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
def thesis(research_service, research_case, document_service, document):
    document_service.attach_to_case(
        research_case_id=research_case.id,
        document_version_id=document.id,
    )
    return research_service.add_thesis(
        research_case.id, statement="GPU demand will grow", created_by="tester"
    )


@pytest.fixture
def statement(research_service, span):
    return research_service.add_statement(
        span.id, "预计需求增长", kind="research_opinion"
    )


@pytest.fixture
def assessment_service(research_repository, session):
    from app.services.assessment import AssessmentService

    return AssessmentService(research_repository, session)


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
def api_client(session, monkeypatch):
    """A TestClient wired to the in-memory test session via get_db override."""
    from app.db import get_db
    from app.main import app

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team"}')
    try:
        yield TestClient(app, headers={"Authorization": "Bearer test-tenant-token"})
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def workbench_case(
    session,
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
    from tests.tenant_admission import admit_case

    admit_case(session, case.id, document_version_id=version.id)
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
        if self._session.bind.dialect.name == "postgresql":
            # Disable the DB-level append-only trigger and drop the FK constraint
            # so the destructive DELETE succeeds; the transaction rolls back
            # (restoring trigger, constraint and row).
            cur.execute(
                "ALTER TABLE source_spans DISABLE TRIGGER no_delete_source_spans"
            )
            cur.execute(
                "ALTER TABLE source_statements "
                "DROP CONSTRAINT IF EXISTS source_statements_source_span_id_fkey"
            )
            cur.execute(
                "DELETE FROM source_spans WHERE id = %s",
                (str(statement.source_span_id),),
            )
        else:
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
        is_pg = self._session.bind.dialect.name == "postgresql"
        placeholder = "%s" if is_pg else "?"
        new_id = str(uuid.uuid4()) if is_pg else uuid.uuid4().hex
        fund_id = str(fund.id) if is_pg else fund.id.hex
        stock_id = str(stock.id) if is_pg else stock.id.hex

        if is_pg:
            # PostgreSQL: ALTER COLUMN to nullable in place (DDL is transactional).
            cur.execute(
                "ALTER TABLE holding_disclosures "
                "ALTER COLUMN published_at DROP NOT NULL"
            )
        else:
            # SQLite cannot ALTER COLUMN; recreate the table with published_at nullable.
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
                "source_document_version_id CHAR(32), "
                "source_span_id CHAR(32), "
                "provider_record_id CHAR(32), "
                "coverage_status VARCHAR(32) NOT NULL DEFAULT 'not_recorded', "
                "filing_kind VARCHAR(16) NOT NULL DEFAULT 'other', "
                "supersedes_disclosure_id CHAR(32), "
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
            "acquired_at, source, coverage_status, created_at) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            (
                new_id,
                fund_id,
                stock_id,
                0.05,
                "2026-03-31",
                None,
                "2026-01-01 00:00:00.000000",
                "test-undated",
                "not_recorded",
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


# ---------------------------------------------------------------------------
# Private-engine fixtures for command (write) endpoint tests.  Command
# endpoints COMMIT, so they must never share the session-scoped engine —
# committed rows would leak into other tests (e.g. the release gate's
# manifest-hash check counts seeded DocumentVersions exactly).
# ---------------------------------------------------------------------------


@pytest.fixture
def cmd_session():
    from app.models.ledger import Base

    eng = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(eng)


@pytest.fixture
def cmd_client(cmd_session):
    from app.db import get_db
    from app.main import app

    def _override_get_db():
        yield cmd_session

    app.dependency_overrides[get_db] = _override_get_db
    previous_tokens = os.environ.get("RESEARCH_TENANT_TOKENS")
    os.environ["RESEARCH_TENANT_TOKENS"] = (
        '{"test-tenant-token":{"tenant_id":"test-team",'
        '"roles":["metric_definition_administrator"]}}'
    )
    try:
        yield TestClient(app, headers={"Authorization": "Bearer test-tenant-token"})
    finally:
        app.dependency_overrides.pop(get_db, None)
        if previous_tokens is None:
            os.environ.pop("RESEARCH_TENANT_TOKENS", None)
        else:
            os.environ["RESEARCH_TENANT_TOKENS"] = previous_tokens


@pytest.fixture
def cmd_seeded(cmd_session):
    from app.scripts.seed_ai_compute_case import seed
    from app.models.identity import CaseAccessGrant, ResearchUser
    from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, ResearchCase
    from sqlalchemy import select

    seed(cmd_session)
    case = cmd_session.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    document_id = cmd_session.scalar(
        select(CaseDocumentVersion.document_version_id)
        .where(CaseDocumentVersion.research_case_id == case.id)
        .order_by(CaseDocumentVersion.linked_at)
    )
    assert document_id is not None
    cmd_session.add(CaseTenantAdmission(
        research_case_id=case.id,
        tenant_id="test-team",
        initial_document_version_id=document_id,
        admitted_by="test-fixture",
        admitted_at=case.created_at,
    ))
    user_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "test-research-principal:test-team:token:test-tenant-token",
    )
    now = datetime.now(UTC)
    cmd_session.add(
        ResearchUser(
            id=user_id,
            issuer="https://test-identity.invalid/realms/research",
            subject="token:test-tenant-token",
            tenant_id="test-team",
            display_name="test-team",
            normalized_email=None,
            active=True,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    cmd_session.add(
        CaseAccessGrant(
            research_case_id=case.id,
            user_id=user_id,
            role="owner",
            granted_by_principal_id="test:fixture",
            reason="legacy seeded Case test migration",
            created_at=now,
            updated_at=now,
        )
    )
    cmd_session.commit()
    return cmd_session
