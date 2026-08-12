"""Freeze the research-protocol footprint on immutable AI assessments.

Revision ID: 0051
Revises: 0050
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ai_assessments") as batch:
            batch.add_column(
                sa.Column("research_protocol_status", sa.String(length=32), nullable=True)
            )
            batch.add_column(sa.Column("effective_binding_id", sa.Uuid(), nullable=True))
            batch.add_column(
                sa.Column("mechanism_template_version_id", sa.Uuid(), nullable=True)
            )
            batch.add_column(sa.Column("verification_rule_ids", sa.JSON(), nullable=True))
            batch.create_check_constraint(
                "ck_ai_assessments_research_protocol_status",
                "research_protocol_status IS NULL OR research_protocol_status IN "
                "('blocked', 'single_metric_monitoring', 'ready')",
            )
            batch.create_foreign_key(
                "fk_ai_assessments_effective_binding_id",
                "outcome_binding_versions",
                ["effective_binding_id"],
                ["id"],
            )
            batch.create_foreign_key(
                "fk_ai_assessments_mechanism_template_version_id",
                "mechanism_template_versions",
                ["mechanism_template_version_id"],
                ["id"],
            )
    else:
        op.add_column(
            "ai_assessments",
            sa.Column("research_protocol_status", sa.String(length=32), nullable=True),
        )
        op.add_column(
            "ai_assessments",
            sa.Column(
                "effective_binding_id",
                sa.Uuid(),
                sa.ForeignKey("outcome_binding_versions.id"),
                nullable=True,
            ),
        )
        op.add_column(
            "ai_assessments",
            sa.Column(
                "mechanism_template_version_id",
                sa.Uuid(),
                sa.ForeignKey("mechanism_template_versions.id"),
                nullable=True,
            ),
        )
        op.add_column(
            "ai_assessments",
            sa.Column("verification_rule_ids", sa.JSON(), nullable=True),
        )
        op.create_check_constraint(
            "ck_ai_assessments_research_protocol_status",
            "ai_assessments",
            "research_protocol_status IS NULL OR research_protocol_status IN "
            "('blocked', 'single_metric_monitoring', 'ready')",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ai_assessments") as batch:
            batch.drop_constraint(
                "fk_ai_assessments_mechanism_template_version_id",
                type_="foreignkey",
            )
            batch.drop_constraint(
                "fk_ai_assessments_effective_binding_id", type_="foreignkey"
            )
            batch.drop_constraint(
                "ck_ai_assessments_research_protocol_status", type_="check"
            )
            batch.drop_column("verification_rule_ids")
            batch.drop_column("mechanism_template_version_id")
            batch.drop_column("effective_binding_id")
            batch.drop_column("research_protocol_status")
    else:
        op.drop_constraint(
            "ck_ai_assessments_research_protocol_status",
            "ai_assessments",
            type_="check",
        )
        op.drop_column("ai_assessments", "verification_rule_ids")
        op.drop_column("ai_assessments", "mechanism_template_version_id")
        op.drop_column("ai_assessments", "effective_binding_id")
        op.drop_column("ai_assessments", "research_protocol_status")
