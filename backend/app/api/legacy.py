"""Legacy research-case read endpoints (pre-v1).

The workbench read model is assembled directly from the append-only ledger.
This route is kept for backward compatibility and is intentionally isolated
from the versioned /api/v1 contract. New consumers should use /api/v1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.services.operational_access import OperationalAccess
from app.services.workbench import WorkbenchService

router = APIRouter(
    prefix="/api/research-cases",
    tags=["research-cases-legacy"],
    dependencies=[Depends(require_research_actor)],
)


@router.get("/{case_id}/workbench")
def workbench(
    case_id: uuid.UUID,
    actor: Annotated[ResearchActor, Depends(require_research_actor)],
    db: Annotated[Session, Depends(get_db)],
    cutoff: Annotated[datetime | None, Query()] = None,
) -> dict:
    """Return the focused workbench read model for a research case.

    The optional ``cutoff`` controls point-in-time visibility of evidence links
    and holding disclosures.  When omitted, the current time is used.
    """
    OperationalAccess(db).require_compatibility_case(actor, case_id)
    service = WorkbenchService(db)
    response = service.load_workbench(case_id=case_id, cutoff=cutoff)
    if response is None:
        raise HTTPException(status_code=404, detail="research case not found")
    return response
