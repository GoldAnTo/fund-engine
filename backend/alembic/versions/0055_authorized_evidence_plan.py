"""Freeze human-authorized preparation plans."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

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
_MIGRATION_REASON = "preparation_authorized_plan_migration_required"
_ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_for_review")
_ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_for_review")
_ACTIVE_TASK_STATUSES = ("queued", "running", "blocked")


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
    runs = sa.table(
        "research_runs",
        sa.column("id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("stop_reason", sa.Text()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    jobs = sa.table(
        "jobs",
        sa.column("kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("cancel_requested", sa.Boolean()),
        sa.column("target_type", sa.String()),
        sa.column("target_id", sa.Uuid()),
        sa.column("step", sa.String()),
        sa.column("error", sa.Text()),
        sa.column("finished_at", sa.DateTime(timezone=True)),
    )
    tasks = sa.table(
        "research_tasks",
        sa.column("run_id", sa.Uuid()),
        sa.column("status", sa.String()),
        sa.column("stage", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    run_events = sa.table(
        "research_run_events",
        sa.column("id", sa.Uuid()),
        sa.column("run_id", sa.Uuid()),
        sa.column("seq", sa.Integer()),
        sa.column("stage", sa.String()),
        sa.column("status", sa.String()),
        sa.column("message", sa.Text()),
        sa.column("payload_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    join_condition = sa.and_(
        artifacts.c.research_preparation_id == preparations.c.id,
        artifacts.c.kind == "evidence_acquisition_plan",
        artifacts.c.state == "current",
    )
    rows = list(bind.execute(
        sa.select(
            preparations.c.id,
            preparations.c.research_run_id,
            artifacts.c.payload,
        )
        .select_from(preparations.outerjoin(artifacts, join_condition))
        .where(preparations.c.status == "authorized")
    ))
    for preparation_id, run_id, payload in rows:
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
            # An old queued/in-flight run must never be executable after its
            # authorization is revoked, so terminalize its operational handoff
            # in the same migration transaction before clearing the link.
            now = datetime.now(timezone.utc)
            if run_id is not None:
                cancelled = bind.execute(
                    runs.update()
                    .where(runs.c.id == run_id)
                    .where(runs.c.status.in_(_ACTIVE_RUN_STATUSES))
                    .values(
                        status="cancelled",
                        stage="stopped",
                        stop_reason=_MIGRATION_REASON,
                        updated_at=now,
                    )
                )
                bind.execute(
                    tasks.update()
                    .where(tasks.c.run_id == run_id)
                    .where(tasks.c.status.in_(_ACTIVE_TASK_STATUSES))
                    .values(status="cancelled", stage="stopped", updated_at=now)
                )
                bind.execute(
                    jobs.update()
                    .where(jobs.c.kind == "research_run")
                    .where(jobs.c.target_type == "research_run")
                    .where(jobs.c.target_id == run_id)
                    .where(jobs.c.status.in_(_ACTIVE_JOB_STATUSES))
                    .values(
                        status="cancelled",
                        cancel_requested=True,
                        step="stopped",
                        error=_MIGRATION_REASON,
                        finished_at=now,
                    )
                )
                if cancelled.rowcount:
                    next_sequence = (
                        bind.scalar(
                            sa.select(sa.func.max(run_events.c.seq)).where(
                                run_events.c.run_id == run_id
                            )
                        )
                        or 0
                    ) + 1
                    bind.execute(
                        run_events.insert().values(
                            id=uuid.uuid4(),
                            run_id=run_id,
                            seq=next_sequence,
                            stage="stopped",
                            status="cancelled",
                            message="research run cancelled because its legacy authorization plan could not be frozen",
                            payload_json={"stop_reason": _MIGRATION_REASON},
                            created_at=now,
                        )
                    )
            bind.execute(
                preparations.update()
                .where(preparations.c.id == preparation_id)
                .values(
                    status="recoverable_failure",
                    research_run_id=None,
                    authorized_evidence_plan=None,
                    last_error_code=_MIGRATION_REASON,
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
