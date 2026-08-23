"""Seal candidate rows and add non-promoting review constraints.

Revision ID: 0063
Revises: 0062
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("uw_evidence_candidate_dossier_versions", recreate="always") as batch:
            batch.create_check_constraint("ck_uw_evidence_candidate_dossier_status", "status = 'candidate'")
        with op.batch_alter_table("uw_evidence_candidate_review_versions", recreate="always") as batch:
            batch.create_check_constraint(
                "ck_uw_evidence_candidate_review_identity_canonical",
                "reviewer_identity = trim(reviewer_identity) AND length(trim(reviewer_identity)) > 0",
            )
            batch.create_unique_constraint("uq_uw_evidence_candidate_review_role", ["dossier_id", "reviewer_role"])
            batch.create_unique_constraint("uq_uw_evidence_candidate_review_reviewer", ["dossier_id", "reviewer_identity"])
        return
    op.create_check_constraint("ck_uw_evidence_candidate_dossier_status", "uw_evidence_candidate_dossier_versions", "status = 'candidate'")
    op.create_check_constraint("ck_uw_evidence_candidate_review_identity_canonical", "uw_evidence_candidate_review_versions", "reviewer_identity = trim(reviewer_identity) AND length(trim(reviewer_identity)) > 0")
    op.create_unique_constraint("uq_uw_evidence_candidate_review_role", "uw_evidence_candidate_review_versions", ["dossier_id", "reviewer_role"])
    op.create_unique_constraint("uq_uw_evidence_candidate_review_reviewer", "uw_evidence_candidate_review_versions", ["dossier_id", "reviewer_identity"])


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("uq_uw_evidence_candidate_review_reviewer", "uw_evidence_candidate_review_versions", type_="unique")
        op.drop_constraint("uq_uw_evidence_candidate_review_role", "uw_evidence_candidate_review_versions", type_="unique")
        op.drop_constraint("ck_uw_evidence_candidate_review_identity_canonical", "uw_evidence_candidate_review_versions", type_="check")
        op.drop_constraint("ck_uw_evidence_candidate_dossier_status", "uw_evidence_candidate_dossier_versions", type_="check")
        return
    with op.batch_alter_table("uw_evidence_candidate_review_versions", recreate="always") as batch:
        batch.drop_constraint("uq_uw_evidence_candidate_review_reviewer", type_="unique")
        batch.drop_constraint("uq_uw_evidence_candidate_review_role", type_="unique")
        batch.drop_constraint("ck_uw_evidence_candidate_review_identity_canonical", type_="check")
    with op.batch_alter_table("uw_evidence_candidate_dossier_versions", recreate="always") as batch:
        batch.drop_constraint("ck_uw_evidence_candidate_dossier_status", type_="check")
