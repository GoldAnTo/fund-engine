"""Add immutable research-object aliases.

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_TABLE = "uw_research_object_aliases"


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
            "normalized_alias = lower(trim(alias))",
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
        _TABLE,
        ["normalized_alias"],
    )

    dialect_name = op.get_bind().dialect.name
    if dialect_name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER no_update_{_TABLE}
            BEFORE UPDATE ON {_TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END;
        """)
        op.execute(f"""
            CREATE TRIGGER no_delete_{_TABLE}
            BEFORE DELETE ON {_TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END;
        """)
        return
    if dialect_name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_update_{_TABLE} BEFORE UPDATE ON {_TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{_TABLE} BEFORE DELETE ON {_TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{_TABLE} ON {_TABLE};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{_TABLE} ON {_TABLE};")
    elif dialect_name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{_TABLE};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{_TABLE};")
    op.drop_index("ix_uw_object_alias_normalized", table_name=_TABLE)
    op.drop_table(_TABLE)
