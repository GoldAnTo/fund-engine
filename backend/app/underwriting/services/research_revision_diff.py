"""Read immutable underwriting research revision history.

This module is intentionally a read model.  It resolves only the parent
references stored on a historical research version; it never asks a repository
for a current/latest artefact and it owns no write operation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingHistoricalBasis,
    UnderwritingLedgerEntry,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingFalsifierVersion,
    UnderwritingForecastInputVersion,
    UnderwritingIndustryScenarioVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricDefinitionVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)
from app.underwriting.services.kernel import canonical_hash


_SNAPSHOT_TOKEN = re.compile(r"semantic_snapshot:[0-9a-f]{64}")


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


@dataclass(frozen=True, slots=True)
class RevisionHistory:
    object_id: UUID
    version_kind: str
    revisions: tuple[ResearchRevisionSummary, ...]


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

    def _locators_from_manifest(self, manifest_id: UUID) -> tuple[str, ...]:
        manifest = self._manifest_for_id(manifest_id)
        if manifest is None or not isinstance(manifest.manifest, Mapping):
            return ()
        return self._manifest_locators(manifest.manifest)

    @staticmethod
    def _answerability_hash(row: UnderwritingAnswerabilityEvaluation) -> str:
        return canonical_hash({
            "object_id": str(row.object_id), "basis_id": str(row.basis_id),
            "version": row.version, "state": row.state,
            "blockers": row.blockers, "research_debt_keys": row.research_debt_keys,
            "resolvable_within_mandate": row.resolvable_within_mandate,
            "allowed_action": row.allowed_action,
            "resolution_requirements": row.resolution_requirements,
        })

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
        )
        return [
            (artifact_type, row)
            for artifact_type, model in model_types
            if (row := self._session.get(model, reference)) is not None
        ]

    def _descriptor(self, artifact_type: str, row: object, reference: str) -> RevisionArtifactRef:
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
                reference, artifact_type, f"{row.ledger_kind}|{row.family_key}|{row.version}", row.content_hash,
                (locator,) if isinstance(locator, str) and locator else (row.source_boundary,),
                row.payload.get("unit") if isinstance(row.payload, Mapping) and isinstance(row.payload.get("unit"), str) else None,
                self._stored_datetime(row.effective_at), self._stored_datetime(row.effective_at),
                self._stored_datetime(row.available_at), row.entry_type,
            )
        if artifact_type == "answerability":
            assert isinstance(row, UnderwritingAnswerabilityEvaluation)
            return RevisionArtifactRef(reference, artifact_type, "answerability", self._answerability_hash(row), (), None, None, None, None, row.state)
        raise AssertionError(f"unrecognised artifact type: {artifact_type}")

    def _resolve_parent(self, revision: UnderwritingResearchVersion, reference: object) -> RevisionArtifactRef:
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
        if basis_id != revision.basis_id:
            raise self._parent_error("belongs to another historical basis")
        return self._descriptor(artifact_type, row, reference)

    @staticmethod
    def _sort_refs(values: Iterable[RevisionArtifactRef]) -> tuple[RevisionArtifactRef, ...]:
        return tuple(sorted(values, key=lambda item: (item.artifact_type, item.identity, item.reference)))

    def _revision(self, revision_id: UUID) -> UnderwritingResearchVersion:
        row = self._session.get(UnderwritingResearchVersion, revision_id)
        if row is None:
            raise ValidationError("research revision not found")
        return row

    def revision_summary(self, revision_id: UUID) -> ResearchRevisionSummary:
        """Describe only parents explicitly frozen into ``revision_id``."""
        revision = self._revision(revision_id)
        basis = self._session.get(UnderwritingHistoricalBasis, revision.basis_id)
        if basis is None:
            raise ValidationError("research revision basis is missing")
        if not isinstance(revision.parent_ids, list):
            raise ValidationError("research revision parents are malformed")
        refs = self._sort_refs(self._resolve_parent(revision, reference) for reference in revision.parent_ids)
        return ResearchRevisionSummary(
            revision.id, revision.object_id, revision.basis_id, revision.version_kind,
            revision.sequence, revision.content_hash, self._stored_datetime(basis.cutoff),
            basis.source_manifest_hash, refs,
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

    def revision_history(self, object_id: UUID, version_kind: str) -> RevisionHistory:
        rows = self._family_rows(object_id, version_kind)
        return RevisionHistory(object_id, version_kind, tuple(self.revision_summary(row.id) for row in rows))

    def effective_revision(self, object_id: UUID, version_kind: str) -> ResearchRevisionSummary:
        rows = self._family_rows(object_id, version_kind)
        return self.revision_summary(rows[-1].id)
