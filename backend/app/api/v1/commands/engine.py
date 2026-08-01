"""Engine commands (prototype 监测与更新 · AI RERUN).

Re-running the assess step for one thesis freezes a new snapshot and appends
a new provisional AIAssessment plus its AIRun audit record.  Nothing is
overwritten — the evolution shows up in the snapshot-compare view.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.api.v1.commands.common import commit_or_rollback
from app.db import get_db
from app.errors import NotFoundError
from app.schemas.v1.commands import RerunAssessmentDTO, RerunResponse

router = APIRouter(prefix="/theses", tags=["engine-commands-v1"])


@router.post(
    "/{thesis_id}/rerun",
    response_model=RerunResponse,
    status_code=status.HTTP_201_CREATED,
)
def rerun_assessment(
    thesis_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    client = LLMClient.from_env()
    try:
        assessment = AssessmentGenerator(client).generate(
            thesis_id, datetime.now(timezone.utc), db
        )
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    commit_or_rollback(db)
    return RerunResponse(
        thesis_id=str(thesis_id),
        mode="mock" if client._mock else client.model_version,
        assessment=RerunAssessmentDTO(
            id=str(assessment.id),
            snapshot_id=str(assessment.snapshot_id),
            conclusion=assessment.conclusion,
            rationale=assessment.rationale,
            gaps=[str(g) for g in assessment.gaps],
            displayed_as_provisional=assessment.displayed_as_provisional,
            created_at=assessment.created_at.isoformat(),
        ),
    )
