"""Extraction watermark derived from append-only AIRun audit records.

Every ``extract`` invocation already writes exactly one AIRun row carrying
``input_ref.document_version_id``, a status and a finish time.  Deriving the
watermark from that audit trail avoids a new table/migration and stays
honest: a version that was extracted successfully but produced zero
statements is distinguishable from one never attempted, and a failed run
leaves the version retryable.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import AIRun

ExtractionState = Literal["extracted", "extracted_empty", "failed", "not_attempted"]


def latest_extract_runs(
    session: Session, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AIRun]:
    """Latest extract AIRun per document version (by started_at)."""
    if not version_ids:
        return {}
    runs = session.scalars(
        select(AIRun)
        .where(AIRun.kind == "extract")
        .where(
            AIRun.input_ref["document_version_id"]
            .as_string()
            .in_([str(v) for v in version_ids])
        )
        .order_by(AIRun.started_at)
    ).all()
    latest: dict[uuid.UUID, AIRun] = {}
    for run in runs:  # ascending started_at — later rows overwrite earlier
        version_id = run.input_ref.get("document_version_id")
        if version_id:
            latest[uuid.UUID(version_id)] = run
    return latest


def successful_extract_version_ids(session: Session) -> set[uuid.UUID]:
    """Versions with at least one successful extract run (watermark high mark).

    Failed runs do NOT mark a version: it stays retryable.
    """
    runs = session.scalars(
        select(AIRun).where(AIRun.kind == "extract", AIRun.status == "success")
    ).all()
    ids: set[uuid.UUID] = set()
    for run in runs:
        version_id = run.input_ref.get("document_version_id")
        if version_id:
            ids.add(uuid.UUID(version_id))
    return ids


def extraction_state(
    *, statement_count: int, latest_run: AIRun | None
) -> ExtractionState:
    """Classify one version's extraction watermark.

    - ``extracted``: statements exist (output is what ultimately matters);
    - ``extracted_empty``: latest run succeeded but produced no statements —
      do not re-extract, this is the defect-3 fix;
    - ``failed``: latest run failed — retryable;
    - ``not_attempted``: no extract run recorded.
    """
    if statement_count > 0:
        return "extracted"
    if latest_run is None:
        return "not_attempted"
    if latest_run.status == "success":
        return "extracted_empty"
    return "failed"
