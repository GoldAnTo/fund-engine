"""Event-research creation commands and extraction endpoint."""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy import select
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
    LegacyCaseAdmissionRequest,
    LegacyCaseAdmissionResponse,
    LegacyCaseAdmissionCandidateDTO,
    LegacyCaseAdmissionDocumentDTO,
    LegacyCaseAdmissionQueueResponse,
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
from app.services.source_governance import SourceGovernanceService
from app.services.event_conclusion import EventConclusionService
from app.services.event_review_queue import EventReviewQueueService
from app.services.event_research_scope import EventResearchScopeService
from app.services.auto_research import AutoResearchService
from app.services.case_relation_reviews import CaseRelationReviewService
from app.api.v1.tenant_context import (
    ResearchActor,
    configured_tenant_ids,
    require_case_administrator,
    require_research_tenant,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.models.event_research import CaseRelation, EventResearchBrief
from app.queries.documents import DocumentReadQueries
from app.queries.basis import HistoricalBasis
from app.schemas.v1.documents import DocumentDetailResponse, DocumentListResponse
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    ValidationError,
)
from app.repositories.event_research import EventResearchLifecycleRepository
from app.repositories.outbox import emit_event


router = APIRouter(
    prefix="/event-research",
    tags=["event-research-v1"],
    dependencies=[Depends(require_research_tenant)],
)


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _record_published_material_decision(
    db: Session,
    *,
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    decision: str,
    reason: str,
    actor: str,
    recovery_required: bool = False,
    source_metadata: dict | None = None,
) -> tuple[str | None, object, bool]:
    """Record the human decision after material was frozen.

    Freezing and deciding are kept separate in the domain but committed by the
    API atomically, for both text snapshots and uploaded originals.
    """
    run_id = None
    if decision == "reopen" and not recovery_required:
        run = AutoResearchService(db).continue_published_event(
            case_id,
            document_version_id=document_id,
            reason=reason,
            triggered_by=actor,
        )
        run_id = str(run.id)
    event = emit_event(
        db,
        type="review_decision_recorded",
        aggregate_type="published_material_decision",
        aggregate_id=document_id,
        ref_type="research_case",
        ref_id=case_id,
        origin="operational",
        actor=actor,
        payload={
            "decision": decision,
            "reason": reason,
            "document_version_id": str(document_id),
            "case_id": str(case_id),
            "run_id": run_id,
            "recovery_required": recovery_required,
            "source_metadata": source_metadata or {},
        },
    )
    return run_id, event, recovery_required


@router.get("", response_model=EventResearchListResponse)
def list_event_research(
    status: str | None = None,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> EventResearchListResponse:
    return EventResearchQueries(db).list(status=status, tenant_id=tenant_id)


@router.get(
    "/legacy-admission-queue", response_model=LegacyCaseAdmissionQueueResponse
)
def legacy_case_admission_queue(
    db: Session = Depends(get_db),
    _actor: ResearchActor = Depends(require_case_administrator),
) -> LegacyCaseAdmissionQueueResponse:
    """List only explicit-admin migration candidates and their attached sources."""
    rows = db.execute(
        select(ResearchCase, EventResearchBrief, DocumentVersion)
        .join(
            EventResearchBrief,
            EventResearchBrief.research_case_id == ResearchCase.id,
        )
        .join(
            CaseDocumentVersion,
            CaseDocumentVersion.research_case_id == ResearchCase.id,
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == CaseDocumentVersion.document_version_id,
        )
        .outerjoin(
            CaseTenantAdmission,
            CaseTenantAdmission.research_case_id == ResearchCase.id,
        )
        .where(CaseTenantAdmission.id.is_(None))
        .order_by(ResearchCase.created_at.asc(), DocumentVersion.available_at.asc())
    ).all()
    candidates: dict[uuid.UUID, LegacyCaseAdmissionCandidateDTO] = {}
    for case, brief, document in rows:
        candidate = candidates.setdefault(
            case.id,
            LegacyCaseAdmissionCandidateDTO(
                case_id=str(case.id),
                event_title=brief.event_title,
                created_at=case.created_at,
                documents=[],
            ),
        )
        candidate.documents.append(
            LegacyCaseAdmissionDocumentDTO(
                document_version_id=str(document.id),
                title=document.title,
                source_url=document.source_url,
                available_at=document.available_at,
            )
        )
    return LegacyCaseAdmissionQueueResponse(items=list(candidates.values()))


@router.get("/network", response_model=ResearchNetworkResponse)
def event_research_network(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> ResearchNetworkResponse:
    return EventResearchQueries(db).network(tenant_id=tenant_id)


@router.get("/{case_id}/documents", response_model=DocumentListResponse)
def event_case_documents(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> DocumentListResponse:
    _require_case(db, case_id, tenant_id)
    return DocumentReadQueries(db).list_documents(
        query=None,
        case_id=case_id,
        basis=HistoricalBasis.from_cutoff(None),
        limit=100,
        cursor=None,
    )


@router.get("/{case_id}/documents/{version_id}", response_model=DocumentDetailResponse)
def event_case_document_detail(
    case_id: uuid.UUID,
    version_id: uuid.UUID,
    research_mode: bool = False,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> DocumentDetailResponse:
    _require_case(db, case_id, tenant_id)
    return DocumentReadQueries(db).detail_for_case(
        case_id=case_id,
        version_id=version_id,
        research_mode=research_mode,
    )


@router.get("/{case_id}/relations", response_model=ResearchNetworkResponse)
def event_research_relations(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> ResearchNetworkResponse:
    _require_case(db, case_id, tenant_id)
    return EventResearchQueries(db).relations(case_id, tenant_id=tenant_id)


@router.post(
    "/case-relations/{candidate_id}/reviews",
    response_model=CaseRelationReviewDTO,
    status_code=status.HTTP_201_CREATED,
)
def review_case_relation(
    candidate_id: uuid.UUID,
    payload: CaseRelationReviewRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> CaseRelationReviewDTO:
    try:
        relation = db.get(CaseRelation, candidate_id)
        if relation is None:
            raise ValidationFailedError("case relation candidate not found")
        _require_case(db, relation.source_case_id, tenant_id)
        _require_case(db, relation.target_case_id, tenant_id)
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
def extract_event(
    payload: ExtractEventResearchRequest,
    tenant_id: str = Depends(require_research_tenant),
) -> ExtractEventResearchResponse:
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
    payload: CreateEventResearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> CreateEventResearchResponse:
    try:
        created = EventResearchService(db).create(payload, tenant_id=tenant_id)
    except (ValueError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
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


@router.post(
    "/{case_id}/tenant-admission",
    response_model=LegacyCaseAdmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def admit_legacy_event_case(
    case_id: uuid.UUID,
    payload: LegacyCaseAdmissionRequest,
    db: Session = Depends(get_db),
    _actor: ResearchActor = Depends(require_case_administrator),
) -> LegacyCaseAdmissionResponse:
    """Explicitly admit one legacy Case; never infer its tenant ownership."""
    if payload.tenant_id not in configured_tenant_ids():
        raise ValidationFailedError("tenant_id is not configured by the host")
    try:
        admission = CaseTenantAccess(db).admit_legacy_case(
            case_id=case_id,
            tenant_id=payload.tenant_id,
            initial_document_version_id=uuid.UUID(payload.initial_document_version_id),
            admitted_by=payload.admitted_by,
            admission_reason=payload.reason,
        )
        db.commit()
    except (ValueError, ValidationError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return LegacyCaseAdmissionResponse(
        case_id=str(admission.research_case_id),
        tenant_id=admission.tenant_id,
        initial_document_version_id=str(admission.initial_document_version_id),
        admitted_by=admission.admitted_by,
        reason=admission.admission_reason or "",
        admitted_at=admission.admitted_at,
    )


@router.put("/{case_id}/scope", response_model=UpdateEventResearchScopeResponse)
def update_event_research_scope(
    case_id: uuid.UUID,
    payload: UpdateEventResearchScopeRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> UpdateEventResearchScopeResponse:
    _require_case(db, case_id, tenant_id)
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
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> EventWorkbenchDTO:
    _require_case(db, case_id, tenant_id)
    return EventResearchQueries(db).workbench(case_id)


@router.get("/{case_id}/conclusion-history", response_model=EventConclusionHistoryResponse)
def event_conclusion_history(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> EventConclusionHistoryResponse:
    _require_case(db, case_id, tenant_id)
    return EventResearchQueries(db).conclusion_history(case_id)


@router.get("/{case_id}/scope-history", response_model=EventResearchScopeHistoryResponse)
def event_scope_history(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> EventResearchScopeHistoryResponse:
    _require_case(db, case_id, tenant_id)
    return EventResearchQueries(db).scope_history(case_id)


@router.get("/{case_id}/review-queue", response_model=EventReviewQueueResponse)
def event_review_queue(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> EventReviewQueueResponse:
    _require_case(db, case_id, tenant_id)
    return EventReviewQueueService(db).review_queue(case_id)


@router.post("/{case_id}/conclusion/publish", response_model=PublishEventConclusionResponse, status_code=status.HTTP_201_CREATED)
def publish_event_conclusion(
    case_id: uuid.UUID,
    payload: PublishEventConclusionRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> PublishEventConclusionResponse:
    _require_case(db, case_id, tenant_id)
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
    tenant_id: str = Depends(require_research_tenant),
) -> ContinueEventResearchResponse:
    try:
        _require_case(db, case_id, tenant_id)
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
    tenant_id: str = Depends(require_research_tenant),
) -> AttachEventMaterialResponse:
    try:
        _require_case(db, case_id, tenant_id)
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
    tenant_id: str = Depends(require_research_tenant),
) -> UploadEventMaterialResponse:
    try:
        _require_case(db, case_id, tenant_id)
        metadata = json.loads(source_metadata)
        if not isinstance(metadata, dict):
            raise ValidationFailedError("source_metadata must be a JSON object")
        metadata = {**metadata, "tenant": tenant_id}
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
    tenant_id: str = Depends(require_research_tenant),
) -> PublishedMaterialDecisionResponse:
    try:
        _require_case(db, case_id, tenant_id)
        document = EventResearchService(db).freeze_published_material(
            case_id,
            raw_input=payload.raw_input,
            source_url=payload.source_url,
            source_type=payload.source_type,
            source_metadata={**payload.source_metadata, "tenant": tenant_id},
            actor=payload.actor,
        )
        run_id, event, recovery_required = _record_published_material_decision(
            db,
            case_id=case_id,
            document_id=document.id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            source_metadata={**payload.source_metadata, "tenant": tenant_id},
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
        recovery_required=recovery_required,
        lifecycle=EventResearchLifecycleDTO(status=lifecycle.status, active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None, current_round=lifecycle.current_round, status_summary=lifecycle.status_summary, current_gap=lifecycle.current_gap, next_human_action=lifecycle.next_human_action),
    )


@router.post(
    "/{case_id}/published-uploaded-material-decisions",
    response_model=PublishedMaterialDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def decide_published_uploaded_material(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    decision: Literal["reopen", "no_change"] = Form(...),
    reason: str = Form(...),
    actor: str = Form(...),
    source_metadata: str = Form("{}"),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> PublishedMaterialDecisionResponse:
    try:
        _require_case(db, case_id, tenant_id)
        metadata = json.loads(source_metadata)
        if not isinstance(metadata, dict):
            raise ValidationFailedError("source_metadata must be a JSON object")
        metadata = {**metadata, "tenant": tenant_id}
        if not actor.strip():
            raise ValidationFailedError("actor must not be empty")
        if not reason.strip():
            raise ValidationFailedError("reason must not be empty")
        if decision == "reopen" and not SourceGovernanceService(
            db
        ).declared_event_intake_allows_research(
            source_type="uploaded_file", source_metadata=metadata
        ):
            raise ValidationFailedError(
                "current source declaration does not permit research"
            )
        raw = await file.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise ValidationFailedError("uploaded original must not exceed 20 MiB")
        frozen = DocumentUploadService(db).freeze_published_case_material(
            case_id=case_id,
            raw=raw,
            file_name=file.filename or "",
            mime_type=file.content_type or "application/octet-stream",
            actor=actor.strip(),
            source_metadata=metadata,
        )
        run_id, event, recovery_required = _record_published_material_decision(
            db,
            case_id=case_id,
            document_id=frozen.document.id,
            decision=decision,
            reason=reason.strip(),
            actor=actor.strip(),
            recovery_required=(
                decision == "reopen" and frozen.document.parse_state == "failed"
            ),
            source_metadata=metadata,
        )
        db.commit()
    except (ValueError, json.JSONDecodeError, ValidationFailedError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    lifecycle = EventResearchLifecycleRepository(db).get(case_id)
    assert lifecycle is not None
    return PublishedMaterialDecisionResponse(
        document_version_id=str(frozen.document.id),
        decision=decision,
        decision_event_id=str(event.id),
        run_id=run_id,
        recovery_required=recovery_required,
        lifecycle=EventResearchLifecycleDTO(
            status=lifecycle.status,
            active_run_id=str(lifecycle.active_run_id)
            if lifecycle.active_run_id
            else None,
            current_round=lifecycle.current_round,
            status_summary=lifecycle.status_summary,
            current_gap=lifecycle.current_gap,
            next_human_action=lifecycle.next_human_action,
        ),
    )
