"""HTTP boundary for the underwriting research kernel."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ConflictError as HttpConflictError
from app.errors import ValidationFailedError
from app.models.ledger import ConflictError as DomainConflictError
from app.models.ledger import ValidationError
from app.underwriting.api.schemas import (
    AnswerabilityCreate,
    AnswerabilityResponse,
    BasisCreate,
    BasisResponse,
    LedgerEntryCreate,
    LedgerEntryResponse,
    MandateCreate,
    MandateResponse,
    ObjectRelationCreate,
    ObjectRelationResponse,
    ResearchObjectCreate,
    ResearchObjectResponse,
    SnapshotResponse,
    UnderwritingErrorEnvelope,
    EvidenceOnlyEconomicModelResponse,
    EconomicSourceResponse,
    EconomicObservationResponse,
    CandidateMechanismResponse,
    ResearchRevisionArtifactResponse,
    ResearchArchiveItemResponse,
    ResearchArchiveListResponse,
    ResearchRevisionChangeResponse,
    ResearchRevisionDiffResponse,
    ResearchRevisionHistoryResponse,
    ResearchRevisionResponse,
)
from app.errors import NotFoundError
from app.underwriting.persistence.models import UnderwritingAnswerabilityEvaluation, UnderwritingHistoricalBasis, UnderwritingResearchVersion, UnderwritingLedgerEntry, UnderwritingObjectRelation
from app.underwriting.persistence.research_models import UnderwritingMechanismPackVersion, UnderwritingMetricDefinitionVersion, UnderwritingMetricObservation, UnderwritingSourceManifestVersion
from app.underwriting.domain.types import (
    BlockerCode,
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
    EligibleAction,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import UnderwritingKernelService
from app.underwriting.services.research_revision_diff import (
    ResearchRevisionDiffService,
    RevisionArtifactRef,
    RevisionChange,
    ResearchRevisionSummary,
)


router = APIRouter(prefix="/api/underwriting/v1", tags=["underwriting-v1"])
T = TypeVar("T")
WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
READ_ERROR_RESPONSES = {404: {"model": UnderwritingErrorEnvelope}, 422: {"model": UnderwritingErrorEnvelope}}


def _service(db: Session) -> UnderwritingKernelService:
    return UnderwritingKernelService(db, now=lambda: datetime.now(UTC))


def _write(db: Session, operation: Callable[[], T]) -> T:
    """Commit only a successful service operation and normalize kernel errors."""
    try:
        value = operation()
        db.commit()
        return value
    except ValidationError as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HttpConflictError("underwriting write conflicts") from exc
    except (DomainConflictError, StaleParentError) as exc:
        db.rollback()
        raise HttpConflictError(str(exc)) from exc
    except Exception:
        db.rollback()
        raise


def _object_response(value) -> ResearchObjectResponse:
    return ResearchObjectResponse(
        id=value.id,
        kind=value.kind,
        external_key=value.external_key,
        canonical_name=value.canonical_name,
        created_at=value.created_at,
    )


def _relation_response(value) -> ObjectRelationResponse:
    return ObjectRelationResponse(
        id=value.id,
        parent_id=value.parent_id,
        child_id=value.child_id,
        relation_type=value.relation_type,
        created_at=value.created_at,
    )


def _basis_response(value) -> BasisResponse:
    return BasisResponse(
        id=value.id,
        cutoff=value.cutoff,
        price_as_of=value.price_as_of,
        source_manifest_hash=value.source_manifest_hash,
        created_at=value.created_at,
    )


def _mandate_response(value) -> MandateResponse:
    return MandateResponse(
        id=value.id,
        mandate_key=value.mandate_key,
        horizon_years=value.horizon_years,
        base_currency=value.base_currency,
        required_return=value.required_return,
        permanent_loss_limit=value.permanent_loss_limit,
        comparison_set=list(value.comparison_set),
        version=value.version,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
    )


def _ledger_response(value) -> LedgerEntryResponse:
    return LedgerEntryResponse(
        id=value.id,
        object_id=value.object_id,
        basis_id=value.basis_id,
        ledger_kind=value.ledger_kind,
        family_key=value.family_key,
        entry_type=value.entry_type,
        version=value.version,
        payload=dict(value.payload),
        effective_at=value.effective_at,
        available_at=value.available_at,
        source_boundary=value.source_boundary,
        content_hash=value.content_hash,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
    )


def _answerability_response(value) -> AnswerabilityResponse:
    return AnswerabilityResponse(
        id=value.id,
        object_id=value.object_id,
        basis_id=value.basis_id,
        state=value.state,
        blockers=list(value.blockers),
        research_debt_keys=list(value.research_debt_keys),
        resolvable_within_mandate=value.resolvable_within_mandate,
        allowed_action=value.allowed_action,
        resolution_requirements=list(value.resolution_requirements),
        version=value.version,
        supersedes_id=value.supersedes_id,
        created_at=value.created_at,
    )


def _revision_artifact_response(value: RevisionArtifactRef) -> ResearchRevisionArtifactResponse:
    return ResearchRevisionArtifactResponse(
        reference=value.reference,
        artifact_type=value.artifact_type,
        identity=value.identity,
        content_hash=value.content_hash,
        source_locators=list(value.source_locators),
        unit=value.unit,
        period_start=value.period_start,
        period_end=value.period_end,
        available_at=value.available_at,
        status=value.status,
    )


def _revision_response(value: ResearchRevisionSummary) -> ResearchRevisionResponse:
    return ResearchRevisionResponse(
        id=value.id,
        object_id=value.object_id,
        basis_id=value.basis_id,
        version_kind=value.version_kind,
        sequence=value.sequence,
        content_hash=value.content_hash,
        cutoff=value.cutoff,
        source_manifest_hash=value.source_manifest_hash,
        parent_refs=[_revision_artifact_response(item) for item in value.parent_refs],
    )


def _revision_change_response(value: RevisionChange) -> ResearchRevisionChangeResponse:
    return ResearchRevisionChangeResponse(
        group=value.group,
        change_type=value.change_type,
        artifact_type=value.artifact_type,
        identity=value.identity,
        before=_revision_artifact_response(value.before) if value.before is not None else None,
        after=_revision_artifact_response(value.after) if value.after is not None else None,
    )


def _require_research_revision(db: Session, revision_id: UUID) -> None:
    with db.no_autoflush:
        if db.get(UnderwritingResearchVersion, revision_id) is None:
            raise NotFoundError("research revision not found")


def _require_research_family(db: Session, object_id: UUID, version_kind: str) -> None:
    with db.no_autoflush:
        if db.scalar(select(UnderwritingResearchVersion.id).where(
            UnderwritingResearchVersion.object_id == object_id,
            UnderwritingResearchVersion.version_kind == version_kind,
        ).limit(1)) is None:
            raise NotFoundError("research revision not found")


@router.post(
    "/objects",
    response_model=ResearchObjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_object(payload: ResearchObjectCreate, db: Session = Depends(get_db)) -> ResearchObjectResponse:
    value = _write(db, lambda: _service(db).add_object(ResearchObjectKind(payload.kind), payload.external_key, payload.canonical_name))
    return _object_response(value)


@router.post(
    "/object-relations",
    response_model=ObjectRelationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_object_relation(payload: ObjectRelationCreate, db: Session = Depends(get_db)) -> ObjectRelationResponse:
    value = _write(db, lambda: _service(db).link_objects(payload.parent_id, payload.child_id, payload.relation_type))
    return _relation_response(value)


@router.post(
    "/historical-bases",
    response_model=BasisResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_basis(payload: BasisCreate, db: Session = Depends(get_db)) -> BasisResponse:
    value = _write(db, lambda: _service(db).add_basis(HistoricalBasisInput(payload.cutoff, payload.price_as_of, payload.source_manifest_hash)))
    return _basis_response(value)


@router.post(
    "/mandates",
    response_model=MandateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_mandate(payload: MandateCreate, db: Session = Depends(get_db)) -> MandateResponse:
    value = _write(db, lambda: _service(db).append_mandate(InvestmentMandateInput(payload.mandate_key, payload.horizon_years, payload.base_currency, payload.required_return, payload.permanent_loss_limit, tuple(payload.comparison_set)), payload.expected_parent_id))
    return _mandate_response(value)


@router.post(
    "/objects/{object_id}/ledger-entries",
    response_model=LedgerEntryResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_ledger_entry(object_id: UUID, payload: LedgerEntryCreate, db: Session = Depends(get_db)) -> LedgerEntryResponse:
    value = _write(db, lambda: _service(db).append_ledger_entry(object_id, payload.basis_id, LedgerEntryInput(LedgerKind(payload.ledger_kind), payload.family_key, payload.entry_type, payload.payload, payload.effective_at, payload.available_at, payload.source_boundary), payload.expected_parent_id))
    return _ledger_response(value)


@router.post(
    "/objects/{object_id}/answerability",
    response_model=AnswerabilityResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_answerability(object_id: UUID, payload: AnswerabilityCreate, db: Session = Depends(get_db)) -> AnswerabilityResponse:
    value = _write(db, lambda: _service(db).record_answerability(object_id, payload.basis_id, tuple(BlockerCode(value) for value in payload.hard_blockers), tuple(payload.research_debt_keys), payload.resolvable_within_mandate, EligibleAction(payload.requested_action), tuple(payload.resolution_requirements), payload.expected_parent_id))
    return _answerability_response(value)


@router.get(
    "/objects/{object_id}/snapshots/{basis_id}",
    response_model=SnapshotResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_snapshot(object_id: UUID, basis_id: UUID, db: Session = Depends(get_db)) -> SnapshotResponse:
    try:
        snapshot = _service(db).snapshot(object_id, basis_id)
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return SnapshotResponse(
        object_id=snapshot.object_id,
        basis_id=snapshot.basis_id,
        cutoff=snapshot.cutoff,
        entries=[_ledger_response(entry) for entry in snapshot.entries],
        snapshot_hash=snapshot.snapshot_hash,
    )


@router.get(
    "/objects/{object_id}/research-versions/{version_kind}",
    response_model=ResearchRevisionHistoryResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_research_revision_history(
    object_id: UUID, version_kind: str, db: Session = Depends(get_db),
) -> ResearchRevisionHistoryResponse:
    _require_research_family(db, object_id, version_kind)
    try:
        history = ResearchRevisionDiffService(db).revision_history(object_id, version_kind)
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return ResearchRevisionHistoryResponse(
        object_id=history.object_id,
        version_kind=history.version_kind,
        revisions=[_revision_response(item) for item in history.revisions],
    )


@router.get(
    "/research-versions/{revision_id}",
    response_model=ResearchRevisionResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_research_revision(revision_id: UUID, db: Session = Depends(get_db)) -> ResearchRevisionResponse:
    _require_research_revision(db, revision_id)
    try:
        return _revision_response(ResearchRevisionDiffService(db).revision_summary(revision_id))
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc


@router.get(
    "/research-versions/{from_revision_id}/diff/{to_revision_id}",
    response_model=ResearchRevisionDiffResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_research_revision_diff(
    from_revision_id: UUID, to_revision_id: UUID, db: Session = Depends(get_db),
) -> ResearchRevisionDiffResponse:
    _require_research_revision(db, from_revision_id)
    _require_research_revision(db, to_revision_id)
    try:
        result = ResearchRevisionDiffService(db).revision_diff(from_revision_id, to_revision_id)
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return ResearchRevisionDiffResponse(
        from_revision_id=result.from_revision_id,
        to_revision_id=result.to_revision_id,
        from_content_hash=result.from_content_hash,
        to_content_hash=result.to_content_hash,
        entries=[_revision_change_response(item) for item in result.entries],
        diff_hash=result.diff_hash,
    )


@router.get(
    "/research-archives",
    response_model=ResearchArchiveListResponse,
    responses={422: {"model": UnderwritingErrorEnvelope}},
)
def get_research_archives(
    query: str | None = None,
    kind: ResearchObjectKind | None = None,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
) -> ResearchArchiveListResponse:
    try:
        page = ResearchRevisionDiffService(db).research_archives(
            query=query,
            kind=kind.value if kind is not None else None,
            limit=limit,
            cursor=cursor,
        )
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return ResearchArchiveListResponse(
        items=[ResearchArchiveItemResponse(**asdict(item)) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/objects/{object_id}/economic-models/{basis_id}",
    response_model=EvidenceOnlyEconomicModelResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_evidence_only_economic_model(object_id: UUID, basis_id: UUID, db: Session = Depends(get_db)) -> EvidenceOnlyEconomicModelResponse:
    research = db.scalar(select(UnderwritingResearchVersion).where(
        UnderwritingResearchVersion.object_id == object_id,
        UnderwritingResearchVersion.basis_id == basis_id,
        UnderwritingResearchVersion.version_kind == "catl_economic_model_evidence_only",
    ))
    basis = db.get(UnderwritingHistoricalBasis, basis_id)
    if research is None or basis is None:
        raise NotFoundError("economic model not found")
    manifest = db.scalar(select(UnderwritingSourceManifestVersion).where(UnderwritingSourceManifestVersion.basis_id == basis_id))
    answerability = db.scalar(select(UnderwritingAnswerabilityEvaluation).where(
        UnderwritingAnswerabilityEvaluation.object_id == object_id,
        UnderwritingAnswerabilityEvaluation.basis_id == basis_id,
    ))
    if manifest is None or answerability is None:
        raise NotFoundError("economic model not found")
    observations = list(db.scalars(select(UnderwritingMetricObservation).where(UnderwritingMetricObservation.basis_id == basis_id).order_by(UnderwritingMetricObservation.metric_key, UnderwritingMetricObservation.id)))
    definitions = {row.id: row for row in db.scalars(select(UnderwritingMetricDefinitionVersion).where(UnderwritingMetricDefinitionVersion.basis_id == basis_id))}
    industry_id = db.scalar(select(UnderwritingObjectRelation.parent_id).where(
        UnderwritingObjectRelation.child_id == object_id,
        UnderwritingObjectRelation.relation_type == "industry_exposes_company",
    ))
    gaps = list(db.scalars(select(UnderwritingLedgerEntry).where(
        UnderwritingLedgerEntry.object_id == industry_id,
        UnderwritingLedgerEntry.basis_id == basis_id,
        UnderwritingLedgerEntry.entry_type == "unknown_evidence_gap",
    ))) if industry_id is not None else []
    gap_payloads = [entry.payload for entry in gaps]
    response_observations = [EconomicObservationResponse(
        metric_key=row.metric_key, value=row.value, unit=row.unit, source_id=row.source_id,
        source_locator=row.source_locator, available_at=row.available_at,
        observation_status=str(dict(row.dimensions).get("disclosure_status", "reported")),
        source_role=definitions[row.definition_id].source_role, dimensions=dict(row.dimensions),
    ) for row in observations]
    response_observations.extend(EconomicObservationResponse(
        metric_key=str(item["metric_key"]), value=None, unit=str(item["unit"]), source_id=str(item["source_id"]),
        source_locator=str(item["source_locator"]), available_at=datetime.fromisoformat(str(item["available_at"])),
        observation_status=str(item["observation_status"]), source_role=str(item["source_role"]), dimensions=dict(item["dimensions"]),
    ) for item in gap_payloads)
    response_observations.sort(key=lambda item: (item.metric_key, item.source_id, item.available_at))
    mechanisms = list(db.scalars(select(UnderwritingMechanismPackVersion).where(
        UnderwritingMechanismPackVersion.object_id == object_id,
        UnderwritingMechanismPackVersion.basis_id == basis_id,
        UnderwritingMechanismPackVersion.status == "candidate",
    )))
    candidates = sorted((CandidateMechanismResponse(key=row.mechanism_key, status="candidate", source_ids=list(row.source_ids), formula=str(row.payload["formula"])) for row in mechanisms), key=lambda item: item.key)
    return EvidenceOnlyEconomicModelResponse(
        object_id=object_id, basis_id=basis_id, cutoff=basis.cutoff, research_version_id=research.id,
        snapshot_hash=next((item.removeprefix("semantic_snapshot:") for item in research.parent_ids if item.startswith("semantic_snapshot:")), research.content_hash), sources=[EconomicSourceResponse(source_id=str(item["source_id"]), title=str(item["title"]), locator=str(item["locator"]), authority=str(item["authority"])) for item in sorted(manifest.manifest["sources"], key=lambda item: str(item["source_id"]))],
        observations=response_observations, candidate_mechanisms=candidates, formal_mechanisms=[],
        answerability=_answerability_response(answerability), eligible_action="wait_for_validation", valuation=None,
    )
