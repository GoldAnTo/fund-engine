"""Human preparation review commands and safe preparation activity reads."""
from __future__ import annotations
import re, uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
from app.services.research_preparation import ResearchPreparationService, ClaimDecision, ProtocolConfirmation
from app.models.ledger import ValidationError
from app.errors import ValidationFailedError
from app.errors import ConflictError
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact, ResearchPreparationEvent
from app.schemas.v1.research_preparation import *

router = APIRouter(prefix="/event-research", tags=["research-preparation-v1"], dependencies=[Depends(require_research_tenant)])
_SECRET = re.compile(r"(?i)(token|authorization|password|secret|bearer|sk-[\w-]+|[?&](?:token|key|auth)=)")
_ERRORS = {"preparation_provider_unavailable": "准备服务暂时不可用", "preparation_internal_error": "准备任务暂时失败", "preparation_backfill_candidate_limit": "候选数量超出处理限制"}
def _case(db, case_id, tenant): CaseTenantAccess(db).require_case(case_id, tenant)
def _safe(value):
    if isinstance(value, dict): return {str(k): _safe(v) for k,v in value.items() if not _SECRET.search(str(k))}
    if isinstance(value, list): return [_safe(v) for v in value]
    return "[redacted]" if isinstance(value, str) and _SECRET.search(value) else value
def _dto(db, case_id):
    prep = db.scalar(select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id))
    if prep is None: from app.errors import NotFoundError; raise NotFoundError("research preparation not found")
    arts = {a.kind:a for a in db.scalars(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id==prep.id, ResearchPreparationArtifact.state=="current"))}
    def art(kind):
        a=arts.get(kind); return None if a is None else PreparationArtifactDTO(sequence=a.sequence,payload=_safe(a.payload),state=a.state,context_fingerprint=a.context_fingerprint)
    return ResearchPreparationDTO(case_id=case_id,revision=prep.version,status=prep.status,research_run_id=prep.research_run_id,next_attempt_at=prep.next_attempt_at.isoformat() if prep.next_attempt_at else None,last_error_message=_ERRORS.get(prep.last_error_code),system={"claims":PreparationStepDTO(state=prep.parse_claims_state,artifact_sequence=arts.get("atomic_claim_candidates").sequence if arts.get("atomic_claim_candidates") else None),"protocol":PreparationStepDTO(state=prep.draft_protocol_state,artifact_sequence=arts.get("research_protocol_draft").sequence if arts.get("research_protocol_draft") else None),"plan":PreparationStepDTO(state=prep.draft_evidence_plan_state,artifact_sequence=arts.get("evidence_acquisition_plan").sequence if arts.get("evidence_acquisition_plan") else None)},review={"claims":PreparationStepDTO(state=prep.claim_review_state),"protocol":PreparationStepDTO(state=prep.protocol_review_state),"plan":PreparationStepDTO(state=prep.plan_review_state)},artifacts={"claims":art("atomic_claim_candidates"),"protocol":art("research_protocol_draft"),"plan":art("evidence_acquisition_plan")})
@router.get("/{case_id}/preparation", response_model=ResearchPreparationDTO)
def get_preparation(case_id: uuid.UUID, db:Session=Depends(get_db), tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); return _dto(db,case_id)
@router.get("/{case_id}/preparation/events", response_model=ResearchPreparationEventsResponse)
def preparation_events(case_id:uuid.UUID, after_seq:int=Query(0,ge=0), limit:int=Query(100,ge=1,le=200), db:Session=Depends(get_db), tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); prep=db.scalar(select(ResearchPreparation).where(ResearchPreparation.research_case_id==case_id));
    if prep is None: from app.errors import NotFoundError; raise NotFoundError("research preparation not found")
    rows=list(db.scalars(select(ResearchPreparationEvent).where(ResearchPreparationEvent.research_preparation_id==prep.id,ResearchPreparationEvent.seq>after_seq).order_by(ResearchPreparationEvent.seq).limit(limit+1))); page=rows[:limit]
    return ResearchPreparationEventsResponse(items=[ResearchPreparationEventDTO(seq=x.seq,type=x.type,step=x.step,message=_safe(x.message),detail=_safe(x.detail),created_at=x.created_at.isoformat()) for x in page],next_after_seq=page[-1].seq if len(rows)>limit and page else None)
def _commit(db):
    try: db.commit()
    except ValidationError as exc: db.rollback(); raise ValidationFailedError("request is invalid") from exc
@router.post("/{case_id}/preparation/claims/confirm", response_model=ResearchPreparationDTO)
def confirm_claims(case_id:uuid.UUID,payload:ConfirmClaimsRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); ResearchPreparationService(db).confirm_claims(case_id,actor=payload.actor,revision=payload.revision,decisions=[ClaimDecision(**x.model_dump()) for x in payload.decisions]); _commit(db); return _dto(db,case_id)
@router.post("/{case_id}/preparation/protocol/confirm", response_model=ResearchPreparationDTO)
def confirm_protocol(case_id:uuid.UUID,payload:ConfirmProtocolRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); ResearchPreparationService(db).confirm_protocol(case_id,actor=payload.actor,revision=payload.revision,payload=ProtocolConfirmation(payload.draft_sequence,payload.edits)); _commit(db); return _dto(db,case_id)
@router.post("/{case_id}/preparation/retry", response_model=ResearchPreparationDTO)
def retry(case_id:uuid.UUID,payload:RetryResearchPreparationRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); ResearchPreparationService(db).retry_failed_step(case_id,actor=payload.actor,revision=payload.revision); _commit(db); return _dto(db,case_id)
@router.post("/{case_id}/preparation/authorize", response_model=ResearchPreparationDTO, status_code=status.HTTP_201_CREATED)
def authorize(case_id:uuid.UUID,payload:AuthorizeEvidencePlanRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id)
    # Existing operational idempotency rows are global; bind the key to this
    # exact authorization command before creating any durable run.
    from app.repositories.operational import IdempotencyRepository
    import hashlib, json
    fingerprint=hashlib.sha256(json.dumps({"case":str(case_id),"revision":payload.revision,"plan":payload.plan_sequence},sort_keys=True).encode()).hexdigest(); repo=IdempotencyRepository(db); existing=repo.get(payload.idempotency_key)
    if existing is not None:
        if existing.request_fingerprint != fingerprint: raise ConflictError("idempotency key conflicts with another authorization")
        if existing.status == "completed": return _dto(db,case_id)
        raise ConflictError("authorization is in progress")
    row=repo.insert_in_progress(key=payload.idempotency_key,request_fingerprint=fingerprint)
    try:
        ResearchPreparationService(db).authorize_evidence_plan(case_id,actor=payload.actor,revision=payload.revision,plan_sequence=payload.plan_sequence)
        repo.complete(row,response_status=201,response_payload={"case_id":str(case_id)}); _commit(db)
    except ValidationError as exc:
        db.rollback()
        raise ValidationFailedError("authorization request is invalid") from exc
    except Exception:
        db.rollback(); raise
    return _dto(db,case_id)
