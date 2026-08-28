"""Version company research event hashes without downgrade inference.

Revision ID: 0069
Revises: 0068
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_EVENTS = "uw_company_research_events"
_CHECK = "ck_uw_company_research_event_hash_version"


def _drop_immutable_triggers(dialect_name: str) -> None:
    suffix = "" if dialect_name == "sqlite" else f" ON {_EVENTS}"
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{_EVENTS}{suffix}")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{_EVENTS}{suffix}")


def _install_immutable_triggers(dialect_name: str) -> None:
    if dialect_name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER no_update_{_EVENTS}
            BEFORE UPDATE ON {_EVENTS}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        op.execute(f"""
            CREATE TRIGGER no_delete_{_EVENTS}
            BEFORE DELETE ON {_EVENTS}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        return
    op.execute(
        f"CREATE TRIGGER no_update_{_EVENTS} BEFORE UPDATE ON {_EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{_EVENTS} BEFORE DELETE ON {_EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect_name)
    op.add_column(
        _EVENTS,
        sa.Column("hash_version", sa.Integer(), nullable=True),
    )
    op.execute(f"UPDATE {_EVENTS} SET hash_version = 1")
    if dialect_name == "sqlite":
        with op.batch_alter_table(_EVENTS, recreate="always") as batch:
            batch.alter_column("hash_version", nullable=False)
            batch.create_check_constraint(_CHECK, "hash_version IN (1, 2)")
    else:
        op.alter_column(_EVENTS, "hash_version", nullable=False)
        op.create_check_constraint(_CHECK, _EVENTS, "hash_version IN (1, 2)")
    _install_immutable_triggers(dialect_name)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect_name)
    if dialect_name == "sqlite":
        with op.batch_alter_table(_EVENTS, recreate="always") as batch:
            batch.drop_constraint(_CHECK, type_="check")
            batch.drop_column("hash_version")
    else:
        op.drop_constraint(_CHECK, _EVENTS, type_="check")
        op.drop_column(_EVENTS, "hash_version")
    _install_immutable_triggers(dialect_name)
