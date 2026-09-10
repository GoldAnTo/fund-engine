"""Release-frozen dossier schema, tested on an isolated temporary database."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

from alembic import command
from tests.test_research_team_migration import config

TABLES = (
    "company_studies",
    "company_study_activities",
    "company_study_revisions",
    "company_study_monitors",
    "company_study_requests",
)


def test_0076_is_frozen_and_database_guards_survive_upgrade_downgrade(tmp_path):
    cfg = config(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    revisions = {
        r.revision: r for r in ScriptDirectory.from_config(cfg).walk_revisions()
    }
    assert "0076" in revisions
    assert revisions["0076"].down_revision == "0075"
    source = Path(revisions["0076"].path).read_text()
    assert "app.models" not in source and "create_all" not in source
    command.upgrade(cfg, "0075")
    engine = sa.create_engine(cfg.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        before = set(sa.inspect(connection).get_table_names())
    command.upgrade(cfg, "0076")
    from uuid import UUID

    from app.services.company_study import CompanyStudyService
    from tests.test_company_study import BODY
    from tests.test_research_gateway_service import ALICE

    with sa.orm.Session(engine) as session:
        service = CompanyStudyService(session)
        study = service.create(ALICE, **BODY, idempotency_key="study")
        service.add_activity(
            ALICE,
            UUID(study["id"]),
            kind="baseline",
            text="建立研究基线",
            idempotency_key="activity",
        )
        service.configure_monitor(
            ALICE,
            UUID(study["id"]),
            status="active",
            frequency="daily",
            focus="财报",
            idempotency_key="monitor",
        )
    with engine.connect() as connection:
        assert set(TABLES) <= set(sa.inspect(connection).get_table_names())
        for table in [
            "company_studies",
            "company_study_activities",
            "company_study_monitors",
            "company_study_requests",
        ]:
            for statement in [
                f"DELETE FROM {table}",
                f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1",
            ]:
                with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
                    connection.execute(sa.text(statement))
        with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
            connection.execute(sa.text("UPDATE company_studies SET subject_id='other'"))
        with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
            connection.execute(
                sa.text("UPDATE company_study_activities SET prompt='rewritten'")
            )
        with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
            connection.execute(
                sa.text("UPDATE company_study_monitors SET focus='rewritten'")
            )
    command.downgrade(cfg, "0075")
    with engine.connect() as connection:
        assert set(sa.inspect(connection).get_table_names()) == before
    engine.dispose()


def test_bootstrap_rejects_corrupted_company_guard(tmp_path):
    from app.db_migrations import UnmanagedDatabaseSchemaError, upgrade_database_to_head

    url = f"sqlite:///{tmp_path / 'corrupt.sqlite'}"
    upgrade_database_to_head(url)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER cs_no_update_company_study_monitors"))
    with pytest.raises(UnmanagedDatabaseSchemaError, match="company study"):
        upgrade_database_to_head(url)
    engine.dispose()
