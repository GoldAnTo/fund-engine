"""Human preparation review commands and safe preparation activity reads."""
from __future__ import annotations
import re, uuid
from urllib.parse import parse_qsl, urlsplit
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
from app.services.research_preparation import ResearchPreparationService, ClaimDecision, ProtocolConfirmation
from app.models.ledger import CaseTenantAdmission, DocumentVersion, ResearchCase, ValidationError
from app.errors import ValidationFailedError
from app.errors import ConflictError
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact, ResearchPreparationEvent
from app.schemas.v1.research_preparation import *
from app.schemas.v1.common import ErrorEnvelope
from app.services.preparation_display import preparation_artifact_allows_display

router = APIRouter(prefix="/event-research", tags=["research-preparation-v1"], dependencies=[Depends(require_research_tenant)])
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:token|authorization|password|secret|bearer)\b|sk-[\w-]+)")
_SENSITIVE_KEY_PARTS = ("token", "authorization", "password", "secret", "bearer", "apikey", "accesskey", "signature")
_ERRORS = {"preparation_provider_unavailable": "准备服务暂时不可用", "preparation_internal_error": "准备任务暂时失败", "preparation_backfill_candidate_limit": "候选数量超出处理限制"}
_WRITE_ERRORS = {
    409: {"model": ErrorEnvelope, "description": "Conflict"},
    422: {"model": ErrorEnvelope, "description": "Validation failed"},
}
def _case(db, case_id, tenant): CaseTenantAccess(db).require_case(case_id, tenant)

_PROGRESS_STEPS = (("parse_claims", "parse_claims_state"), ("draft_protocol", "draft_protocol_state"), ("draft_evidence_plan", "draft_evidence_plan_state"))

def _progress(preparation: ResearchPreparation) -> PreparationProgressDTO:
    states = {step: getattr(preparation, field) for step, field in _PROGRESS_STEPS}
    failed_step = next((step for step, state in states.items() if state == "failed"), None)
    current_step = next((step for step, state in states.items() if state in {"running", "retrying"}), None)
    if current_step is None and failed_step is None:
        current_step = {"awaiting_claim_review": "parse_claims", "awaiting_protocol_confirmation": "draft_protocol", "awaiting_plan_authorization": "draft_evidence_plan"}.get(preparation.status)
    return PreparationProgressDTO(completed_steps=sum(state == "succeeded" for state in states.values()), total_steps=len(_PROGRESS_STEPS), current_step=current_step, failed_step=failed_step)


def _sensitive_key(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sensitive_query(value: str) -> bool:
    if "?" not in value:
        return False
    try:
        query = urlsplit(value).query
        return any(_sensitive_key(key) for key, _ in parse_qsl(query, keep_blank_values=True))
    except ValueError:
        # Invalid URLs are still untrusted text; the ordinary value matcher
        # below covers conventional credential tokens without parsing them.
        return False


def _safe(value):
    if isinstance(value, dict): return {str(k): _safe(v) for k,v in value.items() if not _sensitive_key(k)}
    if isinstance(value, list): return [_safe(v) for v in value]
    return "[redacted]" if isinstance(value, str) and (_SECRET_VALUE.search(value) or _sensitive_query(value)) else value
def _dto(db, case_id):
    prep = db.scalar(select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id))
    if prep is None: from app.errors import NotFoundError; raise NotFoundError("research preparation not found")
    case = db.get(ResearchCase, case_id)
    admission = db.scalar(select(CaseTenantAdmission).where(CaseTenantAdmission.research_case_id == case_id))
    material = db.get(DocumentVersion, admission.initial_document_version_id) if admission is not None else None
    arts = {a.kind:a for a in db.scalars(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id==prep.id, ResearchPreparationArtifact.state=="current"))}
    def art(kind):
        a=arts.get(kind)
        if a is None: return None
        display_withheld = not preparation_artifact_allows_display(db, case_id, a.payload)
        payload = {} if display_withheld else _safe(a.payload)
        return PreparationArtifactDTO(sequence=a.sequence,payload=payload,state=a.state,context_fingerprint=a.context_fingerprint,display_withheld=display_withheld)
    authorized_plan_withheld = (
        prep.status == "authorized"
        and not preparation_artifact_allows_display(
            db, case_id, prep.authorized_evidence_plan
        )
    )
    return ResearchPreparationDTO(case_id=case_id,case_title=case.title if case is not None else "研究 Case",initial_material=PreparationInitialMaterialDTO(document_version_id=material.id,title=material.title,parse_state=material.parse_state) if material is not None else None,progress=_progress(prep),revision=prep.version,status=prep.status,research_run_id=prep.research_run_id,next_attempt_at=prep.next_attempt_at.isoformat() if prep.next_attempt_at else None,last_error_message=_ERRORS.get(prep.last_error_code),system={"claims":PreparationStepDTO(state=prep.parse_claims_state,artifact_sequence=arts.get("atomic_claim_candidates").sequence if arts.get("atomic_claim_candidates") else None),"protocol":PreparationStepDTO(state=prep.draft_protocol_state,artifact_sequence=arts.get("research_protocol_draft").sequence if arts.get("research_protocol_draft") else None),"plan":PreparationStepDTO(state=prep.draft_evidence_plan_state,artifact_sequence=arts.get("evidence_acquisition_plan").sequence if arts.get("evidence_acquisition_plan") else None)},review={"claims":PreparationStepDTO(state=prep.claim_review_state),"protocol":PreparationStepDTO(state=prep.protocol_review_state),"plan":PreparationStepDTO(state=prep.plan_review_state)},artifacts={"claims":art("atomic_claim_candidates"),"protocol":art("research_protocol_draft"),"plan":art("evidence_acquisition_plan")}, authorized_evidence_plan=None if authorized_plan_withheld else _safe(prep.authorized_evidence_plan) if prep.status == "authorized" else None, authorized_evidence_plan_display_withheld=authorized_plan_withheld)
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
@router.post("/{case_id}/preparation/claims/confirm", response_model=ResearchPreparationDTO, responses=_WRITE_ERRORS)
def confirm_claims(case_id:uuid.UUID,payload:ConfirmClaimsRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id)
    try:
        ResearchPreparationService(db).confirm_claims(case_id,actor=payload.actor,revision=payload.revision,decisions=[ClaimDecision(**x.model_dump()) for x in payload.decisions])
        _commit(db)
    except ValidationError as exc:
        db.rollback()
        raise ValidationFailedError("claim candidates cannot be confirmed") from exc
    return _dto(db,case_id)
@router.post("/{case_id}/preparation/protocol/confirm", response_model=ResearchPreparationDTO, responses=_WRITE_ERRORS)
def confirm_protocol(case_id:uuid.UUID,payload:ConfirmProtocolRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id)
    try:
        ResearchPreparationService(db).confirm_protocol(case_id,actor=payload.actor,revision=payload.revision,payload=ProtocolConfirmation(payload.draft_sequence,payload.edits)); _commit(db)
    except ValidationError as exc:
        db.rollback(); raise ValidationFailedError("protocol draft is invalid") from exc
    return _dto(db,case_id)
@router.post("/{case_id}/preparation/retry", response_model=ResearchPreparationDTO, responses=_WRITE_ERRORS)
def retry(case_id:uuid.UUID,payload:RetryResearchPreparationRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id); ResearchPreparationService(db).retry_failed_step(case_id,actor=payload.actor,revision=payload.revision); _commit(db); return _dto(db,case_id)
@router.post("/{case_id}/preparation/authorize", response_model=ResearchPreparationDTO, status_code=status.HTTP_201_CREATED, responses=_WRITE_ERRORS)
def authorize(case_id:uuid.UUID,payload:AuthorizeEvidencePlanRequest,db:Session=Depends(get_db),tenant_id:str=Depends(require_research_tenant)):
    _case(db,case_id,tenant_id)
    from app.repositories.operational import IdempotencyRepository
    import hashlib, json
    fingerprint=hashlib.sha256(json.dumps({"case":str(case_id),"revision":payload.revision,"plan":payload.plan_sequence,"actor":payload.actor},sort_keys=True,separators=(",", ":")).encode()).hexdigest(); repo=IdempotencyRepository(db)
    row, acquired = repo.acquire(key=payload.idempotency_key,request_fingerprint=fingerprint)
    if not acquired:
        if row.request_fingerprint != fingerprint: raise ConflictError("idempotency_key_conflict")
        if row.status == "completed" and isinstance(row.response_payload, dict):
            # Another connection may have completed authorization while this
            # session waited for its idempotency row. Refresh the pre-read
            # preparation before returning current, display-filtered state.
            db.expire_all()
            return _dto(db, case_id)
        raise ConflictError("idempotency_conflict")
    try:
        ResearchPreparationService(db).authorize_evidence_plan(case_id,actor=payload.actor,revision=payload.revision,plan_sequence=payload.plan_sequence)
        response = _dto(db,case_id)
        repo.complete(row,response_status=201,response_payload=response.model_dump(mode="json")); _commit(db)
    except ValidationError as exc:
        db.rollback()
        raise ValidationFailedError("authorization request is invalid") from exc
    except Exception:
        db.rollback(); raise
    return response
