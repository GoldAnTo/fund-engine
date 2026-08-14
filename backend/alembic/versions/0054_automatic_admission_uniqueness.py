"""Enforce one publication and quarantine per automatic decision lineage.

Revision ID: 0054
Revises: 0053
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_evidence_links_automatic_admission_decision",
        table_name="evidence_links",
    )
    op.create_index(
        "uq_evidence_links_automatic_admission_decision",
        "evidence_links",
        ["automatic_admission_decision_id"],
        unique=True,
        sqlite_where=sa.text("automatic_admission_decision_id IS NOT NULL"),
        postgresql_where=sa.text("automatic_admission_decision_id IS NOT NULL"),
    )
    op.create_index(
        "uq_acquisition_exceptions_automatic_quarantine",
        "acquisition_exceptions",
        ["job_id", "retrieval_artifact_id", "candidate_id", "reason_code"],
        unique=True,
        sqlite_where=sa.text(
            "reason_code = 'automatic_admission_quarantined'"
        ),
        postgresql_where=sa.text(
            "reason_code = 'automatic_admission_quarantined'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_acquisition_exceptions_automatic_quarantine",
        table_name="acquisition_exceptions",
    )
    op.drop_index(
        "uq_evidence_links_automatic_admission_decision",
        table_name="evidence_links",
    )
    op.create_index(
        "ix_evidence_links_automatic_admission_decision",
        "evidence_links",
        ["automatic_admission_decision_id"],
        unique=False,
    )
