"""Legacy research-case read endpoints (pre-v1).

The workbench read model is assembled directly from the append-only ledger.
This route is kept for backward compatibility and is intentionally isolated
from the versioned /api/v1 contract. New consumers should use /api/v1.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
from app.services.workbench import WorkbenchService

router = APIRouter(prefix="/api/research-cases", tags=["research-cases-legacy"])


@router.get("/{case_id}/workbench")
def workbench(
    case_id: uuid.UUID,
    cutoff: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> dict:
    """Return the focused workbench read model for a research case.

    The optional ``cutoff`` controls point-in-time visibility of evidence links
    and holding disclosures.  When omitted, the current time is used.
    """
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    service = WorkbenchService(db)
    response = service.load_workbench(case_id=case_id, cutoff=cutoff)
    if response is None:
        raise HTTPException(status_code=404, detail="research case not found")
    return response
