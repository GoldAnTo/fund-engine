"""Event-research creation commands and extraction endpoint."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ValidationFailedError
from app.schemas.v1.event_research import (
    CreateEventResearchRequest,
    CreateEventResearchResponse,
    AttachEventMaterialRequest,
    AttachEventMaterialResponse,
    UploadEventMaterialResponse,
    CaseRelationReviewDTO,
    CaseRelationReviewRequest,
    EventResearchLifecycleDTO,
    ExtractEventResearchRequest,
    ExtractEventResearchResponse,
    EventResearchListResponse,
    EventConclusionHistoryResponse,
    EventResearchScopeHistoryResponse,
    ResearchNetworkResponse,
    EventReviewQueueResponse,
    EventWorkbenchDTO,
    PublishEventConclusionRequest,
    PublishEventConclusionResponse,
    ContinueEventResearchRequest,
    ContinueEventResearchResponse,
    PublishedMaterialDecisionRequest,
    PublishedMaterialDecisionResponse,
    UpdateEventResearchScopeRequest,
    UpdateEventResearchScopeResponse,
)
from app.queries.event_research import EventResearchQueries
from app.services.event_extraction import EventExtractionService
from app.services.event_research import EventResearchService
from app.services.document_uploads import DocumentUploadService
from app.services.event_conclusion import EventConclusionService
from app.services.event_review_queue import EventReviewQueueService
from app.services.event_research_scope import EventResearchScopeService
from app.services.auto_research import AutoResearchService
from app.services.case_relation_reviews import CaseRelationReviewService
from app.api.v1.tenant_context import require_research_tenant
from app.models.ledger import ValidationError
from app.repositories.event_research import EventResearchLifecycleRepository
from app.repositories.outbox import emit_event


router = APIRouter(
    prefix="/event-research",
    tags=["event-research-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.get("", response_model=EventResearchListResponse)
def list_event_research(
    status: str | None = None, db: Session = Depends(get_db)
) -> EventResearchListResponse:
    return EventResearchQueries(db).list(status=status)


@router.get("/network", response_model=ResearchNetworkResponse)
def event_research_network(db: Session = Depends(get_db)) -> ResearchNetworkResponse:
    return EventResearchQueries(db).network()


@router.get("/{case_id}/relations", response_model=ResearchNetworkResponse)
def event_research_relations(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> ResearchNetworkResponse:
    return EventResearchQueries(db).relations(case_id)


@router.post(
    "/case-relations/{candidate_id}/reviews",
    response_model=CaseRelationReviewDTO,
    status_code=status.HTTP_201_CREATED,
)
def review_case_relation(
    candidate_id: uuid.UUID,
    payload: CaseRelationReviewRequest,
    db: Session = Depends(get_db),
) -> CaseRelationReviewDTO:
    try:
        review = CaseRelationReviewService(db).review(
            candidate_id,
            outcome=payload.outcome,
            relation_type=payload.relation_type,
            reviewer=payload.reviewer,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
        db.commit()
    except (ValueError, ValidationError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return CaseRelationReviewDTO(
        id=str(review.id),
        case_relation_id=str(review.case_relation_id),
        outcome=review.outcome,
        relation_type=review.relation_type,
        reviewer=review.reviewer,
        reason=review.reason,
        reviewed_relation_id=str(review.reviewed_relation_id)
        if review.reviewed_relation_id
        else None,
        created_at=review.created_at,
    )


@router.post("/extract", response_model=ExtractEventResearchResponse)
def extract_event(payload: ExtractEventResearchRequest) -> ExtractEventResearchResponse:
    extracted = EventExtractionService().extract(
        raw_input=payload.raw_input, source_url=payload.source_url
    )
    return ExtractEventResearchResponse(
        event_title=extracted.event_title,
        company_name=extracted.company_name,
        ticker=extracted.ticker,
        event_at=extracted.event_at,
        market_reaction=extracted.market_reaction,
        summary=extracted.summary,
        research_question=extracted.research_question,
        candidate_factors=list(extracted.candidate_factors),
        confirmation_required=extracted.confirmation_required,
    )


@router.post("", response_model=CreateEventResearchResponse, status_code=status.HTTP_201_CREATED)
def create_event_research(
    payload: CreateEventResearchRequest, db: Session = Depends(get_db)
) -> CreateEventResearchResponse:
    created = EventResearchService(db).create(payload)
    lifecycle = created.lifecycle
    return CreateEventResearchResponse(
        case_id=created.case_id,
        brief_id=created.brief_id,
        lifecycle=EventResearchLifecycleDTO(
            status=lifecycle.status,
            active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None,
            current_round=lifecycle.current_round,
            status_summary=lifecycle.status_summary,
            current_gap=lifecycle.current_gap,
            next_human_action=lifecycle.next_human_action,
        ),
    )


@router.put("/{case_id}/scope", response_model=UpdateEventResearchScopeResponse)
def update_event_research_scope(
    case_id: uuid.UUID,
    payload: UpdateEventResearchScopeRequest,
    db: Session = Depends(get_db),
) -> UpdateEventResearchScopeResponse:
    updated = EventResearchScopeService(db).update(
        case_id,
        factors=payload.factors,
        changed_by=payload.changed_by,
        change_reason=payload.change_reason,
    )
    db.commit()
    return UpdateEventResearchScopeResponse(
        version=updated.version,
        factors=[
            {"statement": factor.statement, "description": factor.description}
            for factor in updated.factors
        ],
        reclassified_evidence_count=updated.reclassified_evidence_count,
        unmapped_evidence_count=updated.unmapped_evidence_count,
    )


@router.get("/{case_id}/workbench", response_model=EventWorkbenchDTO)
def event_research_workbench(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventWorkbenchDTO:
    return EventResearchQueries(db).workbench(case_id)


@router.get("/{case_id}/conclusion-history", response_model=EventConclusionHistoryResponse)
def event_conclusion_history(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventConclusionHistoryResponse:
    return EventResearchQueries(db).conclusion_history(case_id)


@router.get("/{case_id}/scope-history", response_model=EventResearchScopeHistoryResponse)
def event_scope_history(case_id: uuid.UUID, db: Session = Depends(get_db)) -> EventResearchScopeHistoryResponse:
    return EventResearchQueries(db).scope_history(case_id)


@router.get("/{case_id}/review-queue", response_model=EventReviewQueueResponse)
def event_review_queue(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventReviewQueueResponse:
    return EventReviewQueueService(db).review_queue(case_id)


@router.post("/{case_id}/conclusion/publish", response_model=PublishEventConclusionResponse, status_code=status.HTTP_201_CREATED)
def publish_event_conclusion(
    case_id: uuid.UUID, payload: PublishEventConclusionRequest, db: Session = Depends(get_db)
) -> PublishEventConclusionResponse:
    published = EventConclusionService(db).publish(
        case_id, text=payload.text, reviewer=payload.reviewer
    )
    db.commit()
    return PublishEventConclusionResponse(conclusion_id=str(published.id), state=published.state)


@router.post("/{case_id}/continuations", response_model=ContinueEventResearchResponse, status_code=status.HTTP_201_CREATED)
def continue_event_research(
    case_id: uuid.UUID,
    payload: ContinueEventResearchRequest,
    db: Session = Depends(get_db),
) -> ContinueEventResearchResponse:
    try:
        run = AutoResearchService(db).continue_published_event(
            case_id,
            document_version_id=uuid.UUID(payload.document_version_id),
            reason=payload.reason,
            triggered_by=payload.triggered_by,
        )
        db.commit()
    except (ValueError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    lifecycle = EventResearchLifecycleRepository(db).get(case_id)
    assert lifecycle is not None
    return ContinueEventResearchResponse(
        run_id=str(run.id),
        lifecycle=EventResearchLifecycleDTO(
            status=lifecycle.status,
            active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None,
            current_round=lifecycle.current_round,
            status_summary=lifecycle.status_summary,
            current_gap=lifecycle.current_gap,
            next_human_action=lifecycle.next_human_action,
        ),
    )


@router.post("/{case_id}/materials", response_model=AttachEventMaterialResponse, status_code=status.HTTP_201_CREATED)
def attach_event_material(
    case_id: uuid.UUID,
    payload: AttachEventMaterialRequest,
    db: Session = Depends(get_db),
) -> AttachEventMaterialResponse:
    try:
        document = EventResearchService(db).attach_material_to_existing_case(
            case_id,
            raw_input=payload.raw_input,
            source_url=payload.source_url,
            source_type=payload.source_type,
            source_metadata=payload.source_metadata,
            actor=payload.actor,
        )
        emit_event(
            db,
            type="event_material_attached",
            aggregate_type="document_version",
            aggregate_id=document.id,
            ref_type="research_case",
            ref_id=case_id,
            origin="operational",
            actor=payload.actor,
            payload={"source_type": payload.source_type, "case_id": str(case_id)},
        )
        db.commit()
    except (ValueError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return AttachEventMaterialResponse(
        document_version_id=str(document.id), source_type=payload.source_type
    )


@router.post(
    "/{case_id}/uploaded-materials",
    response_model=UploadEventMaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_event_material(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    actor: str = Form(...),
    source_metadata: str = Form("{}"),
    db: Session = Depends(get_db),
) -> UploadEventMaterialResponse:
    try:
        metadata = json.loads(source_metadata)
        if not isinstance(metadata, dict):
            raise ValidationFailedError("source_metadata must be a JSON object")
        if not actor.strip():
            raise ValidationFailedError("actor must not be empty")
        raw = await file.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise ValidationFailedError("uploaded original must not exceed 20 MiB")
        frozen = DocumentUploadService(db).freeze_case_material(
            case_id=case_id,
            raw=raw,
            file_name=file.filename or "",
            mime_type=file.content_type or "application/octet-stream",
            actor=actor.strip(),
            source_metadata=metadata,
        )
        emit_event(
            db,
            type="event_material_attached",
            aggregate_type="document_version",
            aggregate_id=frozen.document.id,
            ref_type="research_case",
            ref_id=case_id,
            origin="operational",
            actor=actor.strip(),
            payload={
                "source_type": "uploaded_file",
                "case_id": str(case_id),
                "parse_state": frozen.document.parse_state,
            },
        )
        db.commit()
    except (ValueError, json.JSONDecodeError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return UploadEventMaterialResponse(
        document_version_id=str(frozen.document.id),
        parse_state=(
            "failed"
            if frozen.document.parse_state == "failed"
            else "partial"
            if frozen.document.parse_state == "partial"
            else "parsed"
        ),
        next_action=frozen.next_action,
    )


@router.post("/{case_id}/published-material-decisions", response_model=PublishedMaterialDecisionResponse, status_code=status.HTTP_201_CREATED)
def decide_published_material(
    case_id: uuid.UUID,
    payload: PublishedMaterialDecisionRequest,
    db: Session = Depends(get_db),
) -> PublishedMaterialDecisionResponse:
    try:
        document = EventResearchService(db).freeze_published_material(
            case_id,
            raw_input=payload.raw_input,
            source_url=payload.source_url,
            source_type=payload.source_type,
            source_metadata=payload.source_metadata,
            actor=payload.actor,
        )
        run_id = None
        if payload.decision == "reopen":
            run = AutoResearchService(db).continue_published_event(
                case_id,
                document_version_id=document.id,
                reason=payload.reason,
                triggered_by=payload.actor,
            )
            run_id = str(run.id)
        event = emit_event(
            db,
            type="review_decision_recorded",
            aggregate_type="published_material_decision",
            aggregate_id=document.id,
            ref_type="research_case",
            ref_id=case_id,
            origin="operational",
            actor=payload.actor,
            payload={"decision": payload.decision, "reason": payload.reason, "document_version_id": str(document.id), "case_id": str(case_id), "run_id": run_id},
        )
        db.commit()
    except (ValueError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    lifecycle = EventResearchLifecycleRepository(db).get(case_id)
    assert lifecycle is not None
    return PublishedMaterialDecisionResponse(
        document_version_id=str(document.id), decision=payload.decision,
        decision_event_id=str(event.id), run_id=run_id,
        lifecycle=EventResearchLifecycleDTO(status=lifecycle.status, active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None, current_round=lifecycle.current_round, status_summary=lifecycle.status_summary, current_gap=lifecycle.current_gap, next_human_action=lifecycle.next_human_action),
    )
