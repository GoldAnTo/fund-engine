"""Opt-in migration and concurrent dispatch audit on an explicitly disposable PG URL."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.db_migrations import upgrade_database_to_head
from app.services.company_study import CompanyStudyService
from tests.test_company_study import BODY
from tests.test_research_gateway_service import ALICE


@pytest.fixture
def postgres_study():
    raw = os.getenv("COMPANY_STUDY_AUDIT_PG_URL")
    if not raw:
        pytest.skip("requires explicitly disposable COMPANY_STUDY_AUDIT_PG_URL")
    schema = "company_study_audit_" + uuid4().hex
    admin = sa.create_engine(raw)
    with admin.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    rendered = raw + ("&" if "?" in raw else "?") + f"options=-csearch_path={schema}"
    url = sa.engine.make_url(rendered)
    engine = None
    try:
        upgrade_database_to_head(rendered)
        engine = sa.create_engine(url)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime(2026, 9, 9, 11, tzinfo=UTC)
        with factory() as session:
            service = CompanyStudyService(session, now=lambda: now)
            study = service.create(ALICE, **BODY, idempotency_key="study")
            activity = service.add_activity(
                ALICE,
                UUID(study["id"]),
                kind="event",
                text="核验新的公告",
                idempotency_key="event",
            )
        yield SimpleNamespace(
            factory=factory,
            engine=engine,
            now=now,
            study_id=UUID(study["id"]),
            activity_id=UUID(activity["id"]),
            url=rendered,
        )
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgresql_company_migration_guards_and_concurrent_claims(postgres_study):
    from tests.test_company_study_worker import (
        test_concurrent_workers_claim_and_schedule_each_window_once as audit,
    )

    audit(postgres_study)
    with postgres_study.engine.connect() as connection:
        with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
            connection.execute(
                sa.text("UPDATE company_study_activities SET prompt='rewritten'")
            )
        with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
            connection.execute(
                sa.text("UPDATE company_study_monitors SET focus='rewritten'")
            )
    # Repeated bootstrap authenticates frozen functions and their table owners.
    upgrade_database_to_head(postgres_study.url)


def test_postgresql_worker_recovers_committed_native_receipt_once(postgres_study):
    from tests.test_company_study_worker import (
        test_lost_receipt_recovers_same_gateway_intent_without_duplicate_native as audit,
    )

    audit(postgres_study)
