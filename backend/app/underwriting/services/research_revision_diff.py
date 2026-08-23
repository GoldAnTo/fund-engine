"""Read immutable underwriting research revision history.

This module is intentionally a read model.  It resolves only the parent
references stored on a historical research version; it never asks a repository
for a current/latest artefact and it owns no write operation.
"""
from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.answerability import (
    AnswerabilityInput,
    enforce_action_boundary,
    evaluate_answerability,
)
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceDossier,
    CandidateEvidenceItem,
    CandidateEvidenceReview,
)
from app.underwriting.domain.types import ResearchObjectKind
from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
)
from app.underwriting.domain.metrics import MetricObservation as DomainMetricObservation
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingHistoricalBasis,
    UnderwritingLedgerEntry,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingEvidenceCandidateDossierVersion,
    UnderwritingEvidenceCandidateReviewVersion,
    UnderwritingFalsifierVersion,
    UnderwritingForecastInputVersion,
    UnderwritingIndustryScenarioVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricDefinitionVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
    selected_candidate_replay,
    validate_candidate_dossier_governance,
    validate_candidate_review_governance,
)
from app.underwriting.services.kernel import canonical_hash, frozen_research_version_content_hash
from app.underwriting.services.candidate_evidence import INDUSTRY_EVIDENCE_CANDIDATE_KIND
from app.underwriting.services.source_freeze import freeze_manifest
from app.underwriting.services.revision_parent_seal import (
    CATL_PARENT_SET_ENTRY_TYPE,
    CATL_PARENT_SET_FAMILY,
    CATL_VERSION_KIND,
    answerability_content_hash,
    catl_answerability_parent_content_hash,
    canonical_parent_refs,
    catl_revision_content_hash,
    parent_set_semantic_hash,
    parse_catl_parent_set_seal,
)


_SNAPSHOT_TOKEN = re.compile(r"semantic_snapshot:[0-9a-f]{64}")
_ANSWERABILITY_SEAL_LEGACY = "legacy"
_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP = "generic_timestamp"
_ANSWERABILITY_SEAL_CATL_TIMESTAMP = "catl_timestamp"
_LEDGER_SEAL_LEGACY = "legacy"
_LEDGER_SEAL_GENERIC_TIMESTAMP = "generic_timestamp"


@dataclass(frozen=True, slots=True)
class RevisionArtifactRef:
    """A compact, immutable description of one frozen research parent."""

    reference: str
    artifact_type: str
    identity: str
    content_hash: str
    source_locators: tuple[str, ...]
    unit: str | None
    period_start: datetime | None
    period_end: datetime | None
    available_at: datetime | None
    status: str | None


@dataclass(frozen=True, slots=True)
class _ResearchRevisionScope:
    """The immutable scope needed to authenticate parent candidates pre-publish."""

    object_id: UUID
    basis_id: UUID
    version_kind: str


@dataclass(frozen=True, slots=True)
class ResearchRevisionSummary:
    id: UUID
    object_id: UUID
    basis_id: UUID
    version_kind: str
    sequence: int
    content_hash: str
    cutoff: datetime
    source_manifest_hash: str
    parent_refs: tuple[RevisionArtifactRef, ...]
    # Internal provenance state: this is deliberately not projected by the
    # existing history API.  A boundary reader needs it to distinguish an old
    # generic parent descriptor that never sealed answerability.created_at.
    answerability_timestamp_sealed: bool = False
    # Generic v4 descriptors also bind ledger.created_at.  Older generic
    # summaries remain readable, but an Unknown gap cannot be authenticated
    # from a descriptor that omitted the row creation timestamp.
    ledger_timestamp_sealed: bool = False


@dataclass(frozen=True, slots=True)
class FrozenAnswerability:
    """One answerability record sealed into a selected research revision.

    This is intentionally a read-only projection of a parent row.  It carries
    the row's recomputed semantic hash so callers can bind it to the selected
    revision's already-checked parent descriptor rather than treating a
    current answerability head as historical evidence.
    """

    reference: str
    content_hash: str
    state: str
    blockers: tuple[str, ...]
    research_debt_keys: tuple[str, ...]
    resolvable_within_mandate: bool
    resolution_requirements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrozenUnknownEvidenceGap:
    """One explicit Unknown evidence gap sealed into a selected revision.

    This is deliberately a strict projection of a ``reality`` ledger parent,
    not an inference from answerability, a relation, or a current ledger
    family.  A missing projected row therefore means only that this version
    did not seal a displayable gap record.
    """

    reference: str
    content_hash: str
    metric_key: str
    unit: str
    source_id: str
    source_locator: str
    observed_start: datetime
    observed_end: datetime
    effective_at: datetime
    available_at: datetime
    source_role: str
    observation_status: str
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ResearchRevisionBoundary:
    """The explicit research boundary recorded by one immutable revision."""

    revision: ResearchRevisionSummary
    answerability: FrozenAnswerability | None
    unknown_evidence_gaps: tuple[FrozenUnknownEvidenceGap, ...] = ()


@dataclass(frozen=True, slots=True)
class FrozenCandidateDossier:
    """The dossier parent selected by one candidate-only revision."""

    reference: str
    content_hash: str
    dossier_key: str
    version: int
    status: str
    scope_statement: str
    items: tuple[CandidateEvidenceItem, ...]


@dataclass(frozen=True, slots=True)
class FrozenCandidateReview:
    """One exact approved review selected by the candidate revision."""

    reference: str
    content_hash: str
    reviewer_identity: str
    reviewer_role: str
    decision: str
    rationale: str
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class FrozenCandidateAnswerability:
    """The bounded research-state parent of a candidate revision."""

    reference: str
    content_hash: str
    state: str
    research_debt_keys: tuple[str, ...]
    resolution_requirements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateEvidenceRead:
    """Strict, selected-parent projection for an evidence-candidate revision."""

    revision: ResearchRevisionSummary
    dossier: FrozenCandidateDossier
    reviews: tuple[FrozenCandidateReview, ...]
    answerability: FrozenCandidateAnswerability


@dataclass(frozen=True, slots=True)
class RevisionHistory:
    object_id: UUID
    object_kind: ResearchObjectKind
    canonical_name: str
    external_key: str
    version_kind: str
    revisions: tuple[ResearchRevisionSummary, ...]


@dataclass(frozen=True, slots=True)
class ResearchArchiveItem:
    """One immutable object/version-kind family in the research archive.

    An unreadable family deliberately keeps only persisted identity and count.
    It must never expose a partially verified head as though it were a valid
    historical research conclusion.
    """

    object_id: UUID
    object_kind: str
    canonical_name: str
    external_key: str
    version_kind: str
    version_count: int
    lineage_state: str
    latest_revision_id: UUID | None
    latest_sequence: int | None
    cutoff: datetime | None
    source_manifest_hash: str | None


@dataclass(frozen=True, slots=True)
class ResearchArchivePage:
    items: tuple[ResearchArchiveItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RevisionChange:
    """One typed difference between two sealed revision parent sets."""

    group: str
    change_type: str
    artifact_type: str
    identity: str
    before: RevisionArtifactRef | None
    after: RevisionArtifactRef | None

    def refs(self) -> tuple[RevisionArtifactRef, ...]:
        """Return the selected historical artefacts, in before/after order."""
        return tuple(ref for ref in (self.before, self.after) if ref is not None)


@dataclass(frozen=True, slots=True)
class ResearchRevisionDiff:
    """Deterministic, ancestor-only comparison of two historical revisions."""

    from_revision_id: UUID
    to_revision_id: UUID
    from_content_hash: str
    to_content_hash: str
    entries: tuple[RevisionChange, ...]
    diff_hash: str


class ResearchRevisionDiffService:
    """Read the existing ``uw_research_versions`` successor chain exactly."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _stored_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _iso(value: datetime) -> str:
        normalized = ResearchRevisionDiffService._stored_datetime(value)
        assert normalized is not None
        return normalized.isoformat()

    @staticmethod
    def _canonical_decimal(value: Decimal) -> Decimal:
        """Undo database ``NUMERIC`` scale padding before re-sealing an observation."""
        if not value.is_finite():
            raise ValidationError("research revision parent has non-finite metric content")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return Decimal(text or "0")

    @staticmethod
    def _parent_error(reason: str) -> ValidationError:
        return ValidationError(f"research revision parent {reason}")

    @staticmethod
    def _manifest_locators(manifest: Mapping[str, object]) -> tuple[str, ...]:
        sources = manifest.get("sources")
        if not isinstance(sources, list):
            return ()
        return tuple(sorted({
            locator.strip()
            for source in sources
            if isinstance(source, Mapping)
            and isinstance(locator := source.get("locator"), str)
            and locator.strip()
        }))

    def _manifest_for_id(self, manifest_id: UUID) -> UnderwritingSourceManifestVersion | None:
        return self._session.get(UnderwritingSourceManifestVersion, manifest_id)

    @staticmethod
    def _manifest_datetime(value: object, field: str) -> datetime:
        if not isinstance(value, str):
            raise ValidationError(f"research revision parent has malformed {field}")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(f"research revision parent has malformed {field}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValidationError(f"research revision parent has malformed {field}")
        return parsed.astimezone(UTC)

    def _basis_for_id(self, basis_id: UUID) -> UnderwritingHistoricalBasis:
        basis = self._session.get(UnderwritingHistoricalBasis, basis_id)
        if basis is None:
            raise ValidationError("research revision basis is missing")
        return basis

    def _basis_cutoff(self, basis_id: UUID) -> datetime:
        return self._stored_datetime(self._basis_for_id(basis_id).cutoff)

    def _require_available_at_cutoff(self, value: datetime, cutoff: datetime, field: str) -> None:
        if self._stored_datetime(value) > cutoff:
            raise self._parent_error(f"{field} is unavailable at research basis cutoff")

    def _verified_manifest(self, manifest_id: UUID, basis_id: UUID) -> UnderwritingSourceManifestVersion:
        manifest = self._manifest_for_id(manifest_id)
        if manifest is None or manifest.basis_id != basis_id:
            raise self._parent_error("has an invalid source-manifest lineage")
        basis = self._basis_for_id(basis_id)
        cutoff = self._stored_datetime(basis.cutoff)
        if not isinstance(manifest.manifest, Mapping):
            raise self._parent_error("has a malformed source-manifest lineage")
        manifest_cutoff = self._manifest_datetime(manifest.manifest.get("cutoff"), "source-manifest cutoff")
        if manifest_cutoff != cutoff:
            raise self._parent_error("source-manifest cutoff does not match historical basis")
        sources = manifest.manifest.get("sources")
        if not isinstance(sources, list):
            raise self._parent_error("has malformed source-manifest lineage")
        for source in sources:
            if not isinstance(source, Mapping):
                raise self._parent_error("has malformed source-manifest lineage")
            first_available_at = self._manifest_datetime(
                source.get("first_available_at"), "source first_available_at",
            )
            published_at = self._manifest_datetime(
                source.get("published_at"), "source published_at",
            )
            self._require_available_at_cutoff(first_available_at, cutoff, "source")
            self._require_available_at_cutoff(published_at, cutoff, "source")
        try:
            frozen_manifest_hash = freeze_manifest(manifest.manifest, manifest_cutoff).manifest_hash
        except ValidationError as exc:
            raise self._parent_error("has malformed frozen source-manifest content") from exc
        if manifest.manifest_hash != frozen_manifest_hash:
            raise self._parent_error("source-manifest frozen hash does not match content")
        if manifest.manifest_hash != basis.source_manifest_hash:
            raise self._parent_error("source-manifest hash does not match historical basis")
        self._require_available_at_cutoff(manifest.created_at, cutoff, "source manifest")
        if manifest.content_hash != canonical_hash(manifest.manifest):
            raise self._parent_error("has a source-manifest content hash mismatch")
        return manifest

    def _locators_from_manifest(self, manifest_id: UUID) -> tuple[str, ...]:
        manifest = self._manifest_for_id(manifest_id)
        if manifest is None or not isinstance(manifest.manifest, Mapping):
            return ()
        return self._manifest_locators(manifest.manifest)

    @staticmethod
    def _manifest_source_ids(manifest: UnderwritingSourceManifestVersion) -> frozenset[str]:
        sources = manifest.manifest.get("sources") if isinstance(manifest.manifest, Mapping) else None
        if not isinstance(sources, list):
            raise ValidationError("research revision parent has malformed source-manifest lineage")
        source_ids = {
            source_id.strip()
            for source in sources
            if isinstance(source, Mapping)
            and isinstance(source_id := source.get("source_id"), str)
            and source_id.strip()
        }
        if len(source_ids) != len(sources):
            raise ValidationError("research revision parent has malformed source-manifest lineage")
        return frozenset(source_ids)

    @staticmethod
    def _answerability_hash(row: UnderwritingAnswerabilityEvaluation) -> str:
        return answerability_content_hash(
            object_id=row.object_id, basis_id=row.basis_id, version=row.version,
            state=row.state, blockers=row.blockers,
            research_debt_keys=row.research_debt_keys,
            resolvable_within_mandate=row.resolvable_within_mandate,
            allowed_action=row.allowed_action,
            resolution_requirements=row.resolution_requirements,
        )

    def _answerability_parent_hash(
        self,
        row: UnderwritingAnswerabilityEvaluation,
        *,
        seal_created_at: bool,
    ) -> str:
        """Return the generic parent descriptor hash for answerability.

        CATL's separately governed semantic snapshot intentionally retains its
        original semantic hash.  New generic revisions add the normalized
        creation time, which prevents a cutoff-valid timestamp rewrite from
        looking like the same frozen parent.
        """
        semantic_hash = self._answerability_hash(row)
        if not seal_created_at:
            return semantic_hash
        created_at = self._stored_datetime(row.created_at)
        if created_at is None:
            raise self._parent_error("has malformed answerability created_at")
        return canonical_hash({
            "answerability_content_hash": semantic_hash,
            "created_at": self._iso(created_at),
        })

    def _answerability_descriptor_hash(
        self,
        row: UnderwritingAnswerabilityEvaluation,
        *,
        seal_kind: str,
    ) -> str:
        if seal_kind == _ANSWERABILITY_SEAL_LEGACY:
            return self._answerability_hash(row)
        if seal_kind == _ANSWERABILITY_SEAL_GENERIC_TIMESTAMP:
            return self._answerability_parent_hash(row, seal_created_at=True)
        if seal_kind == _ANSWERABILITY_SEAL_CATL_TIMESTAMP:
            created_at = self._stored_datetime(row.created_at)
            if created_at is None:
                raise self._parent_error("has malformed answerability created_at")
            return catl_answerability_parent_content_hash(
                object_id=row.object_id,
                basis_id=row.basis_id,
                version=row.version,
                state=row.state,
                blockers=row.blockers,
                research_debt_keys=row.research_debt_keys,
                resolvable_within_mandate=row.resolvable_within_mandate,
                allowed_action=row.allowed_action,
                resolution_requirements=row.resolution_requirements,
                created_at=created_at,
            )
        raise AssertionError(f"unrecognised answerability parent seal kind: {seal_kind}")

    def _ledger_descriptor_hash(
        self,
        row: UnderwritingLedgerEntry,
        *,
        seal_kind: str,
    ) -> str:
        """Return a ledger parent descriptor hash under its publication seal.

        Ledger content already binds the event's effective and availability
        times, but not its database creation time.  Generic v4 publications
        bind the normalized ``created_at`` as well so a later write cannot be
        made to look historical merely by giving it an earlier availability
        timestamp.  CATL's separately governed parent seal remains on the
        legacy descriptor contract.
        """
        if seal_kind == _LEDGER_SEAL_LEGACY:
            return row.content_hash
        if seal_kind == _LEDGER_SEAL_GENERIC_TIMESTAMP:
            created_at = self._stored_datetime(row.created_at)
            if created_at is None:
                raise self._parent_error("has malformed ledger created_at")
            return canonical_hash({
                "ledger_content_hash": row.content_hash,
                "created_at": self._iso(created_at),
            })
        raise AssertionError(f"unrecognised ledger parent seal kind: {seal_kind}")

    @staticmethod
    def _controlled_string_sequence(value: object, field: str) -> tuple[str, ...]:
        """Return persisted controlled text only when its JSON shape is exact."""
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item.strip() == item and item for item in value)
        ):
            raise ValidationError(f"research revision parent has malformed answerability {field}")
        return tuple(value)

    def _validated_answerability(
        self,
        row: UnderwritingAnswerabilityEvaluation,
        revision: UnderwritingResearchVersion | _ResearchRevisionScope,
    ) -> FrozenAnswerability:
        """Validate the complete controlled answerability state before replay.

        ``uw_answerability_evaluations`` predates a dedicated content-hash
        column.  Its canonical hash is therefore recomputed here and later
        bound to the generic revision's parent descriptor/content hash.  A
        malformed row is rejected before it can contribute that descriptor.
        """
        if row.object_id != revision.object_id or row.basis_id != revision.basis_id:
            raise self._parent_error("has an invalid answerability scope")
        if not isinstance(row.version, int) or isinstance(row.version, bool) or row.version < 1:
            raise self._parent_error("has an invalid answerability version")
        if not isinstance(row.resolvable_within_mandate, bool):
            raise self._parent_error("has malformed answerability mandate control")
        try:
            state = AnswerabilityState(row.state)
            action = EligibleAction(row.allowed_action)
        except (TypeError, ValueError) as exc:
            raise self._parent_error("has malformed answerability controls") from exc
        blockers = self._controlled_string_sequence(row.blockers, "blockers")
        debt_keys = self._controlled_string_sequence(row.research_debt_keys, "research_debt_keys")
        requirements = self._controlled_string_sequence(
            row.resolution_requirements, "resolution_requirements",
        )
        try:
            blocker_codes = tuple(BlockerCode(value) for value in blockers)
        except ValueError as exc:
            raise self._parent_error("has malformed answerability blockers") from exc
        evaluated = evaluate_answerability(
            AnswerabilityInput(
                hard_blockers=blocker_codes,
                research_debt_keys=debt_keys,
                resolvable_within_mandate=row.resolvable_within_mandate,
            )
        )
        if evaluated.state is not state or evaluated.blockers != blocker_codes:
            raise self._parent_error("has inconsistent answerability controls")
        if enforce_action_boundary(evaluated, action) is not action:
            raise self._parent_error("has an action outside its answerability boundary")
        if state is AnswerabilityState.NOT_ANSWERABLE and not requirements:
            raise self._parent_error("has missing answerability resolution requirements")
        created_at = self._stored_datetime(row.created_at)
        if created_at is None:
            raise self._parent_error("has malformed answerability created_at")
        self._require_available_at_cutoff(created_at, self._basis_cutoff(revision.basis_id), "answerability")
        return FrozenAnswerability(
            reference=str(row.id),
            content_hash=self._answerability_hash(row),
            state=state.value,
            blockers=blockers,
            research_debt_keys=debt_keys,
            resolvable_within_mandate=row.resolvable_within_mandate,
            resolution_requirements=requirements,
        )

    @staticmethod
    def _unknown_gap_error(reason: str) -> ValidationError:
        return ValidationError(f"research revision unknown evidence gap {reason}")

    @staticmethod
    def _unknown_gap_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ResearchRevisionDiffService._unknown_gap_error(f"has malformed {field}")
        return value

    @staticmethod
    def _unknown_gap_datetime(value: object, field: str) -> datetime:
        if not isinstance(value, str):
            raise ResearchRevisionDiffService._unknown_gap_error(f"has malformed {field}")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ResearchRevisionDiffService._unknown_gap_error(
                f"has malformed {field}"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ResearchRevisionDiffService._unknown_gap_error(f"has malformed {field}")
        return parsed.astimezone(UTC)

    def _validated_unknown_evidence_gap(
        self,
        row: UnderwritingLedgerEntry,
        revision: UnderwritingResearchVersion,
    ) -> FrozenUnknownEvidenceGap:
        """Parse one selected ``unknown_evidence_gap`` ledger parent exactly.

        Ledger-level hash validation happens while replaying the revision
        summary.  This parser supplies the narrower semantic contract required
        to display an explicit Unknown: exact column/payload timestamps,
        ordinary source fields, a canonical evidence-gap family, and no
        unrecognised payload fields.  It intentionally does not search any
        other ledger row when the parent set omits a gap.
        """
        cutoff = self._basis_cutoff(revision.basis_id)
        if row.object_id != revision.object_id or row.basis_id != revision.basis_id:
            raise self._unknown_gap_error("has an invalid historical scope")
        if row.ledger_kind != "reality" or row.entry_type != "unknown_evidence_gap":
            raise self._unknown_gap_error("has an invalid ledger type")
        source_boundary = self._unknown_gap_text(row.source_boundary, "source boundary")
        if source_boundary != "frozen_source_manifest":
            raise self._unknown_gap_error("has an invalid source boundary")
        if not isinstance(row.payload, Mapping):
            raise self._unknown_gap_error("has malformed payload")
        required_payload_keys = frozenset({
            "metric_key", "unit", "source_id", "source_locator",
            "observed_start", "observed_end", "effective_at", "available_at",
            "source_role", "observation_status", "dimensions",
        })
        if set(row.payload) != required_payload_keys:
            raise self._unknown_gap_error("has malformed payload")
        metric_key = self._unknown_gap_text(row.payload["metric_key"], "metric_key")
        if row.family_key != f"evidence_gap:{metric_key}":
            raise self._unknown_gap_error("has a family that does not match metric_key")
        unit = self._unknown_gap_text(row.payload["unit"], "unit")
        source_id = self._unknown_gap_text(row.payload["source_id"], "source_id")
        source_locator = self._unknown_gap_text(row.payload["source_locator"], "source_locator")
        source_role = self._unknown_gap_text(row.payload["source_role"], "source_role")
        observation_status = self._unknown_gap_text(
            row.payload["observation_status"], "observation_status",
        )
        if observation_status != "unknown":
            raise self._unknown_gap_error("has a non-unknown observation_status")
        dimensions_value = row.payload["dimensions"]
        if (
            not isinstance(dimensions_value, Mapping)
            or not dimensions_value
            or not all(
                isinstance(key, str) and key and key.strip() == key
                and isinstance(value, str) and value and value.strip() == value
                for key, value in dimensions_value.items()
            )
        ):
            raise self._unknown_gap_error("has malformed dimensions")
        dimensions = tuple(sorted(dimensions_value.items()))
        observed_start = self._unknown_gap_datetime(row.payload["observed_start"], "observed_start")
        observed_end = self._unknown_gap_datetime(row.payload["observed_end"], "observed_end")
        effective_at = self._unknown_gap_datetime(row.payload["effective_at"], "effective_at")
        available_at = self._unknown_gap_datetime(row.payload["available_at"], "available_at")
        column_effective_at = self._stored_datetime(row.effective_at)
        column_available_at = self._stored_datetime(row.available_at)
        created_at = self._stored_datetime(row.created_at)
        if (
            column_effective_at is None
            or column_available_at is None
            or created_at is None
            or effective_at != column_effective_at
            or available_at != column_available_at
        ):
            raise self._unknown_gap_error("payload timestamps do not match ledger columns")
        if (
            observed_start > observed_end
            or effective_at < observed_end
            or available_at < effective_at
            or any(value > cutoff for value in (
                observed_start, observed_end, effective_at, available_at, created_at,
            ))
        ):
            raise self._unknown_gap_error("has time outside its historical boundary")
        expected_hash = canonical_hash({
            "ledger_kind": row.ledger_kind,
            "family_key": row.family_key,
            "entry_type": row.entry_type,
            "payload": row.payload,
            "effective_at": column_effective_at,
            "available_at": column_available_at,
            "source_boundary": row.source_boundary,
        })
        if row.content_hash != expected_hash:
            raise self._unknown_gap_error("has a ledger content hash mismatch")
        return FrozenUnknownEvidenceGap(
            reference=str(row.id),
            content_hash=row.content_hash,
            metric_key=metric_key,
            unit=unit,
            source_id=source_id,
            source_locator=source_locator,
            observed_start=observed_start,
            observed_end=observed_end,
            effective_at=effective_at,
            available_at=available_at,
            source_role=source_role,
            observation_status=observation_status,
            dimensions=dimensions,
        )

    def _selected_unknown_gap_manifest(
        self,
        summary: ResearchRevisionSummary,
        revision: UnderwritingResearchVersion,
    ) -> UnderwritingSourceManifestVersion:
        """Return the one source manifest selected by a gap-bearing revision.

        This is intentionally a parent-graph lookup, not a basis-wide search.
        The source locator rule for an Unknown gap is exact equality with the
        selected manifest source's ``locator``: any page or section qualifier
        must therefore be part of the authenticated manifest record itself.
        """
        manifest_refs = tuple(
            ref for ref in summary.parent_refs if ref.artifact_type == "source_manifest"
        )
        if len(manifest_refs) != 1:
            raise self._unknown_gap_error("requires exactly one selected source manifest")
        reference = manifest_refs[0]
        try:
            manifest_id = UUID(reference.reference)
        except (TypeError, ValueError, AttributeError) as exc:
            raise self._unknown_gap_error("source manifest parent is malformed") from exc
        manifest = self._verified_manifest(manifest_id, revision.basis_id)
        resolved = self._resolve_parent(revision, reference.reference)
        if resolved != reference or manifest.content_hash != reference.content_hash:
            raise self._unknown_gap_error("source manifest parent changed after validation")
        return manifest

    def _validate_unknown_gap_manifest_source(
        self,
        gap: FrozenUnknownEvidenceGap,
        manifest: UnderwritingSourceManifestVersion,
    ) -> None:
        """Authenticate an Unknown gap source against its selected manifest.

        ``_manifest_source_ids`` also rejects duplicate/malformed source IDs.
        After that, exact locator equality is deliberate: allowing a prefix,
        substring, or an arbitrary page suffix would let a forged gap claim a
        different source location than the sealed manifest recorded.
        """
        source_ids = self._manifest_source_ids(manifest)
        if gap.source_id not in source_ids:
            raise self._unknown_gap_error("source_id is absent from selected source manifest")
        sources = manifest.manifest.get("sources")
        assert isinstance(sources, list)
        source = next(
            item for item in sources
            if isinstance(item, Mapping) and item.get("source_id") == gap.source_id
        )
        locator = source.get("locator")
        if not isinstance(locator, str) or locator != gap.source_locator:
            raise self._unknown_gap_error("source_locator does not match selected source manifest")

    def _matches_for_uuid(self, reference: UUID) -> list[tuple[str, object]]:
        """Return every recognised immutable row with this exact UUID.

        A UUID collision across persisted tables is corrupt provenance.  The
        caller deliberately rejects it rather than choosing whichever table
        happens to be queried first.
        """
        model_types: tuple[tuple[str, type[object]], ...] = (
            ("source_manifest", UnderwritingSourceManifestVersion),
            ("metric_definition", UnderwritingMetricDefinitionVersion),
            ("metric_observation", UnderwritingMetricObservation),
            ("mechanism", UnderwritingMechanismPackVersion),
            ("industry_state", UnderwritingIndustryStateVersion),
            ("industry_scenario", UnderwritingIndustryScenarioVersion),
            ("company_exposure", UnderwritingCompanyExposureVersion),
            ("earnings_engine", UnderwritingEarningsEngineVersion),
            ("forecast_input", UnderwritingForecastInputVersion),
            ("falsifier", UnderwritingFalsifierVersion),
            ("ledger", UnderwritingLedgerEntry),
            ("answerability", UnderwritingAnswerabilityEvaluation),
            ("candidate_dossier", UnderwritingEvidenceCandidateDossierVersion),
            ("candidate_review", UnderwritingEvidenceCandidateReviewVersion),
        )
        return [
            (artifact_type, row)
            for artifact_type, model in model_types
            if (row := self._session.get(model, reference)) is not None
        ]

    def _verify_artifact_lineage(
        self,
        artifact_type: str,
        row: object,
        revision: UnderwritingResearchVersion | _ResearchRevisionScope,
        *,
        allow_legacy_unavailable_ledger: bool = False,
    ) -> None:
        """Recompute parent payload seals and verify its persisted dependencies.

        Immutable-table guards stop ordinary writes.  This additional read-time
        check is for an attacker or migration bug that bypassed those guards:
        a parent whose source locator, dimensions or content changed without a
        corresponding historical publication must not be presented as genuine.
        """
        cutoff = self._basis_cutoff(revision.basis_id)
        if artifact_type == "source_manifest":
            assert isinstance(row, UnderwritingSourceManifestVersion)
            self._verified_manifest(row.id, revision.basis_id)
            return
        if artifact_type == "metric_definition":
            assert isinstance(row, UnderwritingMetricDefinitionVersion)
            self._require_available_at_cutoff(row.created_at, cutoff, "metric definition")
            if row.content_hash != canonical_hash(row.definition):
                raise self._parent_error("has a metric-definition content hash mismatch")
            self._verified_manifest(row.source_manifest_id, revision.basis_id)
            return
        if artifact_type == "metric_observation":
            assert isinstance(row, UnderwritingMetricObservation)
            self._require_available_at_cutoff(row.available_at, cutoff, "metric observation")
            self._require_available_at_cutoff(row.created_at, cutoff, "metric observation")
            if not isinstance(row.dimensions, Mapping) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in row.dimensions.items()
            ):
                raise self._parent_error("has malformed metric-observation dimensions")
            try:
                expected_hash = DomainMetricObservation(
                    row.metric_key, row.definition_version, self._canonical_decimal(row.value), row.unit,
                    self._stored_datetime(row.observed_start), self._stored_datetime(row.observed_end),
                    self._stored_datetime(row.effective_at), self._stored_datetime(row.available_at),
                    row.source_id, row.source_locator, tuple(row.dimensions.items()),
                ).content_hash
            except ValidationError as exc:
                raise self._parent_error("has malformed metric-observation content") from exc
            if row.dimension_hash != canonical_hash(row.dimensions) or row.content_hash != expected_hash:
                raise self._parent_error("has a metric-observation content hash mismatch")
            definition = self._session.get(UnderwritingMetricDefinitionVersion, row.definition_id)
            if (
                definition is None
                or definition.basis_id != revision.basis_id
                or definition.metric_key != row.metric_key
                or definition.version != row.definition_version
                or definition.source_manifest_id != row.source_manifest_id
                or definition.content_hash != canonical_hash(definition.definition)
            ):
                raise self._parent_error("has an invalid metric-definition lineage")
            manifest = self._verified_manifest(row.source_manifest_id, revision.basis_id)
            if row.source_id not in self._manifest_source_ids(manifest):
                raise self._parent_error("has an unknown metric-observation source")
            return
        if artifact_type == "mechanism":
            assert isinstance(row, UnderwritingMechanismPackVersion)
            self._require_available_at_cutoff(row.created_at, cutoff, "mechanism")
            if row.content_hash != canonical_hash(row.payload):
                raise self._parent_error("has a mechanism content hash mismatch")
            self._verified_manifest(row.source_manifest_id, revision.basis_id)
            return
        if artifact_type == "ledger":
            assert isinstance(row, UnderwritingLedgerEntry)
            if not allow_legacy_unavailable_ledger:
                self._require_available_at_cutoff(row.available_at, cutoff, "ledger")
            self._require_available_at_cutoff(row.created_at, cutoff, "ledger")
            expected_hash = canonical_hash({
                "ledger_kind": row.ledger_kind, "family_key": row.family_key,
                "entry_type": row.entry_type, "payload": row.payload,
                "effective_at": self._stored_datetime(row.effective_at),
                "available_at": self._stored_datetime(row.available_at),
                "source_boundary": row.source_boundary,
            })
            if row.content_hash != expected_hash:
                raise self._parent_error("has a ledger content hash mismatch")
            return
        if artifact_type == "answerability":
            assert isinstance(row, UnderwritingAnswerabilityEvaluation)
            # CATL also retains its special semantic parent-set seal; using the
            # same validation here makes the generic and governed readers
            # agree about malformed answerability controls before that special
            # seal binds their exact canonical hash.
            self._validated_answerability(row, revision)
            return
        if artifact_type == "candidate_dossier":
            assert isinstance(row, UnderwritingEvidenceCandidateDossierVersion)
            # This is a selected-parent replay.  A successor's predecessor
            # chain is not a parent of the selected revision and must not
            # become a current/history lookup that can rewrite this read.
            validate_candidate_dossier_governance(
                self._session,
                row,
                enforce_current=False,
                enforce_lineage=False,
            )
            self._require_available_at_cutoff(row.created_at, cutoff, "candidate dossier")
            return
        if artifact_type == "candidate_review":
            assert isinstance(row, UnderwritingEvidenceCandidateReviewVersion)
            validate_candidate_review_governance(self._session, row, enforce_current=False)
            self._require_available_at_cutoff(row.reviewed_at, cutoff, "candidate review")
            self._require_available_at_cutoff(row.created_at, cutoff, "candidate review")
            return
        # These rows do contain a ``content_hash`` column (except
        # answerability), but the existing persistence contract accepts an
        # externally supplied hash without recording the canonical hash recipe
        # or a signed dependency payload.  Treating it as verified would turn
        # a raw database mutation into a plausible historical artefact.
        if artifact_type in {
            "industry_state", "industry_scenario", "company_exposure",
            "earnings_engine", "forecast_input", "falsifier", "answerability",
        }:
            raise self._parent_error(f"artifact type {artifact_type} is unsealable")
        raise AssertionError(f"unrecognised artifact type: {artifact_type}")

    def _parent_object_id(self, artifact_type: str, row: object) -> UUID | None:
        if artifact_type in {"mechanism", "industry_state", "ledger", "answerability", "candidate_dossier"}:
            return getattr(row, "object_id")
        if artifact_type == "candidate_review":
            assert isinstance(row, UnderwritingEvidenceCandidateReviewVersion)
            dossier = self._session.get(UnderwritingEvidenceCandidateDossierVersion, row.dossier_id)
            return dossier.object_id if dossier is not None else None
        if artifact_type in {"company_exposure", "earnings_engine", "forecast_input"}:
            return getattr(row, "company_id")
        if artifact_type == "industry_scenario":
            assert isinstance(row, UnderwritingIndustryScenarioVersion)
            state = self._session.get(UnderwritingIndustryStateVersion, row.industry_state_id)
            return state.object_id if state is not None and state.basis_id == row.basis_id else None
        if artifact_type == "falsifier":
            assert isinstance(row, UnderwritingFalsifierVersion)
            mechanism = self._session.get(UnderwritingMechanismPackVersion, row.mechanism_id)
            return mechanism.object_id if mechanism is not None and mechanism.basis_id == row.basis_id else None
        return None

    def _descriptor(
        self,
        artifact_type: str,
        row: object,
        reference: str,
        *,
        answerability_seal_kind: str = _ANSWERABILITY_SEAL_LEGACY,
        ledger_seal_kind: str = _LEDGER_SEAL_LEGACY,
    ) -> RevisionArtifactRef:
        if artifact_type == "source_manifest":
            assert isinstance(row, UnderwritingSourceManifestVersion)
            return RevisionArtifactRef(
                reference, artifact_type, f"{row.manifest_key}|{row.version}", row.content_hash,
                self._manifest_locators(row.manifest) if isinstance(row.manifest, Mapping) else (),
                None, None, None, None, "frozen",
            )
        if artifact_type == "metric_definition":
            assert isinstance(row, UnderwritingMetricDefinitionVersion)
            return RevisionArtifactRef(
                reference, artifact_type, f"{row.metric_key}|{row.version}", row.content_hash,
                self._locators_from_manifest(row.source_manifest_id), row.unit,
                None, None, None, row.source_role,
            )
        if artifact_type == "metric_observation":
            assert isinstance(row, UnderwritingMetricObservation)
            definition = self._session.get(UnderwritingMetricDefinitionVersion, row.definition_id)
            status = definition.source_role if definition is not None else None
            return RevisionArtifactRef(
                reference,
                artifact_type,
                "|".join((row.metric_key, str(row.definition_version), self._iso(row.observed_end), row.dimension_hash, row.source_id)),
                row.content_hash,
                (row.source_locator,),
                row.unit,
                self._stored_datetime(row.observed_start),
                self._stored_datetime(row.observed_end),
                self._stored_datetime(row.available_at),
                status,
            )
        if artifact_type == "mechanism":
            assert isinstance(row, UnderwritingMechanismPackVersion)
            return RevisionArtifactRef(
                reference, artifact_type, f"{row.mechanism_key}|{row.version}", row.content_hash,
                self._locators_from_manifest(row.source_manifest_id), None, None, None, None, row.status,
            )
        if artifact_type == "industry_state":
            assert isinstance(row, UnderwritingIndustryStateVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.object_id}|{row.version}", row.content_hash, (), None, None, None, None, "compiled")
        if artifact_type == "industry_scenario":
            assert isinstance(row, UnderwritingIndustryScenarioVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.industry_state_id}|{row.scenario_key}|{row.version}", row.content_hash, (), None, None, None, None, "scenario")
        if artifact_type == "company_exposure":
            assert isinstance(row, UnderwritingCompanyExposureVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.company_id}|{row.exposure_key}|{row.version}", row.content_hash, (), None, None, None, None, "exposure")
        if artifact_type == "earnings_engine":
            assert isinstance(row, UnderwritingEarningsEngineVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.company_id}|{row.version}", row.content_hash, (), None, None, None, None, "compiled")
        if artifact_type == "forecast_input":
            assert isinstance(row, UnderwritingForecastInputVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.company_id}|{row.input_key}|{row.version}", row.content_hash, (), None, None, None, None, "forecast_input")
        if artifact_type == "falsifier":
            assert isinstance(row, UnderwritingFalsifierVersion)
            return RevisionArtifactRef(reference, artifact_type, f"{row.mechanism_id}|{row.falsifier_key}|{row.version}", row.content_hash, (), None, None, None, None, "falsifier")
        if artifact_type == "ledger":
            assert isinstance(row, UnderwritingLedgerEntry)
            locator = row.payload.get("source_locator") if isinstance(row.payload, Mapping) else None
            return RevisionArtifactRef(
                # A ledger version is a replacement of the same research
                # family, not a new economic concept.  Keep the version in
                # the immutable reference/hash while using the family as the
                # semantic identity, so a historical diff can express a
                # revision as ``replaced`` rather than a misleading
                # remove/add pair.
                reference, artifact_type, f"{row.ledger_kind}|{row.family_key}",
                self._ledger_descriptor_hash(row, seal_kind=ledger_seal_kind),
                (locator,) if isinstance(locator, str) and locator else (row.source_boundary,),
                row.payload.get("unit") if isinstance(row.payload, Mapping) and isinstance(row.payload.get("unit"), str) else None,
                self._stored_datetime(row.effective_at), self._stored_datetime(row.effective_at),
                self._stored_datetime(row.available_at), row.entry_type,
            )
        if artifact_type == "answerability":
            assert isinstance(row, UnderwritingAnswerabilityEvaluation)
            answerability = self._validated_answerability(row, _ResearchRevisionScope(
                row.object_id, row.basis_id, "answerability_descriptor",
            ))
            return RevisionArtifactRef(
                reference, artifact_type, "answerability", self._answerability_descriptor_hash(
                    row, seal_kind=answerability_seal_kind,
                ),
                (), None, None, None, None, answerability.state,
            )
        if artifact_type == "candidate_dossier":
            assert isinstance(row, UnderwritingEvidenceCandidateDossierVersion)
            return RevisionArtifactRef(
                reference,
                artifact_type,
                f"{row.dossier_key}|{row.version}",
                canonical_hash({
                    "candidate_dossier_content_hash": row.content_hash,
                    "created_at": self._iso(self._stored_datetime(row.created_at)),
                }),
                self._locators_from_manifest(row.source_manifest_id),
                None,
                None,
                None,
                self._stored_datetime(row.created_at),
                row.status,
            )
        if artifact_type == "candidate_review":
            assert isinstance(row, UnderwritingEvidenceCandidateReviewVersion)
            return RevisionArtifactRef(
                reference,
                artifact_type,
                f"{row.dossier_id}|{row.reviewer_role}|{row.reviewer_identity}",
                canonical_hash({
                    "candidate_review_content_hash": row.content_hash,
                    "reviewed_at": self._iso(self._stored_datetime(row.reviewed_at)),
                    "created_at": self._iso(self._stored_datetime(row.created_at)),
                }),
                (),
                None,
                None,
                None,
                self._stored_datetime(row.reviewed_at),
                row.decision,
            )
        raise AssertionError(f"unrecognised artifact type: {artifact_type}")

    def _resolve_parent(
        self,
        revision: UnderwritingResearchVersion | _ResearchRevisionScope,
        reference: object,
        *,
        allow_legacy_unavailable_ledger: bool = False,
        answerability_seal_kind: str = _ANSWERABILITY_SEAL_LEGACY,
        ledger_seal_kind: str = _LEDGER_SEAL_LEGACY,
    ) -> RevisionArtifactRef:
        if not isinstance(reference, str):
            raise self._parent_error("must be a string")
        if _SNAPSHOT_TOKEN.fullmatch(reference):
            return RevisionArtifactRef(
                reference, "semantic_snapshot", reference, reference.split(":", 1)[1], (), None, None, None, None, "semantic_snapshot",
            )
        try:
            identifier = UUID(reference)
        except (TypeError, ValueError, AttributeError) as exc:
            raise self._parent_error("is malformed") from exc
        if str(identifier) != reference:
            raise self._parent_error("is malformed")
        matches = self._matches_for_uuid(identifier)
        if len(matches) != 1:
            raise self._parent_error("is unresolved or ambiguous")
        artifact_type, row = matches[0]
        basis_id = getattr(row, "basis_id", None)
        if artifact_type == "candidate_review":
            assert isinstance(row, UnderwritingEvidenceCandidateReviewVersion)
            dossier = self._session.get(UnderwritingEvidenceCandidateDossierVersion, row.dossier_id)
            basis_id = dossier.basis_id if dossier is not None else None
        if basis_id != revision.basis_id:
            raise self._parent_error("belongs to another historical basis")
        object_id = self._parent_object_id(artifact_type, row)
        if artifact_type in {
            "mechanism", "industry_state", "industry_scenario", "company_exposure",
            "earnings_engine", "forecast_input", "falsifier", "ledger", "answerability",
            "candidate_dossier", "candidate_review",
        } and object_id != revision.object_id:
            raise self._parent_error("belongs to another research object")
        self._verify_artifact_lineage(
            artifact_type,
            row,
            revision,
            allow_legacy_unavailable_ledger=allow_legacy_unavailable_ledger,
        )
        return self._descriptor(
            artifact_type,
            row,
            reference,
            answerability_seal_kind=answerability_seal_kind,
            ledger_seal_kind=ledger_seal_kind,
        )

    @staticmethod
    def _sort_refs(values: Iterable[RevisionArtifactRef]) -> tuple[RevisionArtifactRef, ...]:
        return tuple(sorted(values, key=lambda item: (item.artifact_type, item.identity, item.reference)))

    @staticmethod
    def _canonical_ref_descriptors(refs: Iterable[RevisionArtifactRef]) -> tuple[dict[str, str], ...]:
        return canonical_parent_refs(
            {
                "reference": ref.reference,
                "artifact_type": ref.artifact_type,
                "identity": ref.identity,
                "content_hash": ref.content_hash,
            }
            for ref in refs
        )

    def _validate_candidate_parent_set(
        self,
        revision: UnderwritingResearchVersion | _ResearchRevisionScope,
        refs: tuple[RevisionArtifactRef, ...],
    ) -> None:
        """Keep candidates in their one sealed, non-promotable revision family."""
        candidate_types = {"candidate_dossier", "candidate_review"}
        present_types = {ref.artifact_type for ref in refs}
        if revision.version_kind != INDUSTRY_EVIDENCE_CANDIDATE_KIND:
            if present_types & candidate_types:
                raise ValidationError("candidate evidence cannot be promoted into a formal research revision")
            return

        expected_counts = {
            "candidate_dossier": 1,
            "candidate_review": 2,
            "source_manifest": 1,
            "answerability": 1,
        }
        actual_counts = {
            artifact_type: sum(ref.artifact_type == artifact_type for ref in refs)
            for artifact_type in set(expected_counts) | present_types
        }
        if actual_counts != expected_counts:
            raise ValidationError("candidate revision must seal one dossier, two reviews, manifest, and answerability")
        dossier_ref = next(ref for ref in refs if ref.artifact_type == "candidate_dossier")
        manifest_ref = next(ref for ref in refs if ref.artifact_type == "source_manifest")
        answerability_ref = next(ref for ref in refs if ref.artifact_type == "answerability")
        dossier = self._session.get(UnderwritingEvidenceCandidateDossierVersion, UUID(dossier_ref.reference))
        manifest = self._session.get(UnderwritingSourceManifestVersion, UUID(manifest_ref.reference))
        answerability = self._session.get(UnderwritingAnswerabilityEvaluation, UUID(answerability_ref.reference))
        if dossier is None or manifest is None or answerability is None:
            raise ValidationError("candidate revision has an unresolved sealed parent")
        if dossier.source_manifest_id != manifest.id:
            raise ValidationError("candidate revision manifest does not match dossier")
        if (
            answerability.state != AnswerabilityState.NOT_ANSWERABLE.value
            or not answerability.research_debt_keys
            or not answerability.resolution_requirements
            or answerability.allowed_action not in {
                EligibleAction.WAIT_FOR_VALIDATION.value,
                EligibleAction.DO_NOT_ENTER.value,
            }
        ):
            raise ValidationError("candidate revision requires not_answerable research debt and requirements")
        reviews = tuple(
            self._session.get(UnderwritingEvidenceCandidateReviewVersion, UUID(ref.reference))
            for ref in refs if ref.artifact_type == "candidate_review"
        )
        if (
            any(review is None for review in reviews)
            or {review.reviewer_role for review in reviews if review is not None} != {"provenance", "methodology"}
            or any(review.decision != "approve" for review in reviews if review is not None)
            or any(review.dossier_id != dossier.id or review.dossier_content_hash != dossier.content_hash for review in reviews if review is not None)
            or len({review.reviewer_identity for review in reviews if review is not None}) != 2
        ):
            raise ValidationError("candidate revision requires exact distinct approved reviews")

    def _revision(self, revision_id: UUID) -> UnderwritingResearchVersion:
        row = self._session.get(UnderwritingResearchVersion, revision_id)
        if row is None:
            raise ValidationError("research revision not found")
        return row

    def _catl_parent_set_seal(
        self,
        refs: tuple[RevisionArtifactRef, ...],
    ) -> tuple[UnderwritingLedgerEntry, str, tuple[dict[str, str], ...], bool]:
        """Read the selected CATL parent-set seal, never a current fixture."""
        seal_rows: list[UnderwritingLedgerEntry] = []
        for ref in refs:
            if ref.artifact_type != "ledger":
                continue
            try:
                row_id = UUID(ref.reference)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError("CATL semantic snapshot parent set has a malformed seal") from exc
            row = self._session.get(UnderwritingLedgerEntry, row_id)
            if (
                row is not None
                and row.family_key == CATL_PARENT_SET_FAMILY
                and row.entry_type == CATL_PARENT_SET_ENTRY_TYPE
            ):
                seal_rows.append(row)
        if len(seal_rows) != 1:
            raise ValidationError("CATL semantic snapshot parent set is missing its seal")
        seal = seal_rows[0]
        try:
            token, sealed_refs, answerability_timestamp_sealed = parse_catl_parent_set_seal(seal.payload)
        except ValidationError as exc:
            raise ValidationError("CATL semantic snapshot parent set seal is malformed") from exc
        return seal, token, sealed_refs, answerability_timestamp_sealed

    def _validate_catl_semantic_snapshot(
        self, revision: UnderwritingResearchVersion, refs: tuple[RevisionArtifactRef, ...]
    ) -> bool:
        """Validate only IDs and seals stored on this selected revision.

        Do not load a fixture, resolve a natural key, or consult a current head:
        any of those would turn a later successor into a mutation of history.
        """
        snapshot_refs = tuple(ref for ref in refs if ref.artifact_type == "semantic_snapshot")
        if len(snapshot_refs) != 1:
            raise ValidationError("CATL semantic snapshot parent set is missing its token")
        manifest_refs = tuple(ref for ref in refs if ref.artifact_type == "source_manifest")
        if len(manifest_refs) != 1:
            raise ValidationError("CATL semantic snapshot parent set is missing its frozen source manifest")
        try:
            manifest_id = UUID(manifest_refs[0].reference)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("CATL semantic snapshot parent set has a malformed source manifest") from exc
        # The selected parent (not a fixture lookup or current manifest head)
        # must itself bind the exact basis cutoff and manifest identity.
        self._verified_manifest(manifest_id, revision.basis_id)
        seal, token, sealed_refs, answerability_timestamp_sealed = self._catl_parent_set_seal(refs)
        actual_refs = canonical_parent_refs(
            {
                "reference": ref.reference,
                "artifact_type": ref.artifact_type,
                "identity": ref.identity,
                "content_hash": ref.content_hash,
            }
            for ref in refs
            if ref.artifact_type not in {"semantic_snapshot"}
            and not (ref.artifact_type == "ledger" and ref.reference == str(seal.id))
        )
        if token != snapshot_refs[0].reference or sealed_refs != actual_refs:
            raise ValidationError("CATL semantic snapshot parent set does not match its stored seal")
        expected_hash = catl_revision_content_hash(
            semantic_snapshot_token=token,
            parent_set_semantic_hash=parent_set_semantic_hash(sealed_refs),
        )
        if revision.content_hash != expected_hash:
            raise ValidationError("CATL semantic snapshot content hash does not match its stored seal")
        return answerability_timestamp_sealed

    def _legacy_frozen_snapshot_hash(
        self, revision: UnderwritingResearchVersion, refs: tuple[RevisionArtifactRef, ...],
    ) -> str:
        """Reconstruct the pre-parent-set kernel snapshot from frozen parents.

        Legacy generic revisions used the effective-ledger snapshot in their
        hash.  Reading the currently effective ledger would make later facts
        rewrite history, so compatibility accepts only the snapshot that can
        be reconstructed from ledger IDs explicitly frozen on this revision.
        If a historical row did not freeze the ledger parents needed for its
        old snapshot, its original hash cannot be proven and the caller must
        fail closed.
        """
        basis = self._session.get(UnderwritingHistoricalBasis, revision.basis_id)
        if basis is None:
            raise ValidationError("research revision basis is missing")
        selected: dict[tuple[str, str], UnderwritingLedgerEntry] = {}
        cutoff = self._stored_datetime(basis.cutoff)
        for ref in refs:
            if ref.artifact_type != "ledger":
                continue
            try:
                row_id = UUID(ref.reference)
            except (TypeError, ValueError, AttributeError) as exc:
                raise self._parent_error("has malformed frozen ledger identity") from exc
            row = self._session.get(UnderwritingLedgerEntry, row_id)
            if row is None or row.basis_id != revision.basis_id or row.object_id != revision.object_id:
                raise self._parent_error("has invalid frozen ledger lineage")
            # This is the old repository's first selection step, replayed from
            # frozen candidates rather than querying its current ledger.
            if self._stored_datetime(row.available_at) > cutoff:
                continue
            key = (row.ledger_kind, row.family_key)
            existing = selected.get(key)
            if existing is None or (row.version, str(row.id)) > (existing.version, str(existing.id)):
                selected[key] = row
        entries = tuple(sorted(selected.values(), key=lambda row: (row.ledger_kind, row.family_key)))
        return canonical_hash({
            "object_id": revision.object_id,
            "basis_cutoff": self._stored_datetime(basis.cutoff),
            "source_manifest_hash": basis.source_manifest_hash,
            "entries": [
                {"id": entry.id, "content_hash": entry.content_hash}
                for entry in entries
            ],
        })

    def _legacy_revision_content_hash(
        self, revision: UnderwritingResearchVersion, refs: tuple[RevisionArtifactRef, ...],
    ) -> str:
        return canonical_hash({
            "snapshot_hash": self._legacy_frozen_snapshot_hash(revision, refs),
            "version_kind": revision.version_kind,
            "parent_ids": sorted(set(revision.parent_ids)),
        })

    def _validate_revision_content(
        self, revision: UnderwritingResearchVersion, refs: tuple[RevisionArtifactRef, ...]
    ) -> None:
        if not isinstance(revision.parent_ids, list) or not all(isinstance(value, str) for value in revision.parent_ids):
            raise ValidationError("research revision parents are malformed")
        if len(set(revision.parent_ids)) != len(revision.parent_ids):
            raise ValidationError("research revision parent set is duplicated")
        # Generic revisions seal only their persisted parent set.  A current
        # ledger snapshot is intentionally excluded: unrelated evidence may be
        # appended after publication and must never rewrite historical reads.
        # A semantic snapshot denotes a separately governed fixture
        # publication (such as CATL), whose payload has its own persisted seal.
        snapshot_tokens = tuple(value for value in revision.parent_ids if _SNAPSHOT_TOKEN.fullmatch(value))
        if revision.version_kind == CATL_VERSION_KIND:
            if len(snapshot_tokens) != 1:
                raise ValidationError("CATL semantic snapshot parent set is missing its governed token")
            self._validate_catl_semantic_snapshot(revision, refs)
            return
        if snapshot_tokens:
            raise ValidationError("research revision semantic snapshot is not governed")
        basis = self._basis_for_id(revision.basis_id)
        expected, _, _ = frozen_research_version_content_hash(
            revision.object_id, revision.basis_id, revision.version_kind, list(revision.parent_ids),
            cutoff=self._stored_datetime(basis.cutoff),
            price_as_of=(
                self._stored_datetime(basis.price_as_of)
                if basis.price_as_of is not None
                else None
            ),
            source_manifest_hash=basis.source_manifest_hash,
            parent_refs=self._canonical_ref_descriptors(refs),
        )
        if revision.content_hash == expected:
            return
        if revision.content_hash != self._legacy_revision_content_hash(revision, refs):
            raise ValidationError("research revision content hash does not match frozen parent set")

    @staticmethod
    def _validate_parent_ids(parent_ids: object) -> None:
        if not isinstance(parent_ids, list) or not all(isinstance(value, str) for value in parent_ids):
            raise ValidationError("research revision parents are malformed")
        if len(set(parent_ids)) != len(parent_ids):
            raise ValidationError("research revision parent set is duplicated")

    @classmethod
    def _validate_parent_shape(cls, revision: UnderwritingResearchVersion) -> None:
        cls._validate_parent_ids(revision.parent_ids)

    def frozen_parent_descriptors(
        self,
        object_id: UUID,
        basis_id: UUID,
        version_kind: str,
        parent_ids: list[str],
    ) -> tuple[dict[str, str], ...]:
        """Resolve exactly the persisted parents a generic v4 publication seals.

        This is intentionally the same strict resolver used for replay.  It
        neither creates a research-version row nor consults a current ledger,
        fixture, or natural-key head, so a caller can preview the exact seal
        before the immutable publication is appended.
        """
        with self._session.no_autoflush:
            if version_kind == CATL_VERSION_KIND:
                raise ValidationError("CATL version kind is reserved for the governed semantic snapshot publisher")
            if version_kind == INDUSTRY_EVIDENCE_CANDIDATE_KIND:
                raise ValidationError("industry_evidence_candidate is reserved for CandidateEvidenceService")
            if not isinstance(parent_ids, list) or not all(isinstance(value, str) for value in parent_ids):
                raise ValidationError("research revision parents are malformed")
            if self._session.get(UnderwritingResearchObject, object_id) is None:
                raise ValidationError("research object not found")
            self._basis_for_id(basis_id)
            scope = _ResearchRevisionScope(object_id, basis_id, version_kind)
            refs = self._sort_refs(
                self._resolve_parent(
                    scope,
                    reference,
                    answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                    ledger_seal_kind=_LEDGER_SEAL_GENERIC_TIMESTAMP,
                )
                for reference in sorted(set(parent_ids))
            )
            if any(ref.artifact_type == "semantic_snapshot" for ref in refs):
                raise ValidationError("generic research revision cannot seal a semantic snapshot token")
            self._validate_candidate_parent_set(scope, refs)
            return self._canonical_ref_descriptors(refs)

    def frozen_candidate_parent_descriptors(
        self,
        object_id: UUID,
        basis_id: UUID,
        parent_ids: list[str],
    ) -> tuple[dict[str, str], ...]:
        """Resolve the sole governed parent graph for candidate publication.

        This is deliberately separate from the generic descriptor path: a
        generic caller must never manufacture an ``industry_evidence_candidate``
        revision from a historical or superseded dossier.
        """
        with self._session.no_autoflush:
            if not isinstance(parent_ids, list) or not all(isinstance(value, str) for value in parent_ids):
                raise ValidationError("research revision parents are malformed")
            if self._session.get(UnderwritingResearchObject, object_id) is None:
                raise ValidationError("research object not found")
            self._basis_for_id(basis_id)
            scope = _ResearchRevisionScope(object_id, basis_id, INDUSTRY_EVIDENCE_CANDIDATE_KIND)
            refs = self._sort_refs(
                self._resolve_parent(
                    scope,
                    reference,
                    answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                    ledger_seal_kind=_LEDGER_SEAL_GENERIC_TIMESTAMP,
                )
                for reference in sorted(set(parent_ids))
            )
            self._validate_candidate_parent_set(scope, refs)
            return self._canonical_ref_descriptors(refs)

    def revision_summary(self, revision_id: UUID) -> ResearchRevisionSummary:
        """Describe only parents explicitly frozen into ``revision_id``."""
        with self._session.no_autoflush:
            revision = self._revision(revision_id)
            basis = self._basis_for_id(revision.basis_id)
            self._validate_parent_shape(revision)
            snapshot_tokens = tuple(
                value for value in revision.parent_ids if _SNAPSHOT_TOKEN.fullmatch(value)
            )
            if snapshot_tokens:
                legacy_refs = self._sort_refs(
                    self._resolve_parent(revision, reference)
                    for reference in revision.parent_ids
                )
                legacy_snapshot_candidate = False
                if revision.version_kind == CATL_VERSION_KIND:
                    _, _, _, answerability_timestamp_sealed = self._catl_parent_set_seal(legacy_refs)
                    refs = (
                        self._sort_refs(
                            self._resolve_parent(
                                revision,
                                reference,
                                answerability_seal_kind=_ANSWERABILITY_SEAL_CATL_TIMESTAMP,
                            )
                            for reference in revision.parent_ids
                        )
                        if answerability_timestamp_sealed
                        else legacy_refs
                    )
                else:
                    refs = legacy_refs
                    answerability_timestamp_sealed = False
                ledger_timestamp_sealed = False
            else:
                # Pre-v2 legacy rows may list a late ledger candidate that
                # their old snapshot algorithm excluded before hashing.  Read
                # it only long enough to determine whether the stored legacy
                # seal is authentic. A generic v4 seal binds both
                # answerability and ledger creation timestamps; v3 binds only
                # answerability, and earlier rows bind neither.
                fully_timestamp_sealed_provisional_refs = self._sort_refs(
                    self._resolve_parent(
                        revision,
                        reference,
                        allow_legacy_unavailable_ledger=True,
                        answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                        ledger_seal_kind=_LEDGER_SEAL_GENERIC_TIMESTAMP,
                    )
                    for reference in revision.parent_ids
                )
                fully_timestamp_sealed_hash, _, _ = frozen_research_version_content_hash(
                    revision.object_id,
                    revision.basis_id,
                    revision.version_kind,
                    list(revision.parent_ids),
                    cutoff=self._stored_datetime(basis.cutoff),
                    price_as_of=(
                        self._stored_datetime(basis.price_as_of)
                        if basis.price_as_of is not None
                        else None
                    ),
                    source_manifest_hash=basis.source_manifest_hash,
                    parent_refs=self._canonical_ref_descriptors(fully_timestamp_sealed_provisional_refs),
                )
                if revision.content_hash == fully_timestamp_sealed_hash:
                    answerability_timestamp_sealed = True
                    ledger_timestamp_sealed = True
                    legacy_snapshot_candidate = False
                    refs = self._sort_refs(
                        self._resolve_parent(
                            revision,
                            reference,
                            answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                            ledger_seal_kind=_LEDGER_SEAL_GENERIC_TIMESTAMP,
                        )
                        for reference in revision.parent_ids
                    )
                else:
                    answerability_timestamp_sealed_provisional_refs = self._sort_refs(
                        self._resolve_parent(
                            revision,
                            reference,
                            allow_legacy_unavailable_ledger=True,
                            answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                        )
                        for reference in revision.parent_ids
                    )
                    answerability_timestamp_sealed_hash, _, _ = frozen_research_version_content_hash(
                        revision.object_id,
                        revision.basis_id,
                        revision.version_kind,
                        list(revision.parent_ids),
                        cutoff=self._stored_datetime(basis.cutoff),
                        price_as_of=(
                            self._stored_datetime(basis.price_as_of)
                            if basis.price_as_of is not None
                            else None
                        ),
                        source_manifest_hash=basis.source_manifest_hash,
                        parent_refs=self._canonical_ref_descriptors(
                            answerability_timestamp_sealed_provisional_refs,
                        ),
                    )
                    if revision.content_hash == answerability_timestamp_sealed_hash:
                        answerability_timestamp_sealed = True
                        ledger_timestamp_sealed = False
                        legacy_snapshot_candidate = False
                        refs = self._sort_refs(
                            self._resolve_parent(
                                revision,
                                reference,
                                answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
                            )
                            for reference in revision.parent_ids
                        )
                    else:
                        # Generic versions published before timestamp sealing
                        # remain readable as historical summaries. They cannot
                        # later answer a boundary whose parent descriptor
                        # omitted the relevant creation time.
                        legacy_provisional_refs = self._sort_refs(
                            self._resolve_parent(
                                revision,
                                reference,
                                allow_legacy_unavailable_ledger=True,
                            )
                            for reference in revision.parent_ids
                        )
                        legacy_parent_set_hash, _, _ = frozen_research_version_content_hash(
                            revision.object_id,
                            revision.basis_id,
                            revision.version_kind,
                            list(revision.parent_ids),
                            cutoff=self._stored_datetime(basis.cutoff),
                            price_as_of=(
                                self._stored_datetime(basis.price_as_of)
                                if basis.price_as_of is not None
                                else None
                            ),
                            source_manifest_hash=basis.source_manifest_hash,
                            parent_refs=self._canonical_ref_descriptors(legacy_provisional_refs),
                        )
                        answerability_timestamp_sealed = False
                        ledger_timestamp_sealed = False
                        legacy_snapshot_candidate = revision.content_hash != legacy_parent_set_hash
                        refs = (
                            legacy_provisional_refs
                            if legacy_snapshot_candidate
                            else self._sort_refs(
                                self._resolve_parent(revision, reference)
                                for reference in revision.parent_ids
                            )
                        )
            self._validate_revision_content(revision, refs)
            self._validate_candidate_parent_set(revision, refs)
            if legacy_snapshot_candidate:
                cutoff = self._stored_datetime(basis.cutoff)
                refs = tuple(
                    ref for ref in refs
                    if not (
                        ref.artifact_type == "ledger"
                        and ref.available_at is not None
                        and ref.available_at > cutoff
                    )
                )
            return ResearchRevisionSummary(
                revision.id, revision.object_id, revision.basis_id, revision.version_kind,
                revision.sequence, revision.content_hash, self._stored_datetime(basis.cutoff),
                basis.source_manifest_hash, refs, answerability_timestamp_sealed,
                ledger_timestamp_sealed,
            )

    def revision_boundary(self, revision_id: UUID) -> ResearchRevisionBoundary:
        """Read only explicitly typed parents sealed into one checked revision.

        No current answerability lookup is permitted: if this version did not
        freeze an answerability record, the boundary is explicitly ``None``.
        Unknown evidence gaps follow the same rule: only selected ledger
        parents that themselves identify as ``unknown_evidence_gap`` can be
        displayed.  A duplicate parent is ambiguous provenance and therefore
        fails closed.
        """
        with self._session.no_autoflush:
            summary = self.revision_summary(revision_id)
            revision = self._revision(summary.id)
            answerability_refs = tuple(
                ref for ref in summary.parent_refs if ref.artifact_type == "answerability"
            )
            if len(answerability_refs) > 1:
                raise ValidationError("research revision boundary has multiple answerability parents")
            answerability: FrozenAnswerability | None = None
            if answerability_refs and not summary.answerability_timestamp_sealed:
                raise ValidationError(
                    "research revision boundary answerability parent is not timestamp-sealed"
                )
            if answerability_refs:
                reference = answerability_refs[0]
                resolved = self._resolve_parent(
                    revision,
                    reference.reference,
                    answerability_seal_kind=(
                        _ANSWERABILITY_SEAL_CATL_TIMESTAMP
                        if summary.version_kind == CATL_VERSION_KIND
                        else _ANSWERABILITY_SEAL_GENERIC_TIMESTAMP
                    ),
                )
                if resolved != reference:
                    raise ValidationError("research revision boundary answerability parent changed after validation")
                try:
                    row_id = UUID(reference.reference)
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValidationError("research revision boundary answerability parent is malformed") from exc
                row = self._session.get(UnderwritingAnswerabilityEvaluation, row_id)
                if row is None:
                    raise ValidationError("research revision boundary answerability parent is missing")
                answerability = self._validated_answerability(row, revision)
                expected_parent_hash = self._answerability_descriptor_hash(
                    row,
                    seal_kind=(
                        _ANSWERABILITY_SEAL_CATL_TIMESTAMP
                        if summary.version_kind == CATL_VERSION_KIND
                        else _ANSWERABILITY_SEAL_GENERIC_TIMESTAMP
                    ),
                )
                if (
                    answerability.reference != reference.reference
                    or expected_parent_hash != reference.content_hash
                    or answerability.state != reference.status
                ):
                    raise ValidationError("research revision boundary answerability parent is not sealed")

            gaps: list[FrozenUnknownEvidenceGap] = []
            gap_metric_keys: set[str] = set()
            gap_parent_refs = tuple(
                ref for ref in summary.parent_refs
                if ref.artifact_type == "ledger" and ref.status == "unknown_evidence_gap"
            )
            if gap_parent_refs and not summary.ledger_timestamp_sealed:
                raise self._unknown_gap_error("parent is not ledger timestamp-sealed")
            gap_manifest = (
                self._selected_unknown_gap_manifest(summary, revision)
                if gap_parent_refs
                else None
            )
            for reference in summary.parent_refs:
                if (
                    reference.artifact_type != "ledger"
                    or reference.status != "unknown_evidence_gap"
                ):
                    continue
                try:
                    row_id = UUID(reference.reference)
                except (TypeError, ValueError, AttributeError) as exc:
                    raise self._unknown_gap_error("parent reference is malformed") from exc
                row = self._session.get(UnderwritingLedgerEntry, row_id)
                if row is None:
                    raise self._unknown_gap_error("parent is missing")
                resolved = self._resolve_parent(
                    revision,
                    reference.reference,
                    ledger_seal_kind=(
                        _LEDGER_SEAL_GENERIC_TIMESTAMP
                        if summary.ledger_timestamp_sealed
                        else _LEDGER_SEAL_LEGACY
                    ),
                )
                if resolved != reference:
                    raise self._unknown_gap_error("parent changed after validation")
                gap = self._validated_unknown_evidence_gap(row, revision)
                if (
                    gap.reference != reference.reference
                    or reference.identity != f"reality|evidence_gap:{gap.metric_key}"
                ):
                    raise self._unknown_gap_error("parent descriptor is not sealed")
                if gap.metric_key in gap_metric_keys:
                    raise self._unknown_gap_error("has a duplicate unknown evidence gap metric_key")
                assert gap_manifest is not None
                self._validate_unknown_gap_manifest_source(gap, gap_manifest)
                gap_metric_keys.add(gap.metric_key)
                gaps.append(gap)
            return ResearchRevisionBoundary(
                summary,
                answerability,
                tuple(sorted(gaps, key=lambda gap: (gap.metric_key, gap.reference))),
            )

    def candidate_evidence(self, revision_id: UUID) -> CandidateEvidenceRead:
        """Project only the exact, verified parents of one candidate revision.

        This deliberately begins with ``revision_summary``: that resolver
        authenticates the selected revision hash and its complete parent graph.
        The rows below are then addressed only by IDs already sealed in that
        graph—never by a dossier key, a current head, or a fixture lookup.
        """
        with selected_candidate_replay(self._session), self._session.no_autoflush:
            summary = self.revision_summary(revision_id)
            if summary.version_kind != INDUSTRY_EVIDENCE_CANDIDATE_KIND:
                raise ValidationError("research revision is not an industry evidence candidate")
            revision = self._revision(summary.id)
            by_type = {
                artifact_type: tuple(
                    ref for ref in summary.parent_refs if ref.artifact_type == artifact_type
                )
                for artifact_type in ("candidate_dossier", "candidate_review", "answerability")
            }
            if (
                len(by_type["candidate_dossier"]) != 1
                or len(by_type["candidate_review"]) != 2
                or len(by_type["answerability"]) != 1
            ):
                raise ValidationError("candidate revision parent graph is incomplete")

            dossier_ref = by_type["candidate_dossier"][0]
            try:
                dossier_id = UUID(dossier_ref.reference)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError("candidate revision dossier parent is malformed") from exc
            dossier_row = self._session.get(UnderwritingEvidenceCandidateDossierVersion, dossier_id)
            if dossier_row is None:
                raise ValidationError("candidate revision dossier parent is missing")
            # Re-resolve the precise stored row, catching a changed descriptor
            # even if an ORM identity map existed before the read began.
            if self._resolve_parent(revision, dossier_ref.reference) != dossier_ref:
                raise ValidationError("candidate revision dossier parent changed after validation")
            dossier = CandidateEvidenceDossier.from_canonical_payload(dossier_row.payload)
            if (
                dossier.object_id != summary.object_id
                or dossier.basis_id != summary.basis_id
                or dossier.content_hash != dossier_row.content_hash
                or str(dossier_row.id) != dossier_ref.reference
            ):
                raise ValidationError("candidate revision dossier parent is not sealed")

            reviews: list[FrozenCandidateReview] = []
            for review_ref in by_type["candidate_review"]:
                try:
                    review_id = UUID(review_ref.reference)
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValidationError("candidate revision review parent is malformed") from exc
                review_row = self._session.get(UnderwritingEvidenceCandidateReviewVersion, review_id)
                if review_row is None:
                    raise ValidationError("candidate revision review parent is missing")
                if self._resolve_parent(revision, review_ref.reference) != review_ref:
                    raise ValidationError("candidate revision review parent changed after validation")
                review = CandidateEvidenceReview.from_canonical_payload(review_row.payload)
                if (
                    review.dossier_id != dossier_row.id
                    or review.dossier_content_hash != dossier.content_hash
                    or review.content_hash != review_row.content_hash
                    or str(review_row.id) != review_ref.reference
                ):
                    raise ValidationError("candidate revision review parent is not sealed")
                reviews.append(FrozenCandidateReview(
                    reference=review_ref.reference,
                    content_hash=review.content_hash,
                    reviewer_identity=review.reviewer_identity,
                    reviewer_role=review.reviewer_role,
                    decision=review.decision,
                    rationale=review.rationale,
                    reviewed_at=review.reviewed_at,
                ))

            answerability_ref = by_type["answerability"][0]
            try:
                answerability_id = UUID(answerability_ref.reference)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError("candidate revision answerability parent is malformed") from exc
            answerability_row = self._session.get(UnderwritingAnswerabilityEvaluation, answerability_id)
            if answerability_row is None:
                raise ValidationError("candidate revision answerability parent is missing")
            if self._resolve_parent(
                revision,
                answerability_ref.reference,
                answerability_seal_kind=_ANSWERABILITY_SEAL_GENERIC_TIMESTAMP,
            ) != answerability_ref:
                raise ValidationError("candidate revision answerability parent changed after validation")
            answerability = self._validated_answerability(answerability_row, revision)
            if (
                answerability.state != AnswerabilityState.NOT_ANSWERABLE.value
                or answerability.reference != answerability_ref.reference
                or answerability_ref.status != answerability.state
            ):
                raise ValidationError("candidate revision answerability parent is not sealed")

            items = tuple(sorted(
                dossier.items,
                key=lambda item: (
                    item.metric_key,
                    item.source_id,
                    item.source_locator,
                    item.observed_start,
                    item.observed_end,
                    item.content_hash,
                ),
            ))
            return CandidateEvidenceRead(
                revision=summary,
                dossier=FrozenCandidateDossier(
                    reference=dossier_ref.reference,
                    content_hash=dossier.content_hash,
                    dossier_key=dossier.dossier_key,
                    version=dossier.version,
                    status=dossier.status.value,
                    scope_statement=dossier.scope_statement,
                    items=items,
                ),
                reviews=tuple(sorted(
                    reviews,
                    key=lambda review: (review.reviewer_role, review.reviewer_identity, review.reference),
                )),
                answerability=FrozenCandidateAnswerability(
                    reference=answerability_ref.reference,
                    content_hash=answerability_ref.content_hash,
                    state=answerability.state,
                    research_debt_keys=answerability.research_debt_keys,
                    resolution_requirements=answerability.resolution_requirements,
                ),
            )

    def _family_rows(self, object_id: UUID, version_kind: str) -> tuple[UnderwritingResearchVersion, ...]:
        rows = tuple(self._session.scalars(
            select(UnderwritingResearchVersion).where(
                UnderwritingResearchVersion.object_id == object_id,
                UnderwritingResearchVersion.version_kind == version_kind,
            ).order_by(UnderwritingResearchVersion.sequence, UnderwritingResearchVersion.id)
        ))
        if not rows:
            raise ValidationError("research revision not found")
        expected_previous: UUID | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            if row.object_id != object_id or row.version_kind != version_kind:
                raise ValidationError("research revision history is corrupt")
            if row.sequence != expected_sequence or row.supersedes_id != expected_previous:
                raise ValidationError("research revision history is corrupt")
            expected_previous = row.id
        return rows

    @staticmethod
    def _validated_research_object_identity(
        object_id: object,
        kind: object,
        canonical_name: object,
        external_key: object,
    ) -> tuple[UUID, ResearchObjectKind, str, str]:
        if not isinstance(object_id, UUID):
            raise ValidationError("research revision object identity is malformed")
        if (
            not isinstance(canonical_name, str)
            or not canonical_name.strip()
            or not isinstance(external_key, str)
            or not external_key.strip()
        ):
            raise ValidationError("research revision object identity is malformed")
        try:
            object_kind = ResearchObjectKind(kind)
        except (TypeError, ValueError) as exc:
            raise ValidationError("research revision object identity is malformed") from exc
        return object_id, object_kind, canonical_name, external_key

    def _history_object(self, object_id: UUID) -> tuple[ResearchObjectKind, str, str]:
        """Load only the immutable object selected by a revision family.

        This is deliberately not a current-security lookup or an inferred
        identity from any parent artefact.  A malformed historical foreign key
        must fail closed rather than silently falling back to a related object.
        """
        # Select scalar columns rather than materialising an ORM entity.  The
        # latter can be served from the session identity map and therefore
        # expose an unflushed caller mutation as though it were frozen history.
        with self._session.no_autoflush:
            row = self._session.execute(
                select(
                    UnderwritingResearchObject.id,
                    UnderwritingResearchObject.kind,
                    UnderwritingResearchObject.canonical_name,
                    UnderwritingResearchObject.external_key,
                ).where(UnderwritingResearchObject.id == object_id)
            ).mappings().one_or_none()
        if row is None:
            raise ValidationError("research revision object is missing")
        stored_id, object_kind, canonical_name, external_key = self._validated_research_object_identity(
            row["id"], row["kind"], row["canonical_name"], row["external_key"],
        )
        if stored_id != object_id:
            raise ValidationError("research revision object is missing")
        return object_kind, canonical_name, external_key

    def revision_history(self, object_id: UUID, version_kind: str) -> RevisionHistory:
        with self._session.no_autoflush:
            rows = self._family_rows(object_id, version_kind)
            object_kind, canonical_name, external_key = self._history_object(object_id)
            return RevisionHistory(
                object_id,
                object_kind,
                canonical_name,
                external_key,
                version_kind,
                tuple(self.revision_summary(row.id) for row in rows),
            )

    def effective_revision(self, object_id: UUID, version_kind: str) -> ResearchRevisionSummary:
        with self._session.no_autoflush:
            rows = self._family_rows(object_id, version_kind)
            return self.revision_summary(rows[-1].id)

    @staticmethod
    def _archive_sort_key(item: ResearchArchiveItem) -> tuple[str, str, str, str]:
        return (
            item.canonical_name,
            item.external_key,
            str(item.object_id),
            item.version_kind,
        )

    @staticmethod
    def _encode_archive_cursor(after: tuple[str, str, str, str]) -> str:
        payload = json.dumps(
            {"after": list(after), "v": 1},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def _decode_archive_cursor(cls, cursor: str | None) -> tuple[str, str, str, str] | None:
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor:
            raise ValidationError("research archive cursor is malformed")
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("research archive cursor is malformed") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "after"}
            or payload.get("v") != 1
            or not isinstance(payload.get("after"), list)
            or len(payload["after"]) != 4
            or not all(isinstance(value, str) for value in payload["after"])
        ):
            raise ValidationError("research archive cursor is malformed")
        after = tuple(payload["after"])
        assert len(after) == 4
        try:
            object_id = UUID(after[2])
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("research archive cursor is malformed") from exc
        if str(object_id) != after[2] or cls._encode_archive_cursor(after) != cursor:
            raise ValidationError("research archive cursor is malformed")
        return after  # type: ignore[return-value]

    def research_archives(
        self,
        query: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ResearchArchivePage:
        """List persisted research families without resolving a current state.

        Each listing row is explicitly a *lineage health* result.  A corrupt
        family remains discoverable so an analyst can repair it, but every
        assertion derived from a revision is withheld until the complete
        successor chain replays from frozen parents.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("research archive limit must be between 1 and 100")
        after = self._decode_archive_cursor(cursor)
        needle = query.casefold() if isinstance(query, str) else None
        with self._session.no_autoflush:
            rows = tuple(self._session.execute(
                select(
                    UnderwritingResearchObject.id,
                    UnderwritingResearchObject.kind,
                    UnderwritingResearchObject.canonical_name,
                    UnderwritingResearchObject.external_key,
                    UnderwritingResearchVersion.version_kind,
                )
                .join(
                    UnderwritingResearchVersion,
                    UnderwritingResearchVersion.object_id == UnderwritingResearchObject.id,
                )
            ).mappings())
            families: dict[tuple[UUID, str], tuple[ResearchObjectKind, str, str, int]] = {}
            for row in rows:
                try:
                    object_id, object_kind, canonical_name, external_key = self._validated_research_object_identity(
                        row["id"], row["kind"], row["canonical_name"], row["external_key"],
                    )
                except ValidationError:
                    # A directory item requires a valid controlled identity.
                    # Without one, no unreadable item can satisfy the API
                    # contract safely, so omit the corrupt family entirely.
                    continue
                version_kind = row["version_kind"]
                if not isinstance(version_kind, str) or not version_kind:
                    continue
                if kind is not None and object_kind.value != kind:
                    continue
                if needle is not None and (
                    needle not in canonical_name.casefold()
                    and needle not in external_key.casefold()
                ):
                    continue
                family_key = (object_id, version_kind)
                existing = families.get(family_key)
                if existing is None:
                    families[family_key] = (object_kind, canonical_name, external_key, 1)
                else:
                    families[family_key] = (*existing[:3], existing[3] + 1)

            archive_items: list[ResearchArchiveItem] = []
            for (object_id, version_kind), (object_kind, canonical_name, external_key, version_count) in families.items():
                try:
                    # These are deliberately the established replay APIs, not
                    # a copy of their logic over a current evidence ledger.
                    self.revision_history(object_id, version_kind)
                    latest = self.effective_revision(object_id, version_kind)
                except ValidationError:
                    archive_items.append(ResearchArchiveItem(
                        object_id=object_id,
                        object_kind=object_kind.value,
                        canonical_name=canonical_name,
                        external_key=external_key,
                        version_kind=version_kind,
                        version_count=version_count,
                        lineage_state="unreadable",
                        latest_revision_id=None,
                        latest_sequence=None,
                        cutoff=None,
                        source_manifest_hash=None,
                    ))
                    continue
                archive_items.append(ResearchArchiveItem(
                    object_id=object_id,
                    object_kind=object_kind.value,
                    canonical_name=canonical_name,
                    external_key=external_key,
                    version_kind=version_kind,
                    version_count=version_count,
                    lineage_state="readable",
                    latest_revision_id=latest.id,
                    latest_sequence=latest.sequence,
                    cutoff=latest.cutoff,
                    source_manifest_hash=latest.source_manifest_hash,
                ))

            ordered = tuple(sorted(archive_items, key=self._archive_sort_key))
            remaining = tuple(
                item for item in ordered
                if after is None or self._archive_sort_key(item) > after
            )
            page_items = remaining[:limit]
            next_cursor = (
                self._encode_archive_cursor(self._archive_sort_key(page_items[-1]))
                if len(remaining) > limit
                else None
            )
            return ResearchArchivePage(page_items, next_cursor)

    @staticmethod
    def _group_for(artifact_type: str) -> str:
        if artifact_type in {
            "source_manifest", "metric_definition", "metric_observation", "ledger", "semantic_snapshot",
        }:
            return "evidence"
        if artifact_type == "mechanism":
            return "mechanism"
        if artifact_type in {
            "industry_state", "industry_scenario", "company_exposure", "earnings_engine",
            "forecast_input", "falsifier",
        }:
            return "industry_model"
        if artifact_type == "answerability":
            return "answerability"
        raise ValidationError("research revision parent has an unknown diff group")

    @staticmethod
    def _ref_payload(ref: RevisionArtifactRef | None) -> dict[str, object] | None:
        if ref is None:
            return None
        return {
            "reference": ref.reference,
            "artifact_type": ref.artifact_type,
            "identity": ref.identity,
            "content_hash": ref.content_hash,
            "source_locators": ref.source_locators,
            "unit": ref.unit,
            "period_start": ref.period_start,
            "period_end": ref.period_end,
            "available_at": ref.available_at,
            "status": ref.status,
        }

    @classmethod
    def _change_payload(cls, change: RevisionChange) -> dict[str, object]:
        return {
            "group": change.group,
            "change_type": change.change_type,
            "artifact_type": change.artifact_type,
            "identity": change.identity,
            "before": cls._ref_payload(change.before),
            "after": cls._ref_payload(change.after),
        }

    def _strict_ancestor(
        self, from_revision: UnderwritingResearchVersion, to_revision: UnderwritingResearchVersion,
    ) -> None:
        if (
            from_revision.id == to_revision.id
            or from_revision.object_id != to_revision.object_id
            or from_revision.version_kind != to_revision.version_kind
        ):
            raise ValidationError("research revision diff requires a strict ancestor in one historical family")
        # Validate the persisted family before walking it.  This catches a
        # cycle/skip inserted by an unsafe migration even when neither corrupt
        # row is one of the requested endpoints.
        self._family_rows(from_revision.object_id, from_revision.version_kind)
        seen: set[UUID] = set()
        current = to_revision
        while current.supersedes_id is not None:
            if current.id in seen:
                raise ValidationError("research revision ancestor chain is cyclic")
            seen.add(current.id)
            predecessor = self._revision(current.supersedes_id)
            if (
                predecessor.object_id != to_revision.object_id
                or predecessor.version_kind != to_revision.version_kind
                or predecessor.sequence != current.sequence - 1
            ):
                raise ValidationError("research revision ancestor chain is corrupt")
            if predecessor.id == from_revision.id:
                return
            current = predecessor
        raise ValidationError("research revision diff requires from revision to be an ancestor")

    @staticmethod
    def _index_refs(refs: tuple[RevisionArtifactRef, ...]) -> dict[tuple[str, str], RevisionArtifactRef]:
        indexed: dict[tuple[str, str], RevisionArtifactRef] = {}
        for ref in refs:
            key = (ref.artifact_type, ref.identity)
            if key in indexed:
                raise ValidationError("research revision parent set has duplicate semantic identity")
            indexed[key] = ref
        return indexed

    @classmethod
    def _sorted_changes(cls, changes: Iterable[RevisionChange]) -> tuple[RevisionChange, ...]:
        rank = {"evidence": 0, "mechanism": 1, "industry_model": 2, "answerability": 3}
        return tuple(sorted(
            changes,
            key=lambda item: (
                rank[item.group], item.artifact_type, item.identity, item.change_type,
                item.before.reference if item.before is not None else "",
                item.after.reference if item.after is not None else "",
            ),
        ))

    def revision_diff(self, from_revision_id: UUID, to_revision_id: UUID) -> ResearchRevisionDiff:
        """Compare sealed parents only when ``from`` is a strict ancestor of ``to``.

        The service deliberately resolves both endpoint summaries before
        indexing.  Therefore neither a same-basis unreferenced artefact nor a
        current natural-key head can appear in the result.
        """
        with self._session.no_autoflush:
            from_revision = self._revision(from_revision_id)
            to_revision = self._revision(to_revision_id)
            self._strict_ancestor(from_revision, to_revision)
            before_summary = self.revision_summary(from_revision.id)
            after_summary = self.revision_summary(to_revision.id)
            before = self._index_refs(before_summary.parent_refs)
            after = self._index_refs(after_summary.parent_refs)
            changes: list[RevisionChange] = []
            for artifact_type, identity in sorted(set(before) | set(after)):
                earlier = before.get((artifact_type, identity))
                later = after.get((artifact_type, identity))
                if earlier is None:
                    change_type = "added"
                elif later is None:
                    change_type = "removed"
                elif (
                    earlier.reference != later.reference
                    # Ledger parent descriptor hashes gained a creation-time
                    # wrapper in generic v4. The immutable ledger row ID is
                    # unchanged, so a legacy-to-v4 seal upgrade is not an
                    # evidence replacement in a historical diff.
                    or (
                        artifact_type != "ledger"
                        and earlier.content_hash != later.content_hash
                    )
                ):
                    change_type = "replaced"
                else:
                    continue
                changes.append(RevisionChange(
                    self._group_for(artifact_type), change_type, artifact_type, identity, earlier, later,
                ))
            entries = self._sorted_changes(changes)
            diff_hash = canonical_hash({
                "from_revision_id": str(from_revision.id),
                "to_revision_id": str(to_revision.id),
                "from_content_hash": from_revision.content_hash,
                "to_content_hash": to_revision.content_hash,
                "entries": [self._change_payload(entry) for entry in entries],
            })
            return ResearchRevisionDiff(
                from_revision.id, to_revision.id, from_revision.content_hash,
                to_revision.content_hash, entries, diff_hash,
            )
