"""Helper for recording append-only ``AIRun`` audit rows.

Since ``ai_runs`` is an immutable (append-only) table, a run record is
inserted *once* at the end of each operation with the final status.  Failed
runs carry the error message; the original exception is re-raised so callers
retain control.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ledger import AIRun
from app.ai.usage import current_usage


_research_context: ContextVar[dict[str, str] | None] = ContextVar("research_ai_audit_context", default=None)


@contextmanager
def research_audit_context(*, case_id: uuid.UUID, run_id: uuid.UUID | None, task_id: uuid.UUID | None = None, acquisition_job_id: uuid.UUID | None = None):
    """Attribute operations to the executing run, without guessing from a thesis."""
    context = {"research_case_id": str(case_id)}
    if run_id is not None:
        context["research_run_id"] = str(run_id)
    if acquisition_job_id is not None:
        context["acquisition_job_id"] = str(acquisition_job_id)
    if task_id is not None:
        context["research_task_id"] = str(task_id)
    token = _research_context.set(context)
    try:
        yield
    finally:
        _research_context.reset(token)


def record_run(
    session: Session,
    *,
    kind: str,
    model_version: str,
    prompt_version: str,
    input_ref: dict,
    output_summary: str,
    status: str,
    error: str | None = None,
    started_at: datetime,
    finished_at: datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> AIRun:
    run = AIRun(
        usage=current_usage(),
        id=run_id or uuid.uuid4(),
        kind=kind,
        model_version=model_version,
        prompt_version=prompt_version,
        input_ref={**input_ref, **(_research_context.get() or {})},
        output_summary=output_summary,
        status=status,
        error=error,
        started_at=started_at,
        finished_at=finished_at or datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    return run
