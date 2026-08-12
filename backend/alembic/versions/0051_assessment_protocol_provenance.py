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
                "AND verification_rule_ids IS NOT NULL "
                "AND (research_protocol_status <> 'single_metric_monitoring' "
                "OR conclusion = 'insufficient_evidence'))",
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
            "AND verification_rule_ids IS NOT NULL "
            "AND (research_protocol_status <> 'single_metric_monitoring' "
            "OR conclusion = 'insufficient_evidence'))",
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
                JOIN theses t ON t.id = s.thesis_id
                JOIN outcome_binding_versions b
                  ON b.id = NEW.effective_binding_id AND b.thesis_id = s.thesis_id
                WHERE s.id = NEW.snapshot_id
                  AND t.research_protocol_required = 1
                  AND b.state = 'approved'
                  AND NOT EXISTS (
                    SELECT 1 FROM outcome_binding_versions newer
                    WHERE newer.thesis_id = b.thesis_id
                      AND (newer.created_at > b.created_at
                           OR (newer.created_at = b.created_at AND newer.id > b.id))
                  )
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
              SELECT RAISE(ABORT, 'assessment verification rules must be unique')
              WHERE (
                SELECT COUNT(*) FROM json_each(NEW.verification_rule_ids)
              ) <> (
                SELECT COUNT(DISTINCT replace(lower(j.value), '-', ''))
                FROM json_each(NEW.verification_rule_ids) j
                WHERE j.type = 'text'
              );
              SELECT RAISE(ABORT, 'assessment verification rule is not current in snapshot protocol scope')
              WHERE EXISTS (
                SELECT 1 FROM json_each(NEW.verification_rule_ids) j
                LEFT JOIN verification_rule_versions r
                  ON r.id = replace(lower(j.value), '-', '') AND j.type = 'text'
                LEFT JOIN mechanism_edge_versions e ON e.id = r.mechanism_edge_id
                WHERE r.id IS NULL
                   OR r.research_case_id IS NULL
                   OR r.research_case_id <> (
                     SELECT t.research_case_id
                     FROM evidence_snapshots s JOIN theses t ON t.id = s.thesis_id
                     WHERE s.id = NEW.snapshot_id
                   )
                   OR e.id IS NULL
                   OR e.template_version_id <> NEW.mechanism_template_version_id
                   OR EXISTS (
                     SELECT 1 FROM verification_rule_versions newer
                     WHERE newer.research_case_id = r.research_case_id
                       AND newer.mechanism_edge_id = r.mechanism_edge_id
                       AND (newer.created_at > r.created_at
                            OR (newer.created_at = r.created_at AND newer.id > r.id))
                   )
              );
              SELECT RAISE(ABORT, 'assessment verification rules omit current protocol rules')
              WHERE EXISTS (
                SELECT 1
                FROM evidence_snapshots s
                JOIN theses t ON t.id = s.thesis_id
                JOIN verification_rule_versions r
                  ON r.research_case_id = t.research_case_id
                JOIN mechanism_edge_versions e
                  ON e.id = r.mechanism_edge_id
                 AND e.template_version_id = NEW.mechanism_template_version_id
                WHERE s.id = NEW.snapshot_id
                  AND NOT EXISTS (
                    SELECT 1 FROM verification_rule_versions newer
                    WHERE newer.research_case_id = r.research_case_id
                      AND newer.mechanism_edge_id = r.mechanism_edge_id
                      AND (newer.created_at > r.created_at
                           OR (newer.created_at = r.created_at AND newer.id > r.id))
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.verification_rule_ids) j
                    WHERE j.type = 'text'
                      AND replace(lower(j.value), '-', '') = r.id
                  )
              );
              SELECT RAISE(ABORT, 'assessment protocol is missing a required verification rule')
              WHERE EXISTS (
                SELECT 1
                FROM mechanism_edge_versions e
                JOIN mechanism_node_versions target ON target.id = e.target_node_id
                WHERE e.template_version_id = NEW.mechanism_template_version_id
                  AND target.role IN ('required_for_outcome', 'required_for_attribution')
                  AND NOT EXISTS (
                    SELECT 1
                    FROM evidence_snapshots s
                    JOIN theses t ON t.id = s.thesis_id
                    JOIN verification_rule_versions r
                      ON r.research_case_id = t.research_case_id
                     AND r.mechanism_edge_id = e.id
                    WHERE s.id = NEW.snapshot_id
                      AND NOT EXISTS (
                        SELECT 1 FROM verification_rule_versions newer
                        WHERE newer.research_case_id = r.research_case_id
                          AND newer.mechanism_edge_id = r.mechanism_edge_id
                          AND (newer.created_at > r.created_at
                               OR (newer.created_at = r.created_at AND newer.id > r.id))
                      )
                  )
              );
              SELECT RAISE(ABORT, 'assessment protocol is missing a counter hypothesis')
              WHERE NOT EXISTS (
                SELECT 1
                FROM evidence_snapshots s
                JOIN theses t ON t.id = s.thesis_id
                JOIN verification_rule_versions r
                  ON r.research_case_id = t.research_case_id
                JOIN mechanism_edge_versions e
                  ON e.id = r.mechanism_edge_id
                 AND e.template_version_id = NEW.mechanism_template_version_id
                WHERE s.id = NEW.snapshot_id
                  AND r.contradiction_predicate <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM verification_rule_versions newer
                    WHERE newer.research_case_id = r.research_case_id
                      AND newer.mechanism_edge_id = r.mechanism_edge_id
                      AND (newer.created_at > r.created_at
                           OR (newer.created_at = r.created_at AND newer.id > r.id))
                  )
              );
              SELECT RAISE(ABORT, 'assessment research protocol status does not match current footprint')
              WHERE NEW.research_protocol_status <> (
                SELECT CASE
                  WHEN CASE json_type(b.entity_scope, '$.business_line')
                    WHEN 'true' THEN 1
                    WHEN 'integer' THEN json_extract(b.entity_scope, '$.business_line') <> 0
                    WHEN 'real' THEN json_extract(b.entity_scope, '$.business_line') <> 0
                    WHEN 'text' THEN json_extract(b.entity_scope, '$.business_line') <> ''
                    WHEN 'array' THEN json_array_length(b.entity_scope, '$.business_line') > 0
                    WHEN 'object' THEN EXISTS (
                      SELECT 1 FROM json_each(b.entity_scope, '$.business_line')
                    )
                    ELSE 0
                  END
                   AND (
                     SELECT COUNT(DISTINCT r.metric_definition_id)
                     FROM mechanism_edge_versions e
                     JOIN mechanism_node_versions target ON target.id = e.target_node_id
                     JOIN verification_rule_versions r ON r.mechanism_edge_id = e.id
                     WHERE e.template_version_id = NEW.mechanism_template_version_id
                       AND target.role IN ('required_for_outcome', 'required_for_attribution')
                       AND r.research_case_id = t.research_case_id
                       AND NOT EXISTS (
                         SELECT 1 FROM verification_rule_versions newer
                         WHERE newer.research_case_id = r.research_case_id
                           AND newer.mechanism_edge_id = r.mechanism_edge_id
                           AND (newer.created_at > r.created_at
                                OR (newer.created_at = r.created_at AND newer.id > r.id))
                       )
                   ) < 2
                  THEN 'single_metric_monitoring'
                  ELSE 'ready'
                END
                FROM evidence_snapshots s
                JOIN theses t ON t.id = s.thesis_id
                JOIN outcome_binding_versions b ON b.id = NEW.effective_binding_id
                WHERE s.id = NEW.snapshot_id
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
            JOIN theses t ON t.id = s.thesis_id
            JOIN outcome_binding_versions b
              ON b.id = NEW.effective_binding_id AND b.thesis_id = s.thesis_id
            WHERE s.id = NEW.snapshot_id
              AND t.research_protocol_required IS TRUE
              AND b.state = 'approved'
              AND NOT EXISTS (
                SELECT 1 FROM outcome_binding_versions newer
                WHERE newer.thesis_id = b.thesis_id
                  AND (newer.created_at, newer.id) > (b.created_at, b.id)
              )
          ) THEN RAISE EXCEPTION 'assessment binding does not match snapshot thesis' USING ERRCODE = '23514'; END IF;
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
          ) THEN RAISE EXCEPTION 'assessment template is not current for snapshot case' USING ERRCODE = '23514'; END IF;
          IF json_typeof(NEW.verification_rule_ids) <> 'array' THEN
            RAISE EXCEPTION 'assessment verification rules must be a JSON array' USING ERRCODE = '23514';
          END IF;
          IF json_array_length(NEW.verification_rule_ids) <> (
            SELECT COUNT(DISTINCT lower(j.value))
            FROM json_array_elements_text(NEW.verification_rule_ids) j(value)
          ) THEN RAISE EXCEPTION 'assessment verification rules must be unique' USING ERRCODE = '23514'; END IF;
          IF EXISTS (
            SELECT 1 FROM json_array_elements_text(NEW.verification_rule_ids) j(value)
            LEFT JOIN verification_rule_versions r
              ON r.id::text = lower(j.value)
            LEFT JOIN mechanism_edge_versions e ON e.id = r.mechanism_edge_id
            WHERE r.id IS NULL
               OR r.research_case_id IS DISTINCT FROM (
                 SELECT t.research_case_id
                 FROM evidence_snapshots s JOIN theses t ON t.id = s.thesis_id
                 WHERE s.id = NEW.snapshot_id
               )
               OR e.id IS NULL
               OR e.template_version_id <> NEW.mechanism_template_version_id
               OR EXISTS (
                 SELECT 1 FROM verification_rule_versions newer
                 WHERE newer.research_case_id = r.research_case_id
                   AND newer.mechanism_edge_id = r.mechanism_edge_id
                   AND (newer.created_at, newer.id) > (r.created_at, r.id)
               )
          ) THEN RAISE EXCEPTION 'assessment verification rule is not current in snapshot protocol scope' USING ERRCODE = '23514'; END IF;
          IF EXISTS (
            SELECT 1
            FROM evidence_snapshots s
            JOIN theses t ON t.id = s.thesis_id
            JOIN verification_rule_versions r
              ON r.research_case_id = t.research_case_id
            JOIN mechanism_edge_versions e
              ON e.id = r.mechanism_edge_id
             AND e.template_version_id = NEW.mechanism_template_version_id
            WHERE s.id = NEW.snapshot_id
              AND NOT EXISTS (
                SELECT 1 FROM verification_rule_versions newer
                WHERE newer.research_case_id = r.research_case_id
                  AND newer.mechanism_edge_id = r.mechanism_edge_id
                  AND (newer.created_at, newer.id) > (r.created_at, r.id)
              )
              AND NOT EXISTS (
                SELECT 1 FROM json_array_elements_text(NEW.verification_rule_ids) j(value)
                WHERE lower(j.value) = r.id::text
              )
          ) THEN RAISE EXCEPTION 'assessment verification rules omit current protocol rules' USING ERRCODE = '23514'; END IF;
          IF EXISTS (
            SELECT 1
            FROM mechanism_edge_versions e
            JOIN mechanism_node_versions target ON target.id = e.target_node_id
            WHERE e.template_version_id = NEW.mechanism_template_version_id
              AND target.role IN ('required_for_outcome', 'required_for_attribution')
              AND NOT EXISTS (
                SELECT 1
                FROM evidence_snapshots s
                JOIN theses t ON t.id = s.thesis_id
                JOIN verification_rule_versions r
                  ON r.research_case_id = t.research_case_id
                 AND r.mechanism_edge_id = e.id
                WHERE s.id = NEW.snapshot_id
                  AND NOT EXISTS (
                    SELECT 1 FROM verification_rule_versions newer
                    WHERE newer.research_case_id = r.research_case_id
                      AND newer.mechanism_edge_id = r.mechanism_edge_id
                      AND (newer.created_at, newer.id) > (r.created_at, r.id)
                  )
              )
          ) THEN RAISE EXCEPTION 'assessment protocol is missing a required verification rule' USING ERRCODE = '23514'; END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM evidence_snapshots s
            JOIN theses t ON t.id = s.thesis_id
            JOIN verification_rule_versions r
              ON r.research_case_id = t.research_case_id
            JOIN mechanism_edge_versions e
              ON e.id = r.mechanism_edge_id
             AND e.template_version_id = NEW.mechanism_template_version_id
            WHERE s.id = NEW.snapshot_id
              AND r.contradiction_predicate <> ''
              AND NOT EXISTS (
                SELECT 1 FROM verification_rule_versions newer
                WHERE newer.research_case_id = r.research_case_id
                  AND newer.mechanism_edge_id = r.mechanism_edge_id
                  AND (newer.created_at, newer.id) > (r.created_at, r.id)
              )
          ) THEN RAISE EXCEPTION 'assessment protocol is missing a counter hypothesis' USING ERRCODE = '23514'; END IF;
          IF NEW.research_protocol_status <> (
            SELECT CASE
              WHEN COALESCE(
                (b.entity_scope::jsonb -> 'business_line') NOT IN (
                  'null'::jsonb,
                  'false'::jsonb,
                  '0'::jsonb,
                  '""'::jsonb,
                  '[]'::jsonb,
                  '{}'::jsonb
                ),
                FALSE
              )
               AND (
                 SELECT COUNT(DISTINCT r.metric_definition_id)
                 FROM mechanism_edge_versions e
                 JOIN mechanism_node_versions target ON target.id = e.target_node_id
                 JOIN verification_rule_versions r ON r.mechanism_edge_id = e.id
                 WHERE e.template_version_id = NEW.mechanism_template_version_id
                   AND target.role IN ('required_for_outcome', 'required_for_attribution')
                   AND r.research_case_id = t.research_case_id
                   AND NOT EXISTS (
                     SELECT 1 FROM verification_rule_versions newer
                     WHERE newer.research_case_id = r.research_case_id
                       AND newer.mechanism_edge_id = r.mechanism_edge_id
                       AND (newer.created_at, newer.id) > (r.created_at, r.id)
                   )
               ) < 2
              THEN 'single_metric_monitoring'
              ELSE 'ready'
            END
            FROM evidence_snapshots s
            JOIN theses t ON t.id = s.thesis_id
            JOIN outcome_binding_versions b ON b.id = NEW.effective_binding_id
            WHERE s.id = NEW.snapshot_id
          ) THEN RAISE EXCEPTION 'assessment research protocol status does not match current footprint' USING ERRCODE = '23514'; END IF;
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
