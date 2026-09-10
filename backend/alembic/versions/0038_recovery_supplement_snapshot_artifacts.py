"""Add case-local recovery artifacts for globally deduplicated document bytes.

Revision ID: 0038
Revises: 0037
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "recovery_supplement_snapshots"


def _trigger(action: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{_TABLE} BEFORE {action.upper()} ON {_TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
    elif dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER no_{action}_{_TABLE} BEFORE {action.upper()} ON {_TABLE} "
            "FOR EACH ROW BEGIN "
            f"SELECT RAISE(ABORT, '{_TABLE} is append-only'); END;"
        )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "original_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "source_contract_version_id",
            sa.Uuid(),
            sa.ForeignKey("source_contract_versions.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_identity", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("verbatim_text", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("claimed_page_reference", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recovery_supplement_snapshots_case", _TABLE, ["research_case_id"])
    op.create_index(
        "ix_recovery_supplement_snapshots_original_document",
        _TABLE,
        ["original_document_version_id"],
    )
    for action in ("update", "delete"):
        _trigger(action)
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER no_replace_{_TABLE} BEFORE INSERT ON {_TABLE} "
            f"FOR EACH ROW WHEN EXISTS (SELECT 1 FROM {_TABLE} WHERE id = NEW.id) "
            "BEGIN "
            f"SELECT RAISE(ABORT, '{_TABLE} is append-only'); END;"
        )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    if count:
        raise RuntimeError(f"refusing to downgrade: {_TABLE} contains {count} immutable records")
    if bind.dialect.name == "postgresql":
        for action in ("delete", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{_TABLE} ON {_TABLE};")
    elif bind.dialect.name == "sqlite":
        for action in ("replace", "delete", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{_TABLE};")
    op.drop_index("ix_recovery_supplement_snapshots_original_document", table_name=_TABLE)
    op.drop_index("ix_recovery_supplement_snapshots_case", table_name=_TABLE)
    op.drop_table(_TABLE)
