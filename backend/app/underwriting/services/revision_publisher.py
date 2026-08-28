"""Preview and atomically publish immutable product research revisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    AssessmentConfidence,
    AssessmentDirection,
    AssessmentState,
    ProductHistoricalBasisInput,
    ProductRevisionView,
    PublicationStatus,
    RevisionBoundaryInput,
    product_historical_basis_content_hash,
)
from app.underwriting.domain.types import AnswerabilityState, ResearchObjectKind
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    price_snapshot_hash,
    security_rights_hash,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftContent


PRODUCT_MANIFEST_SCHEMA = "underwriting.research-revision-manifest.v1"
PRODUCT_BOUNDARY_SCHEMA = "product.revision-boundary.v1"
PRODUCT_REVISION_KIND = "independent_research"
_ASSESSMENT_SCHEMA = "product.research-assessment.v1"
_BLOCKERS = ("increment_a_directional_assessment_not_implemented",)
_REQUIREMENTS = (
    "complete auditable forecast, valuation, and directional assessment gates",
)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _uuid(value: UUID, field: str) -> UUID:
    if type(value) is not UUID:
        raise ValidationError(f"{field} must be a UUID")
    return value


def canonical_boundary_payload(
    project_id: UUID, boundary: RevisionBoundaryInput
) -> dict[str, object]:
    return {
        "schema_version": PRODUCT_BOUNDARY_SCHEMA,
        "project_id": str(project_id),
        "historical_basis_id": str(boundary.historical_basis_id),
        "mandate_id": str(boundary.mandate_id),
        "scope_id": str(boundary.scope_id),
        "agenda_id": str(boundary.agenda_id),
        "price_snapshot_ids": [str(value) for value in boundary.price_snapshot_ids],
        "fx_snapshot_ids": [str(value) for value in boundary.fx_snapshot_ids],
        "capital_structure_snapshot_id": str(boundary.capital_structure_snapshot_id),
        "security_rights_ids": [str(value) for value in boundary.security_rights_ids],
        "parent_revision_id": (
            str(boundary.parent_revision_id)
            if boundary.parent_revision_id is not None
            else None
        ),
    }


def canonical_assessment_payload(
    project_id: UUID,
    *,
    parent_assessment_id: UUID | None,
    blockers: tuple[str, ...] = _BLOCKERS,
    resolution_requirements: tuple[str, ...] = _REQUIREMENTS,
    next_review_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "schema_version": _ASSESSMENT_SCHEMA,
        "project_id": str(project_id),
        "parent_assessment_id": (
            str(parent_assessment_id) if parent_assessment_id is not None else None
        ),
        "answerability": AnswerabilityState.NOT_ANSWERABLE.value,
        "direction": None,
        "confidence": None,
        "publication_status": PublicationStatus.USER_FROZEN.value,
        "blockers": list(blockers),
        "resolution_requirements": list(resolution_requirements),
        "next_review_at": (
            _stored_utc(next_review_at).isoformat()
            if next_review_at is not None
            else None
        ),
    }


@dataclass(frozen=True, slots=True)
class AssessmentPreview:
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus
    blockers: tuple[str, ...]
    resolution_requirements: tuple[str, ...]
    next_review_at: datetime | None
    parent_assessment_id: UUID | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class PublicationPreview:
    project_id: UUID
    expected_lock_version: int
    boundary_as_of: datetime
    assessment: AssessmentPreview
    boundary: RevisionBoundaryInput
    boundary_hash: str
    manifest: dict[str, object]
    manifest_hash: str


class RevisionPublisher:
    """Validate a selected draft graph and publish it in one savepoint."""

    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._session = session
        self.repository = ProductRepository(session)
        self._market = MarketSnapshotService(session, now)
        self._now = now

    @staticmethod
    def _lock_version(value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValidationError("expected_lock_version must be a positive integer")
        return value

    @staticmethod
    def _content(value: object) -> WorkspaceDraftContent:
        try:
            serialized = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            return WorkspaceDraftContent.model_validate_json(serialized)
        except (PydanticValidationError, TypeError, ValueError) as exc:
            raise ValidationError("workspace draft content is invalid") from exc

    def _draft(self, project_id: UUID, expected_lock_version: int):
        draft = self.repository.workspace_draft(project_id)
        if draft is None:
            raise ValidationError("workspace draft does not exist for project")
        if draft.lock_version != expected_lock_version:
            raise ConflictError("workspace draft changed; reload before publishing")
        return draft, self._content(draft.content)

    @staticmethod
    def _required_id(value: UUID | None, field: str) -> UUID:
        if value is None:
            raise ValidationError(f"draft {field} is required")
        return value

    def _parent(self, project_id: UUID, base_revision_id: UUID | None):
        head = self.repository.product_revision_head(project_id)
        if head is None:
            if base_revision_id is not None:
                raise ConflictError("draft base revision is not the project chain head")
            return None, None
        if base_revision_id != head.id:
            raise ConflictError("draft base revision is not the project chain head")
        if (
            head.project_id != project_id
            or head.version_kind != PRODUCT_REVISION_KIND
            or head.manifest_schema != PRODUCT_MANIFEST_SCHEMA
            or head.publication_status != PublicationStatus.USER_FROZEN.value
        ):
            raise ValidationError("draft parent must be a formal product revision")
        from app.underwriting.services.research_revision_diff import (
            ProductResearchRevisionSummary,
            ResearchRevisionDiffService,
        )

        parent_summary = ResearchRevisionDiffService(self._session).revision_summary(
            head.id
        )
        if (
            not isinstance(parent_summary, ProductResearchRevisionSummary)
            or parent_summary.project_id != project_id
        ):
            raise ValidationError("draft parent must be a verified product revision")
        parent_manifest = self.repository.manifest(head.manifest_id)
        if parent_manifest is None or parent_manifest.project_id != project_id:
            raise ValidationError("draft parent product manifest is missing")
        raw_assessment_id = parent_manifest.manifest.get("assessment_ref")
        try:
            assessment_id = UUID(raw_assessment_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError(
                "draft parent assessment reference is malformed"
            ) from exc
        return head, assessment_id

    def _validate_identity_boundary(
        self,
        *,
        company_id: UUID,
        target_security_ids: tuple[UUID, ...],
        boundary_at: datetime,
    ) -> None:
        for object_id in (company_id, *target_security_ids):
            identity = self.repository.effective_identity(object_id, boundary_at)
            research_object = self.repository.object(object_id)
            if identity is None or research_object is None:
                raise ValidationError(
                    "project Company and target Securities require effective identities "
                    "at the frozen market instant"
                )
            identity_hash = canonical_hash(
                {
                    "schema_version": "product.object-identity.v1",
                    "object_id": str(object_id),
                    "kind": research_object.kind,
                    "canonical_name": identity.canonical_name,
                    "symbol": identity.symbol,
                    "exchange": identity.exchange,
                    "share_class": identity.share_class,
                    "trading_currency": identity.trading_currency,
                    "effective_from": _stored_utc(identity.effective_from).isoformat(),
                    "effective_to": (
                        _stored_utc(identity.effective_to).isoformat()
                        if identity.effective_to is not None
                        else None
                    ),
                }
            )
            if identity.content_hash != identity_hash:
                raise ValidationError("effective object identity hash mismatch")

    def _validate_mandate_time(self, mandate, boundary_at: datetime) -> None:
        if (
            mandate.effective_at is None
            or _stored_utc(mandate.effective_at) > boundary_at
        ):
            raise ValidationError(
                "mandate is not effective at the frozen market instant"
            )
        if (
            mandate.expires_at is not None
            and _stored_utc(mandate.expires_at) <= boundary_at
        ):
            raise ValidationError("mandate expired before the frozen market instant")

    @staticmethod
    def _project_references(project, memberships):
        if not memberships:
            raise ValidationError("research project must contain target Securities")
        membership_ids = tuple(row.id for row in memberships)
        security_ids = tuple(sorted((row.security_id for row in memberships), key=str))
        if (
            len(membership_ids) != len(set(membership_ids))
            or len(security_ids) != len(set(security_ids))
            or any(row.project_id != project.id for row in memberships)
            or project.content_hash
            != canonical_hash(
                {
                    "schema_version": "product.research-project.v1",
                    "primary_company_id": str(project.primary_company_id),
                    "target_security_ids": [str(value) for value in security_ids],
                }
            )
            or any(
                row.content_hash
                != canonical_hash(
                    {
                        "schema_version": "product.research-project-security.v1",
                        "project_id": str(project.id),
                        "security_id": str(row.security_id),
                    }
                )
                for row in memberships
            )
        ):
            raise ValidationError("research project membership identity is invalid")
        return (
            {"project_id": str(project.id), "content_hash": project.content_hash},
            [
                {
                    "membership_id": str(row.id),
                    "security_id": str(row.security_id),
                    "content_hash": row.content_hash,
                }
                for row in sorted(memberships, key=lambda item: str(item.id))
            ],
            security_ids,
        )

    def _validate_frozen_hashes(
        self, project_id: UUID, boundary: RevisionBoundaryInput, mandate
    ) -> None:
        basis = self.repository.product_basis(boundary.historical_basis_id)
        scope = self.repository.scope(project_id, boundary.scope_id)
        agenda = self.repository.agenda(project_id, boundary.agenda_id)
        prices = self.repository.prices(boundary.price_snapshot_ids)
        fxs = self.repository.fxs(boundary.fx_snapshot_ids)
        capital = self.repository.capital_structure(
            boundary.capital_structure_snapshot_id
        )
        rights = self.repository.security_rights_many(boundary.security_rights_ids)
        if basis is None or scope is None or agenda is None or capital is None:
            raise ValidationError("publication boundary reference is missing")
        try:
            basis_hash = product_historical_basis_content_hash(
                ProductHistoricalBasisInput(
                    cutoff_at=_stored_utc(basis.cutoff),
                    source_manifest_hash=basis.source_manifest_hash,
                    definition_bundle_hash=basis.definition_bundle_hash,
                    parser_bundle_hash=basis.parser_bundle_hash,
                )
            )
        except ValueError as exc:
            raise ValidationError(
                "publication boundary reference hash mismatch"
            ) from exc
        mandate_hash = canonical_hash(
            {
                "schema_version": "product.investment-mandate.v1",
                "project_id": str(project_id),
                "mandate_key": mandate.mandate_key,
                "horizon_years": mandate.horizon_years,
                "base_currency": mandate.base_currency,
                "required_return": format(mandate.required_return, ".8f"),
                "permanent_loss_limit": format(mandate.permanent_loss_limit, ".8f"),
                "comparison_set": list(mandate.comparison_set),
                "benchmark_key": mandate.benchmark_key,
                "required_excess_return": (
                    format(mandate.required_excess_return, ".8f")
                    if mandate.required_excess_return is not None
                    else None
                ),
                "effective_at": (
                    _stored_utc(mandate.effective_at).isoformat()
                    if mandate.effective_at is not None
                    else None
                ),
                "expires_at": (
                    _stored_utc(mandate.expires_at).isoformat()
                    if mandate.expires_at is not None
                    else None
                ),
            }
        )
        scope_hash = canonical_hash(
            {
                "schema_version": "product.research-scope.v1",
                "project_id": str(project_id),
                "scope": scope.payload,
            }
        )
        agenda_hash = canonical_hash(
            {
                "schema_version": "product.research-agenda.v1",
                "project_id": str(project_id),
                "scope_id": str(scope.id),
                "items": agenda.payload.get("items"),
                "generator": agenda.generator_provenance,
            }
        )
        if (
            basis.content_hash != basis_hash
            or mandate.content_hash != mandate_hash
            or scope.content_hash != scope_hash
            or agenda.content_hash != agenda_hash
            or any(row.content_hash != price_snapshot_hash(row) for row in prices)
            or any(row.content_hash != fx_snapshot_hash(row) for row in fxs)
            or capital.content_hash != capital_structure_snapshot_hash(capital)
            or any(row.content_hash != security_rights_hash(row) for row in rights)
        ):
            raise ValidationError("publication boundary reference hash mismatch")

    def preview(
        self, project_id: UUID, expected_lock_version: int
    ) -> PublicationPreview:
        project_id = _uuid(project_id, "project_id")
        expected_lock_version = self._lock_version(expected_lock_version)
        with self._session.no_autoflush:
            if CompanyResearchRepository(self._session).preparation_for_project(
                project_id
            ) is not None:
                raise ValidationError(
                    "generic publication is not available for company research projects"
                )
            record = self.repository.project(project_id)
            if record is None:
                raise ValidationError("research project does not exist")
            project, _ = record
            project_ref, membership_refs, project_security_ids = (
                self._project_references(
                    project, self.repository.project_memberships(project_id)
                )
            )
            company = self.repository.object(project.primary_company_id)
            if company is None or company.kind != ResearchObjectKind.COMPANY.value:
                raise ValidationError("project primary object must be a Company")
            draft, content = self._draft(project_id, expected_lock_version)
            mandate_id = self._required_id(content.mandate_id, "mandate_id")
            scope_id = self._required_id(content.scope_id, "scope_id")
            agenda_id = self._required_id(content.agenda_id, "agenda_id")
            historical_basis_id = self._required_id(
                content.historical_basis_id, "historical_basis_id"
            )
            capital_id = self._required_id(
                content.capital_structure_snapshot_id,
                "capital_structure_snapshot_id",
            )
            if not content.price_snapshot_ids:
                raise ValidationError("draft price_snapshot_ids are required")
            if not content.security_rights_ids:
                raise ValidationError("draft security_rights_ids are required")
            prices = self.repository.prices(content.price_snapshot_ids)
            if len(prices) != len(content.price_snapshot_ids):
                raise ValidationError("boundary price snapshot does not exist")
            boundary_at = max(_stored_utc(row.market_at) for row in prices)
            parent, parent_assessment_id = self._parent(
                project_id, draft.base_revision_id
            )
            boundary = RevisionBoundaryInput(
                historical_basis_id,
                mandate_id,
                scope_id,
                agenda_id,
                tuple(sorted(content.price_snapshot_ids, key=str)),
                tuple(sorted(content.fx_snapshot_ids, key=str)),
                capital_id,
                tuple(sorted(content.security_rights_ids, key=str)),
                parent.id if parent is not None else None,
            )
            mandate = self.repository.product_mandate(project_id, mandate_id)
            if mandate is None:
                raise ValidationError("mandate must belong to the project")
            self._validate_mandate_time(mandate, boundary_at)
            context = self._market.boundary_context(
                project_id,
                boundary,
                as_of=boundary_at,
                model_currency=mandate.base_currency,
            )
            self._validate_frozen_hashes(project_id, boundary, mandate)
            self._validate_identity_boundary(
                company_id=project.primary_company_id,
                target_security_ids=project_security_ids,
                boundary_at=boundary_at,
            )
            if set(context.target_security_ids) - set(project_security_ids):
                raise ValidationError("scope target Securities must belong to project")

            blockers = tuple(sorted(set(_BLOCKERS)))
            requirements = tuple(sorted(set(_REQUIREMENTS)))
            state = AssessmentState(
                AnswerabilityState.NOT_ANSWERABLE,
                None,
                None,
                PublicationStatus.USER_FROZEN,
            )
            assessment_hash = canonical_hash(
                canonical_assessment_payload(
                    project_id,
                    parent_assessment_id=parent_assessment_id,
                    blockers=blockers,
                    resolution_requirements=requirements,
                )
            )
            assessment = AssessmentPreview(
                state.answerability,
                state.direction,
                state.confidence,
                state.publication_status,
                blockers,
                requirements,
                None,
                parent_assessment_id,
                assessment_hash,
            )
            boundary_payload = canonical_boundary_payload(project_id, boundary)
            manifest: dict[str, object] = {
                "schema_version": PRODUCT_MANIFEST_SCHEMA,
                "project_id": str(project_id),
                "project_ref": project_ref,
                "project_membership_refs": membership_refs,
                "primary_object_id": str(project.primary_company_id),
                "boundary_ref": "$boundary",
                "mandate_id": str(boundary.mandate_id),
                "scope_id": str(boundary.scope_id),
                "agenda_id": str(boundary.agenda_id),
                "historical_basis_id": str(boundary.historical_basis_id),
                "price_snapshot_ids": [
                    str(value) for value in boundary.price_snapshot_ids
                ],
                "fx_snapshot_ids": [str(value) for value in boundary.fx_snapshot_ids],
                "capital_structure_snapshot_id": str(
                    boundary.capital_structure_snapshot_id
                ),
                "security_rights_ids": [
                    str(value) for value in boundary.security_rights_ids
                ],
                "market_snapshot_refs": sorted(
                    [f"price:{value}" for value in boundary.price_snapshot_ids]
                    + [f"fx:{value}" for value in boundary.fx_snapshot_ids]
                    + [f"capital_structure:{boundary.capital_structure_snapshot_id}"]
                    + [
                        f"security_rights:{value}"
                        for value in boundary.security_rights_ids
                    ]
                ),
                "model_refs": [],
                "assessment_ref": "$assessment",
                "memo_ref": None,
                "parent_revision_id": (
                    str(boundary.parent_revision_id)
                    if boundary.parent_revision_id is not None
                    else None
                ),
            }
            return PublicationPreview(
                project_id,
                expected_lock_version,
                boundary_at,
                assessment,
                boundary,
                canonical_hash(boundary_payload),
                manifest,
                canonical_hash(manifest),
            )

    @staticmethod
    def _idempotency_key(value: object) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValidationError("idempotency_key must not be empty")
        if len(normalized) > 120:
            raise ValidationError("idempotency_key must be at most 120 characters")
        return normalized

    def _created_at(self) -> datetime:
        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError("publication clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _view_from_summary(summary) -> ProductRevisionView:
        return ProductRevisionView(
            summary.id,
            summary.project_id,
            summary.boundary_id,
            summary.manifest_hash,
            summary.price_snapshot_ids,
            summary.fx_snapshot_ids,
            summary.capital_structure_snapshot_id,
            summary.security_rights_ids,
            summary.answerability,
            summary.direction,
            summary.confidence,
            summary.publication_status,
        )

    def _verified_existing(self, project_id: UUID, key: str):
        with self._session.no_autoflush:
            existing = self.repository.revision_for_idempotency(project_id, key)
            if existing is None:
                return None
            from app.underwriting.services.research_revision_diff import (
                ProductResearchRevisionSummary,
                ResearchRevisionDiffService,
            )

            summary = ResearchRevisionDiffService(self._session).revision_summary(
                existing.id
            )
            if not isinstance(summary, ProductResearchRevisionSummary):
                raise ValidationError(
                    "idempotency key does not reference a product revision"
                )
            return self._view_from_summary(summary)

    def publish(
        self,
        project_id: UUID,
        expected_lock_version: int,
        *,
        idempotency_key: str,
    ) -> ProductRevisionView:
        project_id = _uuid(project_id, "project_id")
        expected_lock_version = self._lock_version(expected_lock_version)
        key = self._idempotency_key(idempotency_key)
        with self._session.no_autoflush:
            self.repository.lock_project(project_id)
            if CompanyResearchRepository(self._session).preparation_for_project(
                project_id
            ) is not None:
                raise ValidationError(
                    "generic publication is not available for company research projects"
                )
            if existing := self._verified_existing(project_id, key):
                return existing
        try:
            with self._session.begin_nested():
                preview = self.preview(project_id, expected_lock_version)
                created_at = self._created_at()
                assessment = self.repository.append_assessment(
                    project_id=project_id,
                    answerability=preview.assessment.answerability.value,
                    direction=None,
                    confidence=None,
                    publication_status=preview.assessment.publication_status.value,
                    blockers=list(preview.assessment.blockers),
                    resolution_requirements=list(
                        preview.assessment.resolution_requirements
                    ),
                    next_review_at=None,
                    content_hash=preview.assessment.content_hash,
                    expected_parent_id=preview.assessment.parent_assessment_id,
                    created_at=created_at,
                )
                boundary = self.repository.append_boundary(
                    project_id=project_id,
                    value=preview.boundary,
                    schema_version=PRODUCT_BOUNDARY_SCHEMA,
                    content_hash=preview.boundary_hash,
                    created_at=created_at,
                )
                manifest_payload = dict(preview.manifest)
                manifest_payload["boundary_ref"] = str(boundary.id)
                manifest_payload["assessment_ref"] = str(assessment.id)
                manifest_hash = canonical_hash(manifest_payload)
                manifest = self.repository.append_manifest(
                    project_id=project_id,
                    boundary_id=boundary.id,
                    idempotency_key=key,
                    manifest=manifest_payload,
                    content_hash=manifest_hash,
                    created_at=created_at,
                )
                revision = self.repository.append_product_revision(
                    project_id=project_id,
                    object_id=manifest_payload["primary_object_id"],
                    basis_id=preview.boundary.historical_basis_id,
                    boundary_id=boundary.id,
                    manifest_id=manifest.id,
                    manifest_hash=manifest_hash,
                    assessment_id=assessment.id,
                    parent_revision_id=preview.boundary.parent_revision_id,
                    created_at=created_at,
                )
                draft = self.repository.reset_draft_after_publish(
                    project_id=project_id,
                    expected_lock_version=expected_lock_version,
                    base_revision_id=revision.id,
                    updated_at=created_at,
                )
                if draft.base_revision_id != revision.id:
                    raise ConflictError("published draft base was not reset")
                from app.underwriting.services.research_revision_diff import (
                    ResearchRevisionDiffService,
                )

                published_view = self._view_from_summary(
                    ResearchRevisionDiffService(self._session).revision_summary(
                        revision.id
                    )
                )
            return published_view
        except IntegrityError as exc:
            if existing := self._verified_existing(project_id, key):
                return existing
            raise ConflictError("research revision publication conflicted") from exc
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc
