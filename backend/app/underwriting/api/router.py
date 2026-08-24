"""HTTP boundary for the underwriting research kernel."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ValidationFailedError
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
    FrozenAnswerabilityResponse,
    FrozenUnknownEvidenceGapResponse,
    ResearchRevisionBoundaryResponse,
    CandidateEvidenceAnswerabilityResponse,
    CandidateEvidenceDossierResponse,
    CandidateEvidenceDossierCanonicalPayloadResponse,
    CandidateEvidenceItemResponse,
    CandidateEvidenceDossierParentPreimageResponse,
    CandidateEvidenceManifestParentPreimageResponse,
    CandidateEvidenceParentResponse,
    CandidateEvidenceReviewParentPreimageResponse,
    CandidateEvidenceResponse,
    CandidateEvidenceReviewResponse,
)
from app.errors import NotFoundError
from app.underwriting.api.product_router import router as product_router
from app.underwriting.api.transactions import commit_write
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
from app.underwriting.services.kernel import UnderwritingKernelService
from app.underwriting.services.research_revision_diff import (
    ResearchRevisionDiffService,
    RevisionArtifactRef,
    RevisionChange,
    FrozenAnswerability,
    FrozenUnknownEvidenceGap,
    ResearchRevisionBoundary,
    ResearchRevisionSummary,
    ProductResearchRevisionSummary,
    CandidateEvidenceRead,
)


router = APIRouter(prefix="/api/underwriting/v1", tags=["underwriting-v1"])
router.include_router(product_router)
WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
READ_ERROR_RESPONSES = {404: {"model": UnderwritingErrorEnvelope}, 422: {"model": UnderwritingErrorEnvelope}}


def _service(db: Session) -> UnderwritingKernelService:
    return UnderwritingKernelService(db, now=lambda: datetime.now(UTC))


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


def _revision_response(
    value: ResearchRevisionSummary | ProductResearchRevisionSummary,
) -> ResearchRevisionResponse:
    if isinstance(value, ProductResearchRevisionSummary):
        raise ValidationError(
            "product research revision requires the product endpoint"
        )
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


def _frozen_answerability_response(value: FrozenAnswerability) -> FrozenAnswerabilityResponse:
    return FrozenAnswerabilityResponse(
        reference=UUID(value.reference),
        content_hash=value.content_hash,
        state=value.state,
        blockers=list(value.blockers),
        research_debt_keys=list(value.research_debt_keys),
        resolvable_within_mandate=value.resolvable_within_mandate,
        resolution_requirements=list(value.resolution_requirements),
    )


def _frozen_unknown_evidence_gap_response(
    value: FrozenUnknownEvidenceGap,
) -> FrozenUnknownEvidenceGapResponse:
    return FrozenUnknownEvidenceGapResponse(
        reference=UUID(value.reference),
        content_hash=value.content_hash,
        metric_key=value.metric_key,
        unit=value.unit,
        source_id=value.source_id,
        source_locator=value.source_locator,
        observed_start=value.observed_start,
        observed_end=value.observed_end,
        effective_at=value.effective_at,
        available_at=value.available_at,
        source_role=value.source_role,
        observation_status=value.observation_status,
        dimensions=dict(value.dimensions),
    )


def _revision_boundary_response(
    value: ResearchRevisionBoundary | ProductResearchRevisionSummary,
) -> ResearchRevisionBoundaryResponse:
    if isinstance(value, ProductResearchRevisionSummary):
        raise ValidationError(
            "product research revision requires the product endpoint"
        )
    revision = value.revision
    return ResearchRevisionBoundaryResponse(
        revision_id=revision.id,
        object_id=revision.object_id,
        basis_id=revision.basis_id,
        version_kind=revision.version_kind,
        content_hash=revision.content_hash,
        cutoff=revision.cutoff,
        source_manifest_hash=revision.source_manifest_hash,
        answerability=(
            _frozen_answerability_response(value.answerability)
            if value.answerability is not None
            else None
        ),
        unknown_evidence_gaps=[
            _frozen_unknown_evidence_gap_response(gap)
            for gap in value.unknown_evidence_gaps
        ],
    )


def _candidate_evidence_response(value: CandidateEvidenceRead) -> CandidateEvidenceResponse:
    """Render only the immutable candidate parents selected by the revision."""
    revision = value.revision
    dossier = value.dossier
    return CandidateEvidenceResponse(
        revision_id=revision.id,
        object_id=revision.object_id,
        basis_id=revision.basis_id,
        version_kind=revision.version_kind,
        content_hash=revision.content_hash,
        cutoff=revision.cutoff,
        source_manifest_hash=revision.source_manifest_hash,
        parent_refs=[
            CandidateEvidenceParentResponse(
                reference=UUID(parent.reference),
                artifact_type=parent.artifact_type,
                identity=parent.identity,
                content_hash=parent.content_hash,
                descriptor_preimage=(
                    CandidateEvidenceDossierParentPreimageResponse(
                        raw_content_hash=parent.raw_content_hash,
                        created_at=parent.created_at,
                    )
                    if parent.artifact_type == "candidate_dossier"
                    else CandidateEvidenceReviewParentPreimageResponse(
                        raw_content_hash=parent.raw_content_hash,
                        created_at=parent.created_at,
                        reviewed_at=parent.reviewed_at,
                    )
                    if parent.artifact_type == "candidate_review"
                    else CandidateEvidenceManifestParentPreimageResponse(
                        row_content_hash=parent.raw_content_hash,
                        manifest_hash=parent.manifest_hash,
                    )
                ),
            )
            for parent in value.parent_refs
        ],
        dossier=CandidateEvidenceDossierResponse(
            reference=UUID(dossier.reference),
            content_hash=dossier.content_hash,
            dossier_key=dossier.dossier_key,
            version=dossier.version,
            status=dossier.status,
            scope_statement=dossier.scope_statement,
            rejected_calculations=list(dossier.rejected_calculations),
            supersedes_id=dossier.supersedes_id,
            canonical_payload=CandidateEvidenceDossierCanonicalPayloadResponse.model_validate(
                dossier.canonical_payload,
            ),
        ),
        items=[
            CandidateEvidenceItemResponse(
                metric_key=item.metric_key,
                status=item.status.value,
                value=item.value,
                unit=item.unit,
                observed_start=item.observed_start,
                observed_end=item.observed_end,
                available_at=item.available_at,
                source_id=item.source_id,
                source_locator=item.source_locator,
                scope_statement=item.scope_statement,
                exclusions=list(item.exclusions),
                methodology=item.methodology,
                prohibited_splicing_declaration=item.prohibited_splicing_declaration,
                transcription_method=item.transcription_method,
                error_bound=item.error_bound,
                scenario_use=item.scenario_use,
                not_observed_declared=item.not_observed_declared,
                unknown_reason=item.unknown_reason,
            )
            for item in dossier.items
        ],
        reviews=[
            CandidateEvidenceReviewResponse(
                reference=UUID(review.reference),
                content_hash=review.content_hash,
                reviewer_identity=review.reviewer_identity,
                reviewer_role=review.reviewer_role,
                decision=review.decision,
                rationale=review.rationale,
                reviewed_at=review.reviewed_at,
            )
            for review in value.reviews
        ],
        answerability=CandidateEvidenceAnswerabilityResponse(
            reference=UUID(value.answerability.reference),
            content_hash=value.answerability.content_hash,
            state=value.answerability.state,
            research_debt_keys=list(value.answerability.research_debt_keys),
            resolution_requirements=list(value.answerability.resolution_requirements),
        ),
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
    value = commit_write(db, lambda: _service(db).add_object(ResearchObjectKind(payload.kind), payload.external_key, payload.canonical_name))
    return _object_response(value)


@router.post(
    "/object-relations",
    response_model=ObjectRelationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_object_relation(payload: ObjectRelationCreate, db: Session = Depends(get_db)) -> ObjectRelationResponse:
    value = commit_write(db, lambda: _service(db).link_objects(payload.parent_id, payload.child_id, payload.relation_type))
    return _relation_response(value)


@router.post(
    "/historical-bases",
    response_model=BasisResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_basis(payload: BasisCreate, db: Session = Depends(get_db)) -> BasisResponse:
    value = commit_write(db, lambda: _service(db).add_basis(HistoricalBasisInput(payload.cutoff, payload.price_as_of, payload.source_manifest_hash)))
    return _basis_response(value)


@router.post(
    "/mandates",
    response_model=MandateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_mandate(payload: MandateCreate, db: Session = Depends(get_db)) -> MandateResponse:
    value = commit_write(db, lambda: _service(db).append_mandate(InvestmentMandateInput(payload.mandate_key, payload.horizon_years, payload.base_currency, payload.required_return, payload.permanent_loss_limit, tuple(payload.comparison_set)), payload.expected_parent_id))
    return _mandate_response(value)


@router.post(
    "/objects/{object_id}/ledger-entries",
    response_model=LedgerEntryResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_ledger_entry(object_id: UUID, payload: LedgerEntryCreate, db: Session = Depends(get_db)) -> LedgerEntryResponse:
    value = commit_write(db, lambda: _service(db).append_ledger_entry(object_id, payload.basis_id, LedgerEntryInput(LedgerKind(payload.ledger_kind), payload.family_key, payload.entry_type, payload.payload, payload.effective_at, payload.available_at, payload.source_boundary), payload.expected_parent_id))
    return _ledger_response(value)


@router.post(
    "/objects/{object_id}/answerability",
    response_model=AnswerabilityResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_answerability(object_id: UUID, payload: AnswerabilityCreate, db: Session = Depends(get_db)) -> AnswerabilityResponse:
    value = commit_write(db, lambda: _service(db).record_answerability(object_id, payload.basis_id, tuple(BlockerCode(value) for value in payload.hard_blockers), tuple(payload.research_debt_keys), payload.resolvable_within_mandate, EligibleAction(payload.requested_action), tuple(payload.resolution_requirements), payload.expected_parent_id))
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
        object_kind=history.object_kind,
        canonical_name=history.canonical_name,
        external_key=history.external_key,
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
    "/research-versions/{revision_id}/boundary",
    response_model=ResearchRevisionBoundaryResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_research_revision_boundary(
    revision_id: UUID, db: Session = Depends(get_db),
) -> ResearchRevisionBoundaryResponse:
    """Return only the selected revision's verified, explicit boundary."""
    _require_research_revision(db, revision_id)
    try:
        return _revision_boundary_response(
            ResearchRevisionDiffService(db).revision_boundary(revision_id),
        )
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc


@router.get(
    "/research-versions/{revision_id}/candidate-evidence",
    response_model=CandidateEvidenceResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_candidate_evidence(
    revision_id: UUID, db: Session = Depends(get_db),
) -> CandidateEvidenceResponse:
    """Return the exact frozen candidate graph of the selected revision."""
    _require_research_revision(db, revision_id)
    try:
        return _candidate_evidence_response(
            ResearchRevisionDiffService(db).candidate_evidence(revision_id),
        )
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
