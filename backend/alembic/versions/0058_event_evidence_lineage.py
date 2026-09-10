"""Enforce relational lineage for automatic event evidence assignments.

Revision ID: 0058
Revises: 0057
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "event_research_scope_evidence_assignments"
_LINEAGE_TRIGGER = "validate_event_scope_automatic_assignment_lineage"
_SQLITE_UPDATE_TRIGGER = "no_update_event_research_scope_evidence_assignments"
_SQLITE_DELETE_TRIGGER = "no_delete_event_research_scope_evidence_assignments"
_PARENT_TRIGGER_FUNCTION = "validate_event_scope_automatic_assignment_parent_lineage"
_LINEAGE_PARENTS = (
    (
        "acquisition_jobs",
        "job",
        (
            "id",
            "tenant_id",
            "research_case_id",
            "thesis_id",
            "research_run_id",
            "request_snapshot",
        ),
    ),
    (
        "automatic_admission_decisions",
        "decision",
        ("id", "job_id", "outcome"),
    ),
    (
        "evidence_links",
        "evidence",
        ("id", "automatic_admission_decision_id", "review_state", "thesis_id"),
    ),
    (
        "acquisition_query_plans",
        "plan",
        ("id", "acquisition_job_id", "series_id", "goal_id"),
    ),
    (
        "acquisition_series",
        "series",
        (
            "id",
            "tenant_id",
            "research_case_id",
            "research_run_id",
            "scope_version_id",
            "thesis_id",
            "goal_id",
        ),
    ),
    (
        "event_research_scope_versions",
        "scope",
        ("id", "research_case_id"),
    ),
    ("research_runs", "run", ("id", "research_case_id")),
    ("theses", "thesis", ("id", "research_case_id", "statement")),
    (
        "case_tenant_admissions",
        "tenant",
        ("research_case_id", "tenant_id"),
    ),
    (
        "event_research_scope_factors",
        "factor",
        ("id", "scope_version_id", "statement"),
    ),
)


def _parent_trigger_name(short_name: str, operation: str) -> str:
    return f"validate_event_scope_lineage_{short_name}_{operation}"


def _postgres_lineage_predicate(row: str) -> str:
    return f"""
    EXISTS (
        SELECT 1
        FROM automatic_admission_decisions d
        JOIN evidence_links e
          ON e.id = {row}.evidence_link_id
         AND e.automatic_admission_decision_id = d.id
         AND e.review_state = 'automatically_admitted'
        JOIN acquisition_jobs j ON j.id = d.job_id
        JOIN acquisition_query_plans p ON p.acquisition_job_id = j.id
        JOIN acquisition_series s ON s.id = p.series_id
        JOIN event_research_scope_versions sv ON sv.id = {row}.scope_version_id
        JOIN research_runs r ON r.id = {row}.research_run_id
        JOIN theses t ON t.id = e.thesis_id
        WHERE d.id = {row}.automatic_admission_decision_id
          AND d.outcome = 'admitted'
          AND j.research_run_id = {row}.research_run_id
          AND j.thesis_id = e.thesis_id
          AND j.research_case_id = sv.research_case_id
          AND j.research_case_id = r.research_case_id
          AND j.research_case_id = t.research_case_id
          AND s.tenant_id = j.tenant_id
          AND s.research_case_id = j.research_case_id
          AND s.research_run_id = {row}.research_run_id
          AND s.scope_version_id = {row}.scope_version_id
          AND s.thesis_id = e.thesis_id
          AND s.goal_id = {row}.acquisition_goal_id
          AND p.goal_id = {row}.acquisition_goal_id
          AND j.request_snapshot->>'goal_id' = {row}.acquisition_goal_id
          AND j.request_snapshot->>'research_run_id' = {row}.research_run_id::text
          AND j.request_snapshot->>'scope_version_id' = {row}.scope_version_id::text
          AND {row}.automatic_provenance_json->>'goal_id' = {row}.acquisition_goal_id
          AND {row}.automatic_provenance_json->>'job_id' = j.id::text
          AND {row}.automatic_provenance_json->>'admission_decision_id' = d.id::text
          AND EXISTS (
              SELECT 1 FROM case_tenant_admissions cta
              WHERE cta.research_case_id = j.research_case_id
                AND cta.tenant_id = j.tenant_id
          )
          AND (
              (
                  {row}.automatic_provenance_json->>'mapping_scope' = 'factor'
                  AND {row}.disposition = 'mapped'
                  AND {row}.factor_statement = t.statement
                  AND EXISTS (
                      SELECT 1 FROM event_research_scope_factors f
                      WHERE f.scope_version_id = {row}.scope_version_id
                        AND f.statement = {row}.factor_statement
                  )
              ) OR (
                  {row}.automatic_provenance_json->>'mapping_scope' = 'event'
                  AND {row}.disposition = 'unmapped'
                  AND {row}.factor_statement IS NULL
                  AND {row}.acquisition_goal_id =
                      'event:' || j.research_case_id::text || ':alternative_explanation'
              )
          )
    )
    """


def _sqlite_lineage_predicate(row: str) -> str:
    return f"""
    EXISTS (
        SELECT 1
        FROM automatic_admission_decisions d
        JOIN evidence_links e
          ON e.id = {row}.evidence_link_id
         AND e.automatic_admission_decision_id = d.id
         AND e.review_state = 'automatically_admitted'
        JOIN acquisition_jobs j ON j.id = d.job_id
        JOIN acquisition_query_plans p ON p.acquisition_job_id = j.id
        JOIN acquisition_series s ON s.id = p.series_id
        JOIN event_research_scope_versions sv ON sv.id = {row}.scope_version_id
        JOIN research_runs r ON r.id = {row}.research_run_id
        JOIN theses t ON t.id = e.thesis_id
        WHERE d.id = {row}.automatic_admission_decision_id
          AND d.outcome = 'admitted'
          AND j.research_run_id = {row}.research_run_id
          AND j.thesis_id = e.thesis_id
          AND j.research_case_id = sv.research_case_id
          AND j.research_case_id = r.research_case_id
          AND j.research_case_id = t.research_case_id
          AND s.tenant_id = j.tenant_id
          AND s.research_case_id = j.research_case_id
          AND s.research_run_id = {row}.research_run_id
          AND s.scope_version_id = {row}.scope_version_id
          AND s.thesis_id = e.thesis_id
          AND s.goal_id = {row}.acquisition_goal_id
          AND p.goal_id = {row}.acquisition_goal_id
          AND json_extract(j.request_snapshot, '$.goal_id') = {row}.acquisition_goal_id
          AND replace(json_extract(j.request_snapshot, '$.research_run_id'), '-', '') = {row}.research_run_id
          AND replace(json_extract(j.request_snapshot, '$.scope_version_id'), '-', '') = {row}.scope_version_id
          AND json_extract({row}.automatic_provenance_json, '$.goal_id') = {row}.acquisition_goal_id
          AND replace(json_extract({row}.automatic_provenance_json, '$.job_id'), '-', '') = j.id
          AND replace(json_extract({row}.automatic_provenance_json, '$.admission_decision_id'), '-', '') = d.id
          AND EXISTS (
              SELECT 1 FROM case_tenant_admissions cta
              WHERE cta.research_case_id = j.research_case_id
                AND cta.tenant_id = j.tenant_id
          )
          AND (
              (
                  json_extract({row}.automatic_provenance_json, '$.mapping_scope') = 'factor'
                  AND {row}.disposition = 'mapped'
                  AND {row}.factor_statement = t.statement
                  AND EXISTS (
                      SELECT 1 FROM event_research_scope_factors f
                      WHERE f.scope_version_id = {row}.scope_version_id
                        AND f.statement = {row}.factor_statement
                  )
              ) OR (
                  json_extract({row}.automatic_provenance_json, '$.mapping_scope') = 'event'
                  AND {row}.disposition = 'unmapped'
                  AND {row}.factor_statement IS NULL
                  AND {row}.acquisition_goal_id =
                      'event:' || lower(
                          substr(j.research_case_id, 1, 8) || '-' ||
                          substr(j.research_case_id, 9, 4) || '-' ||
                          substr(j.research_case_id, 13, 4) || '-' ||
                          substr(j.research_case_id, 17, 4) || '-' ||
                          substr(j.research_case_id, 21, 12)
                      ) || ':alternative_explanation'
              )
          )
    )
    """


def _reject_invalid_existing_rows() -> None:
    bind = op.get_bind()
    predicate = (
        _postgres_lineage_predicate("a")
        if bind.dialect.name == "postgresql"
        else _sqlite_lineage_predicate("a")
    )
    invalid = bind.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_TABLE} a "
        f"WHERE a.assignment_kind = 'automatic' AND NOT ({predicate})"
    ).scalar_one()
    if invalid:
        raise RuntimeError(
            "cannot upgrade 0058 while automatic evidence lineage is invalid"
        )


def _create_postgres_triggers() -> None:
    predicate = _postgres_lineage_predicate("NEW")
    op.get_bind().exec_driver_sql(
        f"""
        CREATE FUNCTION {_LINEAGE_TRIGGER}()
        RETURNS trigger AS $$
        DECLARE
            lineage_thesis_id evidence_links.thesis_id%%TYPE;
            lineage_job_id acquisition_jobs.id%%TYPE;
            lineage_case_id acquisition_jobs.research_case_id%%TYPE;
            lineage_tenant_id acquisition_jobs.tenant_id%%TYPE;
        BEGIN
            IF NEW.assignment_kind = 'automatic' THEN
                PERFORM 1
                FROM event_research_scope_versions
                WHERE id = NEW.scope_version_id
                FOR SHARE;

                PERFORM 1
                FROM research_runs
                WHERE id = NEW.research_run_id
                FOR SHARE;

                SELECT thesis_id INTO lineage_thesis_id
                FROM evidence_links
                WHERE id = NEW.evidence_link_id
                FOR SHARE;

                SELECT job_id INTO lineage_job_id
                FROM automatic_admission_decisions
                WHERE id = NEW.automatic_admission_decision_id
                FOR SHARE;

                SELECT research_case_id, tenant_id
                INTO lineage_case_id, lineage_tenant_id
                FROM acquisition_jobs
                WHERE id = lineage_job_id
                FOR SHARE;

                PERFORM 1
                FROM acquisition_query_plans
                WHERE acquisition_job_id = lineage_job_id
                FOR SHARE;

                PERFORM 1
                FROM acquisition_series
                WHERE id IN (
                    SELECT series_id
                    FROM acquisition_query_plans
                    WHERE acquisition_job_id = lineage_job_id
                )
                FOR SHARE;

                PERFORM 1
                FROM theses
                WHERE id = lineage_thesis_id
                FOR SHARE;

                PERFORM 1
                FROM case_tenant_admissions
                WHERE research_case_id = lineage_case_id
                  AND tenant_id = lineage_tenant_id
                FOR SHARE;

                IF NEW.automatic_provenance_json->>'mapping_scope' = 'factor' THEN
                    PERFORM 1
                    FROM event_research_scope_factors
                    WHERE scope_version_id = NEW.scope_version_id
                      AND statement = NEW.factor_statement
                    FOR SHARE;
                END IF;

                IF NOT ({predicate}) THEN
                    RAISE EXCEPTION 'automatic evidence assignment lineage mismatch';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.get_bind().exec_driver_sql(
        f"CREATE TRIGGER {_LINEAGE_TRIGGER} BEFORE INSERT OR UPDATE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_LINEAGE_TRIGGER}();"
    )
    parent_predicate = _postgres_lineage_predicate("a")
    op.get_bind().exec_driver_sql(
        f"""
        CREATE FUNCTION {_PARENT_TRIGGER_FUNCTION}()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {_TABLE} a
                WHERE a.assignment_kind = 'automatic'
                  AND NOT ({parent_predicate})
            ) THEN
                RAISE EXCEPTION 'automatic evidence assignment parent lineage mismatch';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table, short_name, columns in _LINEAGE_PARENTS:
        update_trigger = _parent_trigger_name(short_name, "update")
        delete_trigger = _parent_trigger_name(short_name, "delete")
        op.get_bind().exec_driver_sql(
            f"CREATE TRIGGER {update_trigger} AFTER UPDATE OF {', '.join(columns)} "
            f"ON {table} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION {_PARENT_TRIGGER_FUNCTION}();"
        )
        op.get_bind().exec_driver_sql(
            f"CREATE TRIGGER {delete_trigger} AFTER DELETE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {_PARENT_TRIGGER_FUNCTION}();"
        )


def _create_sqlite_triggers() -> None:
    predicate = _sqlite_lineage_predicate("NEW")
    op.get_bind().exec_driver_sql(
        f"""
        CREATE TRIGGER {_LINEAGE_TRIGGER}
        BEFORE INSERT ON {_TABLE}
        FOR EACH ROW WHEN NEW.assignment_kind = 'automatic'
        BEGIN
            SELECT CASE WHEN NOT ({predicate})
                THEN RAISE(ABORT, 'automatic evidence assignment lineage mismatch')
            END;
        END;
        """
    )
    parent_predicate = _sqlite_lineage_predicate("a")
    for table, short_name, columns in _LINEAGE_PARENTS:
        for operation, event in (
            ("update", f"UPDATE OF {', '.join(columns)}"),
            ("delete", "DELETE"),
        ):
            trigger_name = _parent_trigger_name(short_name, operation)
            op.get_bind().exec_driver_sql(
                f"""
                CREATE TRIGGER {trigger_name}
                AFTER {event} ON {table}
                FOR EACH ROW BEGIN
                    SELECT CASE WHEN EXISTS (
                        SELECT 1 FROM {_TABLE} a
                        WHERE a.assignment_kind = 'automatic'
                          AND NOT ({parent_predicate})
                    ) THEN RAISE(
                        ABORT,
                        'automatic evidence assignment parent lineage mismatch'
                    ) END;
                END;
                """
            )
    op.get_bind().exec_driver_sql(
        f"""
        CREATE TRIGGER {_SQLITE_UPDATE_TRIGGER}
        BEFORE UPDATE ON {_TABLE}
        FOR EACH ROW BEGIN
            SELECT RAISE(ABORT, 'event evidence assignments are append-only');
        END;
        """
    )
    op.get_bind().exec_driver_sql(
        f"""
        CREATE TRIGGER {_SQLITE_DELETE_TRIGGER}
        BEFORE DELETE ON {_TABLE}
        FOR EACH ROW BEGIN
            SELECT RAISE(ABORT, 'event evidence assignments are append-only');
        END;
        """
    )


def upgrade() -> None:
    _reject_invalid_existing_rows()
    if op.get_bind().dialect.name == "postgresql":
        _create_postgres_triggers()
    else:
        _create_sqlite_triggers()


def downgrade() -> None:
    populated = op.get_bind().execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_TABLE} "
            "WHERE assignment_kind = 'automatic'"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "cannot downgrade 0058 while automatic evidence provenance exists"
        )
    if op.get_bind().dialect.name == "postgresql":
        for table, short_name, _columns in _LINEAGE_PARENTS:
            op.get_bind().exec_driver_sql(
                f"DROP TRIGGER IF EXISTS "
                f"{_parent_trigger_name(short_name, 'update')} ON {table}"
            )
            op.get_bind().exec_driver_sql(
                f"DROP TRIGGER IF EXISTS "
                f"{_parent_trigger_name(short_name, 'delete')} ON {table}"
            )
        op.get_bind().exec_driver_sql(
            f"DROP FUNCTION IF EXISTS {_PARENT_TRIGGER_FUNCTION}()"
        )
        op.get_bind().exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {_LINEAGE_TRIGGER} ON {_TABLE}"
        )
        op.get_bind().exec_driver_sql(
            f"DROP FUNCTION IF EXISTS {_LINEAGE_TRIGGER}()"
        )
    else:
        for _table, short_name, _columns in _LINEAGE_PARENTS:
            op.get_bind().exec_driver_sql(
                f"DROP TRIGGER IF EXISTS "
                f"{_parent_trigger_name(short_name, 'update')}"
            )
            op.get_bind().exec_driver_sql(
                f"DROP TRIGGER IF EXISTS "
                f"{_parent_trigger_name(short_name, 'delete')}"
            )
        op.get_bind().exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {_LINEAGE_TRIGGER}"
        )
        op.get_bind().exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {_SQLITE_UPDATE_TRIGGER}"
        )
        op.get_bind().exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {_SQLITE_DELETE_TRIGGER}"
        )
