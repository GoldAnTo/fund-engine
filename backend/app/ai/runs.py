"""Helper for recording append-only ``AIRun`` audit rows.

Since ``ai_runs`` is an immutable (append-only) table, a run record is
inserted *once* at the end of each operation with the final status.  Failed
runs carry the error message; the original exception is re-raised so callers
retain control.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.ledger import AIRun


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
) -> AIRun:
    run = AIRun(
        kind=kind,
        model_version=model_version,
        prompt_version=prompt_version,
        input_ref=input_ref,
        output_summary=output_summary,
        status=status,
        error=error,
        started_at=started_at,
        finished_at=finished_at or datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    return run
