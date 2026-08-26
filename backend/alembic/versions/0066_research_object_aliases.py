"""Add immutable research-object aliases and derived search terms.

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_ALIAS_TABLE = "uw_research_object_aliases"
_TERM_TABLE = "uw_research_object_search_terms"
_BACKFILL_BATCH_SIZE = 500


def _normalize_search_term(value: str) -> str:
    """Frozen 0066 normalization; do not replace with an application import."""
    return unicodedata.normalize("NFC", value.strip()).lower()


def _digest_search_term(normalized_value: str) -> str:
    """Frozen 0066 digest; do not replace with an application import."""
    return hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()


def _ascii_normalization_check(
    dialect_name: str, raw_column: str, normalized_column: str
) -> str:
    if dialect_name == "sqlite":
        return (
            f"length(CAST({raw_column} AS BLOB)) != length({raw_column}) OR "
            f"{raw_column} GLOB '*[^ -~]*' OR "
            f"{normalized_column} = lower(trim({raw_column}))"
        )
    if dialect_name == "postgresql":
        return (
            f"octet_length({raw_column}) != char_length({raw_column}) OR "
            f"{raw_column} !~ '^[ -~]*$' OR "
            f"{normalized_column} = lower(btrim({raw_column}))"
        )
    raise RuntimeError("research search terms require SQLite or PostgreSQL")


def _install_immutable_triggers(table_name: str, dialect_name: str) -> None:
    if dialect_name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER no_update_{table_name}
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END;
        """)
        op.execute(f"""
            CREATE TRIGGER no_delete_{table_name}
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END;
        """)
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table_name: str, dialect_name: str) -> None:
    if dialect_name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name} ON {table_name};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name} ON {table_name};")
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name};")
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name};")


def _backfill_search_terms() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    objects = sa.Table("uw_research_objects", metadata, autoload_with=connection)
    identities = sa.Table(
        "uw_object_identity_versions", metadata, autoload_with=connection
    )
    terms = sa.Table(_TERM_TABLE, metadata, autoload_with=connection)

    batch: list[dict[str, object]] = []

    def append_term(
        *,
        object_id: object,
        identity_version_id: object | None,
        term_kind: str,
        raw_value: str,
        created_at: object,
    ) -> None:
        normalized_value = _normalize_search_term(raw_value)
        batch.append(
            {
                "id": (
                    uuid4() if connection.dialect.name == "postgresql" else uuid4().hex
                ),
                "object_id": object_id,
                "identity_version_id": identity_version_id,
                "term_kind": term_kind,
                "raw_value": raw_value,
                "normalized_value": normalized_value,
                "normalized_digest": _digest_search_term(normalized_value),
                "created_at": created_at,
            }
        )
        if len(batch) >= _BACKFILL_BATCH_SIZE:
            connection.execute(terms.insert(), batch)
            batch.clear()

    object_rows = connection.execute(
        sa.select(
            objects.c.id,
            objects.c.external_key,
            objects.c.created_at,
        ).execution_options(stream_results=True)
    )
    for row in object_rows:
        append_term(
            object_id=row.id,
            identity_version_id=None,
            term_kind="external_key",
            raw_value=row.external_key,
            created_at=row.created_at,
        )
    identity_rows = connection.execute(
        sa.select(
            identities.c.id,
            identities.c.object_id,
            identities.c.canonical_name,
            identities.c.symbol,
            identities.c.created_at,
        ).execution_options(stream_results=True)
    )
    for row in identity_rows:
        append_term(
            object_id=row.object_id,
            identity_version_id=row.id,
            term_kind="canonical_name",
            raw_value=row.canonical_name,
            created_at=row.created_at,
        )
        if row.symbol is not None:
            append_term(
                object_id=row.object_id,
                identity_version_id=row.id,
                term_kind="symbol",
                raw_value=row.symbol,
                created_at=row.created_at,
            )
    if batch:
        connection.execute(terms.insert(), batch)


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    op.create_table(
        _ALIAS_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=160), nullable=False),
        sa.Column("normalized_alias", sa.String(length=160), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(alias)) > 0",
            name="ck_uw_object_alias_text",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_alias)) > 0 "
            "AND normalized_alias = trim(normalized_alias)",
            name="ck_uw_object_alias_normalized_text",
        ),
        sa.CheckConstraint(
            _ascii_normalization_check(dialect_name, "alias", "normalized_alias"),
            name="ck_uw_object_alias_normalized",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["uw_research_objects.id"],
            name="fk_uw_object_alias_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_research_object_aliases"),
        sa.UniqueConstraint(
            "object_id",
            "normalized_alias",
            name="uq_uw_object_alias_object_value",
        ),
    )
    op.create_index(
        "ix_uw_object_alias_normalized",
        _ALIAS_TABLE,
        ["normalized_alias"],
    )
    op.create_table(
        _TERM_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("identity_version_id", sa.Uuid(), nullable=True),
        sa.Column("term_kind", sa.String(length=24), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("normalized_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "term_kind IN ('external_key', 'canonical_name', 'symbol')",
            name="ck_uw_search_term_kind",
        ),
        sa.CheckConstraint(
            "(term_kind = 'external_key' AND identity_version_id IS NULL) OR "
            "(term_kind IN ('canonical_name', 'symbol') "
            "AND identity_version_id IS NOT NULL)",
            name="ck_uw_search_term_source",
        ),
        sa.CheckConstraint(
            "length(trim(raw_value)) > 0",
            name="ck_uw_search_term_raw_text",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_value)) > 0 "
            "AND normalized_value = trim(normalized_value)",
            name="ck_uw_search_term_normalized_text",
        ),
        sa.CheckConstraint(
            _ascii_normalization_check(dialect_name, "raw_value", "normalized_value"),
            name="ck_uw_search_term_normalized",
        ),
        sa.CheckConstraint(
            "length(normalized_digest) = 64 "
            "AND normalized_digest = lower(normalized_digest)",
            name="ck_uw_search_term_digest",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["uw_research_objects.id"],
            name="fk_uw_search_term_object",
        ),
        sa.ForeignKeyConstraint(
            ["identity_version_id"],
            ["uw_object_identity_versions.id"],
            name="fk_uw_search_term_identity",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_research_object_search_terms"),
        sa.UniqueConstraint(
            "identity_version_id",
            "term_kind",
            name="uq_uw_search_term_identity_kind",
        ),
    )
    op.create_index("ix_uw_search_term_identity", _TERM_TABLE, ["identity_version_id"])
    op.create_index(
        "ix_uw_search_term_normalized_digest",
        _TERM_TABLE,
        ["normalized_digest"],
    )
    op.create_index(
        "uq_uw_search_term_external_object",
        _TERM_TABLE,
        ["object_id"],
        unique=True,
        sqlite_where=sa.text("term_kind = 'external_key'"),
        postgresql_where=sa.text("term_kind = 'external_key'"),
    )
    _backfill_search_terms()
    _install_immutable_triggers(_ALIAS_TABLE, dialect_name)
    _install_immutable_triggers(_TERM_TABLE, dialect_name)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(_TERM_TABLE, dialect_name)
    _drop_immutable_triggers(_ALIAS_TABLE, dialect_name)
    op.drop_index("uq_uw_search_term_external_object", table_name=_TERM_TABLE)
    op.drop_index("ix_uw_search_term_normalized_digest", table_name=_TERM_TABLE)
    op.drop_index("ix_uw_search_term_identity", table_name=_TERM_TABLE)
    op.drop_table(_TERM_TABLE)
    op.drop_index("ix_uw_object_alias_normalized", table_name=_ALIAS_TABLE)
    op.drop_table(_ALIAS_TABLE)
