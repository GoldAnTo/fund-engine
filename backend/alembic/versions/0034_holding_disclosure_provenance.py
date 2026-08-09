"""Attach source and coverage provenance to fund holding disclosures.

Revision ID: 0034
Revises: 0033
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("holding_disclosures", sa.Column("source_document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=True))
    op.add_column("holding_disclosures", sa.Column("source_span_id", sa.Uuid(), sa.ForeignKey("source_spans.id"), nullable=True))
    op.add_column("holding_disclosures", sa.Column("provider_record_id", sa.Uuid(), sa.ForeignKey("provider_records.id"), nullable=True))
    op.add_column("holding_disclosures", sa.Column("coverage_status", sa.String(length=32), nullable=False, server_default="not_recorded"))
    op.create_check_constraint("ck_holding_disclosures_coverage_status", "holding_disclosures", "coverage_status IN ('complete', 'partial', 'not_recorded')")
    op.alter_column("holding_disclosures", "coverage_status", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_holding_disclosures_coverage_status", "holding_disclosures", type_="check")
    op.drop_column("holding_disclosures", "coverage_status")
    op.drop_column("holding_disclosures", "provider_record_id")
    op.drop_column("holding_disclosures", "source_span_id")
    op.drop_column("holding_disclosures", "source_document_version_id")
