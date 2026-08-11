"""Add immutable filing-version metadata to historical holding disclosures.

Revision ID: 0050
Revises: 0049
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("holding_disclosures") as batch:
        batch.add_column(
            sa.Column(
                "filing_kind", sa.String(length=16), nullable=False, server_default="other"
            )
        )
        batch.add_column(
            sa.Column("supersedes_disclosure_id", sa.Uuid(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_holding_disclosures_filing_kind",
            "filing_kind IN ('quarterly', 'annual', 'correction', 'other')",
        )
        batch.create_foreign_key(
            "fk_holding_disclosures_supersedes_disclosure_id",
            "holding_disclosures",
            ["supersedes_disclosure_id"],
            ["id"],
        )
    # These fields are new metadata, not a rewrite of the frozen holding fact.
    # Recover the filing category from the immutable source title so existing
    # historical cases receive the same deterministic read rule as new intake.
    op.execute(
        """
        UPDATE holding_disclosures
        SET filing_kind = CASE
            WHEN COALESCE((SELECT title FROM document_versions
                           WHERE id = holding_disclosures.source_document_version_id), '') LIKE '%更正%'
                THEN 'correction'
            WHEN COALESCE((SELECT title FROM document_versions
                           WHERE id = holding_disclosures.source_document_version_id), '') LIKE '%年度报告%'
                THEN 'annual'
            WHEN COALESCE((SELECT title FROM document_versions
                           WHERE id = holding_disclosures.source_document_version_id), '') LIKE '%季度%'
                THEN 'quarterly'
            ELSE 'other'
        END
        """
    )
    # Link only an objectively earlier candidate: either a lower-priority
    # filing, or an older publication of the same kind.  Values and source
    # documents remain untouched; this is an append-only audit relationship.
    op.execute(
        """
        UPDATE holding_disclosures AS successor
        SET supersedes_disclosure_id = (
            SELECT predecessor.id
            FROM holding_disclosures AS predecessor
            WHERE predecessor.fund_id = successor.fund_id
              AND predecessor.stock_id = successor.stock_id
              AND predecessor.report_period = successor.report_period
              AND predecessor.id <> successor.id
              AND (
                CASE predecessor.filing_kind
                    WHEN 'correction' THEN 3 WHEN 'annual' THEN 2
                    WHEN 'quarterly' THEN 1 ELSE 0 END
                < CASE successor.filing_kind
                    WHEN 'correction' THEN 3 WHEN 'annual' THEN 2
                    WHEN 'quarterly' THEN 1 ELSE 0 END
                OR (
                    predecessor.filing_kind = successor.filing_kind
                    AND predecessor.published_at < successor.published_at
                )
              )
            ORDER BY
                CASE predecessor.filing_kind
                    WHEN 'correction' THEN 3 WHEN 'annual' THEN 2
                    WHEN 'quarterly' THEN 1 ELSE 0 END DESC,
                predecessor.published_at DESC,
                predecessor.created_at DESC,
                predecessor.id DESC
            LIMIT 1
        )
        WHERE supersedes_disclosure_id IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("holding_disclosures") as batch:
        batch.drop_constraint("fk_holding_disclosures_supersedes_disclosure_id", type_="foreignkey")
        batch.drop_constraint("ck_holding_disclosures_filing_kind", type_="check")
        batch.drop_column("supersedes_disclosure_id")
        batch.drop_column("filing_kind")
