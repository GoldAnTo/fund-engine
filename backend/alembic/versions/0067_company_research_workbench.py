"""Persist company-research preparation and immutable workbench artifacts.

Revision ID: 0067
Revises: 0066
"""
from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_PREPARATIONS = "uw_company_research_preparations"
_ARTIFACTS = "uw_company_research_artifact_versions"
_EVENTS = "uw_company_research_events"
_PREPARATION_STATUSES = (
    "queued",
    "preparing_sources",
    "awaiting_evidence_review",
    "building_model",
    "awaiting_judgment_review",
    "ready_to_freeze",
    "recoverable_failure",
    "blocked",
    "completed",
)
_ARTIFACT_KINDS = (
    "evidence_index",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "research_gaps",
    "judgment_context",
    "memo",
)


def _json_shape_constraint(column: str, shape: str, dialect: str) -> str:
    if dialect == "sqlite":
        return f"json_type({column}) = '{shape}'"
    if dialect == "postgresql":
        return f"json_typeof({column}) = '{shape}'"
    raise RuntimeError("company research workbench requires SQLite or PostgreSQL")


def _install_immutable_triggers(table_name: str, dialect_name: str) -> None:
    if dialect_name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER no_update_{table_name}
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        op.execute(f"""
            CREATE TRIGGER no_delete_{table_name}
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table_name: str, dialect_name: str) -> None:
    if dialect_name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name} ON {table_name};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name} ON {table_name};")
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name};")
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name};")


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    op.create_table(
        _PREPARATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=96), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{item}'" for item in _PREPARATION_STATUSES) + ")",
            name="ck_uw_company_research_preparation_status",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_uw_company_research_preparation_progress",
        ),
        sa.CheckConstraint(
            "attempt >= 1", name="ck_uw_company_research_preparation_attempt"
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_uw_company_research_preparation_request_hash",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["uw_research_projects.id"],
            name="fk_uw_company_research_preparation_project",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_uw_company_research_preparation_job"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_company_research_preparations"),
        sa.UniqueConstraint("project_id", name="uq_uw_company_research_preparation_project"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_uw_company_research_preparation_idempotency"
        ),
    )
    op.create_index(
        "ix_uw_company_research_preparation_job", _PREPARATIONS, ["job_id"]
    )
    op.create_table(
        _ARTIFACTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{item}'" for item in _ARTIFACT_KINDS) + ")",
            name="ck_uw_company_research_artifact_kind",
        ),
        sa.CheckConstraint("version >= 1", name="ck_uw_company_research_artifact_version"),
        sa.CheckConstraint(
            "length(input_hash) = 64", name="ck_uw_company_research_artifact_input_hash"
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_company_research_artifact_content_hash"
        ),
        sa.CheckConstraint(
            _json_shape_constraint("payload", "object", dialect_name),
            name="ck_uw_company_research_artifact_payload_shape",
        ),
        sa.CheckConstraint(
            _json_shape_constraint("source_refs", "array", dialect_name),
            name="ck_uw_company_research_artifact_source_refs_shape",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["uw_research_projects.id"],
            name="fk_uw_company_research_artifact_project",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], [f"{_ARTIFACTS}.id"],
            name="fk_uw_company_research_artifact_supersedes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_company_research_artifact_versions"),
        sa.UniqueConstraint(
            "project_id", "kind", "version", name="uq_uw_company_research_artifact_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_company_research_artifact_successor"
        ),
    )
    op.create_index(
        "ix_uw_company_research_artifact_project_kind",
        _ARTIFACTS,
        ["project_id", "kind"],
    )
    op.create_table(
        _EVENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("preparation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_company_research_event_content_hash"
        ),
        sa.CheckConstraint(
            _json_shape_constraint("payload", "object", dialect_name),
            name="ck_uw_company_research_event_payload_shape",
        ),
        sa.ForeignKeyConstraint(
            ["preparation_id"], [f"{_PREPARATIONS}.id"],
            name="fk_uw_company_research_event_preparation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_company_research_events"),
    )
    op.create_index(
        "ix_uw_company_research_event_preparation",
        _EVENTS,
        ["preparation_id", "created_at"],
    )
    _install_immutable_triggers(_ARTIFACTS, dialect_name)
    _install_immutable_triggers(_EVENTS, dialect_name)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(_EVENTS, dialect_name)
    _drop_immutable_triggers(_ARTIFACTS, dialect_name)
    op.drop_index("ix_uw_company_research_event_preparation", table_name=_EVENTS)
    op.drop_table(_EVENTS)
    op.drop_index("ix_uw_company_research_artifact_project_kind", table_name=_ARTIFACTS)
    op.drop_table(_ARTIFACTS)
    op.drop_index("ix_uw_company_research_preparation_job", table_name=_PREPARATIONS)
    op.drop_table(_PREPARATIONS)
