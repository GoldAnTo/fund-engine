"""Research-case read endpoints.

Exposes the focused workbench read model.  The workbench is assembled directly
from the append-only ledger; it never depends on the Neo4j projection and never
exposes a recommendation, target price, or buy/sell signal.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.workbench import WorkbenchService

router = APIRouter(prefix="/api/research-cases", tags=["research-cases"])


@router.get("/{case_id}/workbench")
def workbench(
    case_id: uuid.UUID,
    cutoff: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Return the focused workbench read model for a research case.

    The optional ``cutoff`` controls point-in-time visibility of evidence links
    and holding disclosures.  When omitted, the current time is used.
    """
    service = WorkbenchService(db)
    response = service.load_workbench(case_id=case_id, cutoff=cutoff)
    if response is None:
        raise HTTPException(status_code=404, detail="research case not found")
    return response
