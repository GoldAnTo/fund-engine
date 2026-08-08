"""Add immutable China industry-index facts for report market controls.

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = (
    "china_industry_indexes",
    "china_industry_index_memberships",
    "china_industry_index_snapshots",
)
_OBSERVATION_FK = "fk_report_market_observations_industry_index_snapshot"
_OBSERVATION_CHECK = "ck_report_market_industry_snapshot_source"
_OBSERVATION_CHECK_SQL = (
    "(kind = 'industry_control' AND status = 'verified' "
    "AND industry_index_snapshot_id IS NOT NULL "
    "AND stock_id IS NULL AND valuation_snapshot_id IS NULL) "
    "OR (kind = 'industry_control' AND status != 'verified' "
    "AND industry_index_snapshot_id IS NULL) "
    "OR (kind != 'industry_control' AND industry_index_snapshot_id IS NULL)"
)


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "china_industry_indexes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "code", name="uq_china_industry_index_provider_code"),
    )
    op.create_table(
        "china_industry_index_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "industry_index_id",
            sa.Uuid(),
            sa.ForeignKey("china_industry_indexes.id"),
            nullable=False,
        ),
        sa.Column("applicable_from", sa.Date(), nullable=True),
        sa.Column("applicable_to", sa.Date(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_china_industry_memberships_company_available",
        "china_industry_index_memberships",
        ["company_id", "available_at"],
    )
    op.create_index(
        "ix_china_industry_memberships_index_available",
        "china_industry_index_memberships",
        ["industry_index_id", "available_at"],
    )
    op.create_table(
        "china_industry_index_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "industry_index_id",
            sa.Uuid(),
            sa.ForeignKey("china_industry_indexes.id"),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("metric_value", sa.Numeric(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_china_industry_index_snapshots_metric_as_of",
        "china_industry_index_snapshots",
        ["industry_index_id", "metric_name", "as_of_date"],
    )
    op.create_index(
        "ix_china_industry_index_snapshots_availability",
        "china_industry_index_snapshots",
        ["industry_index_id", "as_of_date", "available_at"],
    )
    # ``batch_alter_table`` makes the column and its foreign key work on both
    # PostgreSQL and SQLite, where adding constraints requires a table rebuild.
    with op.batch_alter_table("report_market_observations") as batch:
        batch.add_column(
            sa.Column("industry_index_snapshot_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            _OBSERVATION_FK,
            "china_industry_index_snapshots",
            ["industry_index_snapshot_id"],
            ["id"],
        )
        batch.create_check_constraint(_OBSERVATION_CHECK, _OBSERVATION_CHECK_SQL)
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _IMMUTABLE_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {table} contains {count} immutable records"
            )
    count = bind.execute(
        sa.text(
            "SELECT count(*) FROM report_market_observations "
            "WHERE industry_index_snapshot_id IS NOT NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            "refusing to downgrade: report_market_observations references industry snapshots"
        )
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    with op.batch_alter_table("report_market_observations") as batch:
        batch.drop_constraint(_OBSERVATION_CHECK, type_="check")
        batch.drop_constraint(_OBSERVATION_FK, type_="foreignkey")
        batch.drop_column("industry_index_snapshot_id")
    op.drop_index(
        "ix_china_industry_index_snapshots_availability",
        table_name="china_industry_index_snapshots",
    )
    op.drop_index(
        "ix_china_industry_index_snapshots_metric_as_of",
        table_name="china_industry_index_snapshots",
    )
    op.drop_table("china_industry_index_snapshots")
    op.drop_index(
        "ix_china_industry_memberships_index_available",
        table_name="china_industry_index_memberships",
    )
    op.drop_index(
        "ix_china_industry_memberships_company_available",
        table_name="china_industry_index_memberships",
    )
    op.drop_table("china_industry_index_memberships")
    op.drop_table("china_industry_indexes")
