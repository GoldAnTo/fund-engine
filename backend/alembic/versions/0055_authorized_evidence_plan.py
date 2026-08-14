"""Freeze human-authorized preparation plans."""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


_PLAN_ITEM_KEYS = {
    "factor",
    "evidence_target",
    "allowed_source_roles",
    "priority",
    "stop_condition",
    "budget",
}


def _is_recoverable_plan(payload: object) -> bool:
    """Conservatively recognize a plan safe to freeze during migration."""
    if not isinstance(payload, dict) or set(payload) != {"items"}:
        return False
    items = payload["items"]
    if not isinstance(items, list) or not items:
        return False
    factors: set[str] = set()
    budget = 0
    for item in items:
        if not isinstance(item, dict) or set(item) != _PLAN_ITEM_KEYS:
            return False
        factor = item["factor"]
        if not isinstance(factor, str) or not factor.strip() or factor in factors:
            return False
        factors.add(factor)
        if any(
            not isinstance(item[field], str) or not item[field].strip()
            for field in ("evidence_target", "stop_condition")
        ):
            return False
        roles = item["allowed_source_roles"]
        if (
            not isinstance(roles, list)
            or not roles
            or any(not isinstance(role, str) or not role.strip() for role in roles)
        ):
            return False
        if item["priority"] not in {"high", "normal", "low"}:
            return False
        if type(item["budget"]) is not int or item["budget"] <= 0:
            return False
        budget += item["budget"]
    return budget <= 10000


def upgrade() -> None:
    # Adding a nullable column is supported directly by SQLite. Populate it
    # before installing the stronger check: otherwise a batch copy would see
    # legacy authorized rows with a NULL frozen plan and fail partway through.
    op.add_column(
        "research_preparations",
        sa.Column("authorized_evidence_plan", sa.JSON(none_as_null=True), nullable=True),
    )
    bind = op.get_bind()
    preparations = sa.table(
        "research_preparations",
        sa.column("id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("research_run_id", sa.Uuid()),
        sa.column("authorized_evidence_plan", sa.JSON(none_as_null=True)),
        sa.column("last_error_code", sa.String()),
    )
    artifacts = sa.table(
        "research_preparation_artifacts",
        sa.column("research_preparation_id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("state", sa.String()),
    )
    join_condition = sa.and_(
        artifacts.c.research_preparation_id == preparations.c.id,
        artifacts.c.kind == "evidence_acquisition_plan",
        artifacts.c.state == "current",
    )
    rows = list(bind.execute(
        sa.select(preparations.c.id, artifacts.c.payload)
        .select_from(preparations.outerjoin(artifacts, join_condition))
        .where(preparations.c.status == "authorized")
    ))
    for preparation_id, payload in rows:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if _is_recoverable_plan(payload):
            bind.execute(
                preparations.update()
                .where(preparations.c.id == preparation_id)
                .values(authorized_evidence_plan=payload)
            )
        else:
            # Preserve the Run and all audit rows, but require a human to
            # re-authorize a plan whose legacy snapshot cannot be trusted.
            bind.execute(
                preparations.update()
                .where(preparations.c.id == preparation_id)
                .values(
                    status="recoverable_failure",
                    research_run_id=None,
                    authorized_evidence_plan=None,
                    last_error_code="preparation_authorized_plan_migration_required",
                )
            )
    with op.batch_alter_table("research_preparations") as batch:
        batch.drop_constraint("ck_research_preparations_authorized_run", type_="check")
        batch.create_check_constraint(
            "ck_research_preparations_authorized_run",
            "(status = 'authorized' AND research_run_id IS NOT NULL AND "
            "authorized_evidence_plan IS NOT NULL) OR (status <> 'authorized' "
            "AND research_run_id IS NULL AND authorized_evidence_plan IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("research_preparations") as batch:
        batch.drop_constraint("ck_research_preparations_authorized_run", type_="check")
        batch.drop_column("authorized_evidence_plan")
        batch.create_check_constraint(
            "ck_research_preparations_authorized_run",
            "(status = 'authorized' AND research_run_id IS NOT NULL) OR "
            "(status <> 'authorized' AND research_run_id IS NULL)",
        )
