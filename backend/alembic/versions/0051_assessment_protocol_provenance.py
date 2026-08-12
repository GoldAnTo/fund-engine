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
                "(research_protocol_status IS NULL AND effective_binding_id IS NULL "
                "AND mechanism_template_version_id IS NULL AND verification_rule_ids IS NULL) "
                "OR (research_protocol_status IS NOT NULL "
                "AND research_protocol_status IN ('single_metric_monitoring', 'ready') "
                "AND effective_binding_id IS NOT NULL "
                "AND mechanism_template_version_id IS NOT NULL "
                "AND verification_rule_ids IS NOT NULL)",
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
            "(research_protocol_status IS NULL AND effective_binding_id IS NULL "
            "AND mechanism_template_version_id IS NULL AND verification_rule_ids IS NULL) "
            "OR (research_protocol_status IS NOT NULL "
            "AND research_protocol_status IN ('single_metric_monitoring', 'ready') "
            "AND effective_binding_id IS NOT NULL "
            "AND mechanism_template_version_id IS NOT NULL "
            "AND verification_rule_ids IS NOT NULL)",
        )
    _create_protocol_scope_trigger()


def _create_protocol_scope_trigger() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_ai_assessments_protocol_scope
            BEFORE INSERT ON ai_assessments
            WHEN NEW.research_protocol_status IS NOT NULL
            BEGIN
              SELECT RAISE(ABORT, 'assessment binding does not match snapshot thesis')
              WHERE NOT EXISTS (
                SELECT 1 FROM evidence_snapshots s
                JOIN outcome_binding_versions b
                  ON b.id = NEW.effective_binding_id AND b.thesis_id = s.thesis_id
                WHERE s.id = NEW.snapshot_id
              );
              SELECT RAISE(ABORT, 'assessment template is not current for snapshot case')
              WHERE NOT EXISTS (
                SELECT 1 FROM evidence_snapshots s
                JOIN theses t ON t.id = s.thesis_id
                JOIN case_mechanism_selection_versions c
                  ON c.research_case_id = t.research_case_id
                 AND c.template_version_id = NEW.mechanism_template_version_id
                WHERE s.id = NEW.snapshot_id
                  AND NOT EXISTS (
                    SELECT 1 FROM case_mechanism_selection_versions newer
                    WHERE newer.research_case_id = c.research_case_id
                      AND (newer.created_at > c.created_at
                           OR (newer.created_at = c.created_at AND newer.id > c.id))
                  )
              );
              SELECT RAISE(ABORT, 'assessment verification rules must be a JSON array')
              WHERE json_type(NEW.verification_rule_ids) <> 'array';
              SELECT RAISE(ABORT, 'assessment verification rule is outside snapshot protocol scope')
              WHERE EXISTS (
                SELECT 1 FROM json_each(NEW.verification_rule_ids) j
                LEFT JOIN verification_rule_versions r
                  ON r.id = replace(lower(j.value), '-', '')
                LEFT JOIN mechanism_edge_versions e ON e.id = r.mechanism_edge_id
                WHERE r.id IS NULL
                   OR r.research_case_id <> (
                     SELECT t.research_case_id
                     FROM evidence_snapshots s JOIN theses t ON t.id = s.thesis_id
                     WHERE s.id = NEW.snapshot_id
                   )
                   OR e.template_version_id <> NEW.mechanism_template_version_id
              );
            END
            """
        )
        return
    op.execute(
        """
        CREATE FUNCTION validate_ai_assessment_protocol_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.research_protocol_status IS NULL THEN RETURN NEW; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM evidence_snapshots s
            JOIN outcome_binding_versions b
              ON b.id = NEW.effective_binding_id AND b.thesis_id = s.thesis_id
            WHERE s.id = NEW.snapshot_id
          ) THEN RAISE EXCEPTION 'assessment binding does not match snapshot thesis'; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM evidence_snapshots s
            JOIN theses t ON t.id = s.thesis_id
            JOIN case_mechanism_selection_versions c
              ON c.research_case_id = t.research_case_id
             AND c.template_version_id = NEW.mechanism_template_version_id
            WHERE s.id = NEW.snapshot_id
              AND NOT EXISTS (
                SELECT 1 FROM case_mechanism_selection_versions newer
                WHERE newer.research_case_id = c.research_case_id
                  AND (newer.created_at, newer.id) > (c.created_at, c.id)
              )
          ) THEN RAISE EXCEPTION 'assessment template is not current for snapshot case'; END IF;
          IF json_typeof(NEW.verification_rule_ids) <> 'array' THEN
            RAISE EXCEPTION 'assessment verification rules must be a JSON array';
          END IF;
          IF EXISTS (
            SELECT 1 FROM json_array_elements_text(NEW.verification_rule_ids) j(value)
            LEFT JOIN verification_rule_versions r
              ON r.id::text = lower(j.value)
            LEFT JOIN mechanism_edge_versions e ON e.id = r.mechanism_edge_id
            WHERE r.id IS NULL
               OR r.research_case_id <> (
                 SELECT t.research_case_id
                 FROM evidence_snapshots s JOIN theses t ON t.id = s.thesis_id
                 WHERE s.id = NEW.snapshot_id
               )
               OR e.template_version_id <> NEW.mechanism_template_version_id
          ) THEN RAISE EXCEPTION 'assessment verification rule is outside snapshot protocol scope'; END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_assessments_protocol_scope
        BEFORE INSERT ON ai_assessments
        FOR EACH ROW EXECUTE FUNCTION validate_ai_assessment_protocol_scope()
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_ai_assessments_protocol_scope")
    else:
        op.execute(
            "DROP TRIGGER IF EXISTS trg_ai_assessments_protocol_scope "
            "ON ai_assessments"
        )
        op.execute("DROP FUNCTION IF EXISTS validate_ai_assessment_protocol_scope()")
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
