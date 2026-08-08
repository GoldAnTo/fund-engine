from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


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
