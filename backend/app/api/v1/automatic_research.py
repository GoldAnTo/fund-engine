"""One-click automatic-research commands and progress read model."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import require_research_tenant
from app.db import get_db
from app.errors import ConflictError, NotFoundError
from app.models.acquisition import AcquisitionJob
from app.models.event_research import EventResearchBrief
from app.models.ledger import ResearchCase
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_monitor import ResearchRunEvent
from app.queries.automatic_research import AutomaticResearchQueries
from app.schemas.v1.automatic_research import (
    AutomaticResearchStartRequest,
    AutomaticResearchStartResponse,
    AutomaticResearchViewDTO,
)
from app.services.auto_research import AutoResearchService
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.automatic_research_scope import validate_automatic_research_scope
from app.services.case_tenant_access import CaseTenantAccess


router = APIRouter(
    prefix="/automatic-research",
    tags=["automatic-research-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.post(
    "",
    response_model=AutomaticResearchStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_automatic_research(
    payload: AutomaticResearchStartRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchStartResponse:
    started = AutomaticResearchIntakeService(db).start(
        payload.input, tenant_id=tenant_id
    )
    return AutomaticResearchStartResponse(
        case_id=started.case_id,
        run_id=started.run_id,
        status="queued",
    )


@router.get("/{case_id}", response_model=AutomaticResearchViewDTO)
def get_automatic_research(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchViewDTO:
    return AutomaticResearchQueries(db).get(case_id, tenant_id)


@router.post(
    "/{case_id}/retry",
    response_model=AutomaticResearchStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_automatic_research(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchStartResponse:
    # Authorize before taking locks without disclosing whether another tenant
    # owns the Case.  The stable mutation order is Case -> lifecycle -> run ->
    # source jobs, matching worker terminal writes.
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    db.scalar(select(ResearchCase).where(ResearchCase.id == case_id).with_for_update())
    brief = db.scalar(
        select(EventResearchBrief).where(
            EventResearchBrief.research_case_id == case_id
        )
    )
    if brief is None or brief.workflow_mode != "automatic":
        raise NotFoundError("automatic research case not found")
    lifecycle = db.scalar(
        select(EventResearchLifecycle)
        .where(EventResearchLifecycle.research_case_id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if lifecycle is None or lifecycle.active_run_id is None:
        raise NotFoundError("automatic research case not found")
    old_run = db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.id == lifecycle.active_run_id,
            ResearchRun.research_case_id == case_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if old_run is None:
        raise NotFoundError("automatic research case not found")
    active_runs = list(
        db.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.research_case_id == case_id,
                ResearchRun.status.in_(
                    ("queued", "running", "waiting_for_sources", "waiting_for_review")
                ),
            )
            .order_by(ResearchRun.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if active_runs:
        raise ConflictError("automatic research already has an active run")
    list(
        db.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == old_run.id)
            .order_by(AcquisitionJob.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if old_run.status != "failed":
        raise ConflictError("only a failed automatic research run can be retried")
    scope_event = db.scalar(
        select(ResearchRunEvent)
        .where(
            ResearchRunEvent.run_id == old_run.id,
            ResearchRunEvent.stage == "scope",
        )
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    payload = scope_event.payload_json if scope_event is not None else None
    try:
        validated_scope = validate_automatic_research_scope(db, old_run, payload)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConflictError("automatic research frozen scope is unavailable") from exc

    try:
        frozen = validated_scope.payload
        retry_context = {
            "workflow_mode": "automatic",
            "automatic_protocol": frozen["automatic_protocol"],
            "automatic_evidence_plan": frozen["automatic_evidence_plan"],
            "retried_from_run_id": str(old_run.id),
        }
        if "source_scope" in frozen:
            retry_context["source_scope"] = frozen["source_scope"]
        new_run = AutoResearchService(db).start(
            case_id,
            max_rounds=old_run.max_rounds,
            budget=old_run.budget,
            commit=False,
            thesis_ids=list(validated_scope.factor_ids),
            monitor_version_id=old_run.monitor_version_id,
            trigger="retry",
            allowed_source_types=(
                list(frozen["allowed_source_types"])
                if old_run.monitor_version_id is not None
                else None
            ),
            scope_context=retry_context,
        )
        new_scope_event = db.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == new_run.id,
                ResearchRunEvent.stage == "scope",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        new_payload = (
            new_scope_event.payload_json if new_scope_event is not None else None
        )
        new_validated = validate_automatic_research_scope(
            db, new_run, new_payload
        )
        clone_keys = (
            "factor_ids",
            "factor_statements",
            "budget",
            "automatic_protocol",
            "automatic_evidence_plan",
            "allowed_source_types",
            "monitor_version_id",
            "frequency",
            "next_verification_event",
            "configured_by",
            "configuration_change_reason",
        )
        if (
            new_run.scope_thesis_ids != old_run.scope_thesis_ids
            or new_run.max_rounds != old_run.max_rounds
            or new_run.budget != old_run.budget
            or any(new_payload.get(key) != frozen.get(key) for key in clone_keys)
            or new_payload.get("trigger") != "retry"
            or new_payload.get("retried_from_run_id") != str(old_run.id)
            or new_validated.factor_ids != validated_scope.factor_ids
            or new_payload.get("source_scope") != frozen.get("source_scope")
        ):
            raise ConflictError("automatic research retry scope differs from failed run")
        lifecycle.status = "researching"
        lifecycle.active_run_id = new_run.id
        lifecycle.current_round = 1
        lifecycle.status_summary = "自动研究已重新排队"
        lifecycle.current_gap = None
        lifecycle.next_human_action = None
        lifecycle.updated_at = new_run.updated_at
        db.commit()
    except ConflictError:
        db.rollback()
        raise
    except (TypeError, ValueError, AttributeError) as exc:
        db.rollback()
        raise ConflictError(
            "automatic research retry scope differs from failed run"
        ) from exc
    except Exception:
        db.rollback()
        raise
    return AutomaticResearchStartResponse(
        case_id=str(case_id), run_id=str(new_run.id), status="queued"
    )
