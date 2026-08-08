from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.models.report_research import ReportResearchScopeVersion
from app.queries.report_wiki import ReportWikiQueries


def _config(database_url: str) -> Config:
    backend = Path(__file__).parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_sqlite_upgrade_from_initial_ledger_to_document_blobs_and_safe_downgrade(
    monkeypatch, tmp_path
) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    # alembic/env.py reads this module value while configuring the migration
    # context; keep the test's real SQLite database authoritative.
    import app.db

    monkeypatch.setattr(app.db, "DATABASE_URL", database_url)
    config = _config(database_url)

    command.upgrade(config, "0001")
    command.upgrade(config, "0025")

    engine = sa.create_engine(database_url, future=True)
    with engine.begin() as connection:
        table_names = set(sa.inspect(connection).get_table_names())
        assert "document_blobs" in table_names
        connection.execute(
            sa.text(
                """
                INSERT INTO document_versions
                (id, content_sha256, source_url, published_at, available_at,
                 acquired_at, parser_version, supersedes_id, natural_key,
                 title, byte_size, language, parse_state)
                VALUES
                (:id, :sha, :source_url, NULL, :available_at, :acquired_at,
                 'fixture', NULL, NULL, 'fixture', 1, NULL, 'success')
                """
            ),
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "sha": "a" * 64,
                "source_url": "report://pdf_upload/fixture",
                "available_at": "2026-08-01 00:00:00",
                "acquired_at": "2026-08-01 00:00:00",
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO document_blobs
                (id, document_version_id, storage_key, content_sha256,
                 byte_size, media_type, created_at)
                VALUES
                (:id, :document_version_id, 'sha256/aa/fixture', :sha,
                 1, 'application/pdf', :created_at)
                """
            ),
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "document_version_id": "11111111-1111-1111-1111-111111111111",
                "sha": "a" * 64,
                "created_at": "2026-08-01 00:00:00",
            },
        )

    with pytest.raises(RuntimeError, match="document_blobs contains 1"):
        command.downgrade(config, "0024")


def test_sqlite_upgrade_preserves_legacy_scope_visibility_without_rewriting_facts(
    monkeypatch, tmp_path
) -> None:
    """0035 must make legacy scopes readable from their migration-time horizon.

    The report and a valid, attached disclosure predate the migration.  Scope
    ``created_at`` deliberately remains the older 0032 backfill timestamp;
    using it as the current visibility horizon would incorrectly hide the
    disclosure.  The migration therefore records a separate, auditable cutoff
    while preserving all original source timestamps.
    """
    database_url = f"sqlite:///{tmp_path / 'legacy-scope.db'}"
    import app.db

    monkeypatch.setattr(app.db, "DATABASE_URL", database_url)
    config = _config(database_url)
    command.upgrade(config, "0034")

    ids = {
        name: uuid4().hex
        for name in ("case", "report", "disclosure", "scope", "span", "statement")
    }
    old_scope_at = "2026-08-01 08:00:00"
    disclosure_available_at = "2026-08-03 09:00:00"
    disclosure_linked_at = "2026-08-03 09:01:00"
    engine = sa.create_engine(database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO research_cases (id, title, industry_topic, created_at, created_by) "
                "VALUES (:id, '旧范围', 'TMT', :created_at, 'migration-test')"
            ),
            {"id": ids["case"], "created_at": old_scope_at},
        )
        for document_id, source_url, available_at in (
            (ids["report"], "https://broker.valid.cn/report", old_scope_at),
            (ids["disclosure"], "https://disclosure.cninfo.com.cn/fixture", disclosure_available_at),
        ):
            connection.execute(
                sa.text(
                    "INSERT INTO document_versions "
                    "(id, content_sha256, source_url, published_at, available_at, acquired_at, "
                    "parser_version, supersedes_id, natural_key, title, byte_size, language, parse_state) "
                    "VALUES (:id, :sha, :source_url, NULL, :available_at, :available_at, "
                    "'verified-v1', NULL, NULL, 'fixture', 1, NULL, 'success')"
                ),
                {
                    "id": document_id,
                    "sha": ("a" if document_id == ids["report"] else "b") * 64,
                    "source_url": source_url,
                    "available_at": available_at,
                },
            )
        for document_id, linked_at in (
            (ids["report"], old_scope_at),
            (ids["disclosure"], disclosure_linked_at),
        ):
            connection.execute(
                sa.text(
                    "INSERT INTO case_document_versions "
                    "(id, research_case_id, document_version_id, linked_at) "
                    "VALUES (:id, :case_id, :document_id, :linked_at)"
                ),
                {
                    "id": str(uuid4()),
                    "case_id": ids["case"],
                    "document_id": document_id,
                    "linked_at": linked_at,
                },
            )
        connection.execute(
            sa.text(
                "INSERT INTO report_research_scope_versions "
                "(id, research_case_id, document_version_id, version, changed_by, change_summary, "
                "created_at, research_question, factor_selection, evidence_plan) "
                "VALUES (:id, :case_id, :report_id, 1, 'migration-0032', '旧范围', :created_at, "
                "'验证旧范围', '[]', '[]')"
            ),
            {
                "id": ids["scope"],
                "case_id": ids["case"],
                "report_id": ids["report"],
                "created_at": old_scope_at,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO source_spans "
                "(id, document_version_id, locator, verbatim_text, text_sha256, context_hash, locator_v1) "
                "VALUES (:id, :document_id, '{\"page\": 1}', '公告：订单增长', NULL, NULL, NULL)"
            ),
            {"id": ids["span"], "document_id": ids["disclosure"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO source_statements "
                "(id, source_span_id, kind, normalized_text, observed_period, created_at) "
                "VALUES (:id, :span_id, 'disclosed_fact', '公告：订单增长', NULL, :created_at)"
            ),
            {
                "id": ids["statement"],
                "span_id": ids["span"],
                "created_at": disclosure_available_at,
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT visibility_cutoff_at, created_at FROM report_research_scope_versions "
                "WHERE id = :id"
            ),
            {"id": ids["scope"]},
        ).mappings().one()
        facts = connection.execute(
            sa.text(
                "SELECT available_at FROM document_versions WHERE id = :id "
                "UNION ALL SELECT linked_at FROM case_document_versions "
                "WHERE research_case_id = :case_id AND document_version_id = :id"
            ),
            {"id": ids["disclosure"], "case_id": ids["case"]},
        ).scalars().all()
    assert row["created_at"] == old_scope_at
    assert row["visibility_cutoff_at"] != old_scope_at
    assert facts == [disclosure_available_at, disclosure_linked_at]

    # Current application reads use the dedicated cutoff, so a valid disclosure
    # that existed before the upgrade is not lost merely because 0032 used an
    # older scope-backfill timestamp.
    with Session(engine) as session:
        scope = session.get(ReportResearchScopeVersion, UUID(ids["scope"]))
        assert scope is not None
        evidence = ReportWikiQueries(session)._independent_case_evidence(
            scope.research_case_id,
            scope.document_version_id,
            scope.visibility_cutoff_at,
        )
    assert [row.statement.id for row in evidence] == [UUID(ids["statement"])]
