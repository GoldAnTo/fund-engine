"""Freeze Case ownership of its initial tenant source admission.

Revision ID: 0039
Revises: 0038
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "report_case_initial_admissions"


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
            "document_source_record_id",
            sa.Uuid(),
            sa.ForeignKey("document_source_records.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_case_id", name="uq_report_case_initial_admissions_case"),
    )
    op.create_index("ix_report_case_initial_admissions_tenant", _TABLE, ["tenant_id"])
    op.create_index(
        "ix_report_case_initial_admissions_source_record",
        _TABLE,
        ["document_source_record_id"],
    )
    for action in ("update", "delete"):
        _trigger(action)
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER no_replace_{_TABLE} BEFORE INSERT ON {_TABLE} "
            f"FOR EACH ROW WHEN EXISTS (SELECT 1 FROM {_TABLE} WHERE id = NEW.id "
            "OR research_case_id = NEW.research_case_id) "
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
    op.drop_index("ix_report_case_initial_admissions_source_record", table_name=_TABLE)
    op.drop_index("ix_report_case_initial_admissions_tenant", table_name=_TABLE)
    op.drop_table(_TABLE)
