"""v1 causal-chain command endpoints (steps and edges under a thesis)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import CausalEdge, CausalStep, Thesis
from app.repositories.research import ResearchRepository
from app.schemas.v1.causal_commands import (
    CausalEdgeDTO,
    CausalStepDTO,
    CreateCausalEdgeRequest,
    CreateCausalStepRequest,
)
from app.services.research import ResearchService

router = APIRouter(tags=["causal-commands-v1"])


def _service(db: Session) -> ResearchService:
    return ResearchService(ResearchRepository(db))


def _get_thesis(db: Session, thesis_id: uuid.UUID) -> Thesis:
    thesis = db.get(Thesis, thesis_id)
    if thesis is None:
        raise NotFoundError("Thesis", str(thesis_id))
    return thesis


@router.post(
    "/theses/{thesis_id}/causal-steps",
    response_model=CausalStepDTO,
    status_code=201,
)
def create_causal_step(
    thesis_id: uuid.UUID,
    payload: CreateCausalStepRequest,
    db: Session = Depends(get_db),
) -> CausalStep:
    thesis = _get_thesis(db, thesis_id)
    step = translate_validation(
        _service(db).add_causal_step,
        thesis,
        description=payload.description,
        sequence=payload.sequence,
    )
    commit_or_rollback(db)
    return step


@router.post(
    "/theses/{thesis_id}/causal-edges",
    response_model=CausalEdgeDTO,
    status_code=201,
)
def create_causal_edge(
    thesis_id: uuid.UUID,
    payload: CreateCausalEdgeRequest,
    db: Session = Depends(get_db),
) -> CausalEdge:
    thesis = _get_thesis(db, thesis_id)
    source_step = db.get(CausalStep, payload.source_step_id)
    if source_step is None:
        raise NotFoundError("CausalStep", str(payload.source_step_id))
    target_step = db.get(CausalStep, payload.target_step_id)
    if target_step is None:
        raise NotFoundError("CausalStep", str(payload.target_step_id))

    edge = translate_validation(
        _service(db).add_causal_edge,
        thesis,
        source_step=source_step,
        target_step=target_step,
        rationale=payload.rationale,
        creator_type=payload.creator_type,
    )
    commit_or_rollback(db)
    return edge
