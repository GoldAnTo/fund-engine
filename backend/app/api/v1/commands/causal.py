"""v1 causal-chain command endpoints (steps and edges under a thesis)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.api.v1.tenant_context import require_research_tenant
from app.services.review_tenant_access import ReviewTenantAccess
from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import CausalEdge, CausalStep
from app.repositories.research import ResearchRepository
from app.schemas.v1.causal_commands import (
    CreateCausalEdgeRequest,
    CreatedCausalEdgeDTO,
    CreateCausalStepRequest,
    CreatedCausalStepDTO,
)
from app.services.research import ResearchService

router = APIRouter(tags=["causal-commands-v1"])


def _service(db: Session) -> ResearchService:
    return ResearchService(ResearchRepository(db))


@router.post(
    "/theses/{thesis_id}/causal-steps",
    response_model=CreatedCausalStepDTO,
    status_code=201,
)
def create_causal_step(
    thesis_id: uuid.UUID,
    payload: CreateCausalStepRequest,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
) -> CausalStep:
    thesis = ReviewTenantAccess(db).require_thesis(thesis_id, tenant_id)
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
    response_model=CreatedCausalEdgeDTO,
    status_code=201,
)
def create_causal_edge(
    thesis_id: uuid.UUID,
    payload: CreateCausalEdgeRequest,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
) -> CausalEdge:
    thesis = ReviewTenantAccess(db).require_thesis(thesis_id, tenant_id)
    source_step = db.get(CausalStep, payload.source_step_id)
    if source_step is None or source_step.thesis_id != thesis.id:
        raise NotFoundError("CausalStep", str(payload.source_step_id))
    target_step = db.get(CausalStep, payload.target_step_id)
    if target_step is None or target_step.thesis_id != thesis.id:
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
