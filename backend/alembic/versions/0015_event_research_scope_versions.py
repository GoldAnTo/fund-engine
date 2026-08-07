"""Persist append-only, versioned event-research factor scope snapshots.

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = (
    "event_research_scope_versions",
    "event_research_scope_factors",
    "event_research_scope_evidence_assignments",
)


def _add_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "event_research_scope_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_case_id",
            "version",
            name="uq_event_research_scope_versions_case_version",
        ),
    )
    op.create_index(
        "ix_event_research_scope_versions_case",
        "event_research_scope_versions",
        ["research_case_id"],
    )

    op.create_table(
        "event_research_scope_factors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "scope_version_id",
            "position",
            name="uq_event_research_scope_factors_position",
        ),
    )
    op.create_index(
        "ix_event_research_scope_factors_scope_version",
        "event_research_scope_factors",
        ["scope_version_id"],
    )

    op.create_table(
        "event_research_scope_evidence_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "evidence_link_id",
            sa.Uuid(),
            sa.ForeignKey("evidence_links.id"),
            nullable=False,
        ),
        sa.Column("factor_statement", sa.Text(), nullable=True),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope_version_id",
            "evidence_link_id",
            name="uq_event_research_scope_evidence_assignments_scope_link",
        ),
        sa.CheckConstraint(
            "disposition IN ('mapped', 'unmapped')",
            name="ck_event_research_scope_evidence_assignment_disposition",
        ),
    )
    op.create_index(
        "ix_event_research_scope_evidence_assignments_scope_version",
        "event_research_scope_evidence_assignments",
        ["scope_version_id"],
    )

    op.execute(
        """
        INSERT INTO event_research_scope_versions (
            id, research_case_id, version, changed_by, change_summary, created_at
        )
        SELECT
            research_case_id,
            research_case_id,
            1,
            MIN(created_by),
            'Migrated initial event research factors',
            MIN(created_at)
        FROM event_research_factor_drafts
        GROUP BY research_case_id
        """
    )
    op.execute(
        """
        INSERT INTO event_research_scope_factors (
            id, scope_version_id, statement, position
        )
        SELECT id, research_case_id, statement, position
        FROM event_research_factor_drafts
        """
    )
    op.execute(
        """
        INSERT INTO event_research_scope_evidence_assignments (
            id, scope_version_id, evidence_link_id, factor_statement, disposition, created_at
        )
        SELECT
            evidence_links.id,
            theses.research_case_id,
            evidence_links.id,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM event_research_factor_drafts
                    WHERE event_research_factor_drafts.research_case_id = theses.research_case_id
                    AND event_research_factor_drafts.statement = theses.statement
                ) THEN theses.statement
                ELSE NULL
            END,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM event_research_factor_drafts
                    WHERE event_research_factor_drafts.research_case_id = theses.research_case_id
                    AND event_research_factor_drafts.statement = theses.statement
                ) THEN 'mapped'
                ELSE 'unmapped'
            END,
            evidence_links.created_at
        FROM evidence_links
        JOIN theses ON theses.id = evidence_links.thesis_id
        JOIN event_research_scope_versions
            ON event_research_scope_versions.id = theses.research_case_id
        WHERE evidence_links.review_state = 'reviewed'
        """
    )

    for table in _IMMUTABLE_TABLES:
        _add_immutable_triggers(table)


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        _drop_immutable_triggers(table)
    op.drop_index(
        "ix_event_research_scope_evidence_assignments_scope_version",
        table_name="event_research_scope_evidence_assignments",
    )
    op.drop_table("event_research_scope_evidence_assignments")
    op.drop_index(
        "ix_event_research_scope_factors_scope_version",
        table_name="event_research_scope_factors",
    )
    op.drop_table("event_research_scope_factors")
    op.drop_index(
        "ix_event_research_scope_versions_case",
        table_name="event_research_scope_versions",
    )
    op.drop_table("event_research_scope_versions")
