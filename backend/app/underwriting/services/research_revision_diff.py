"""Read immutable underwriting research revision history.

This module is intentionally a read model.  It resolves only the parent
references stored on a historical research version; it never asks a repository
for a current/latest artefact and it owns no write operation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.metrics import MetricObservation as DomainMetricObservation
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
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
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash
from app.underwriting.services.catl_baseline import CatlBaselineService


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

    def _verified_manifest(self, manifest_id: UUID, basis_id: UUID) -> UnderwritingSourceManifestVersion:
        manifest = self._manifest_for_id(manifest_id)
        if manifest is None or manifest.basis_id != basis_id:
            raise self._parent_error("has an invalid source-manifest lineage")
        if not isinstance(manifest.manifest, Mapping) or manifest.content_hash != canonical_hash(manifest.manifest):
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

    def _verify_artifact_lineage(self, artifact_type: str, row: object, revision: UnderwritingResearchVersion) -> None:
        """Recompute parent payload seals and verify its persisted dependencies.

        Immutable-table guards stop ordinary writes.  This additional read-time
        check is for an attacker or migration bug that bypassed those guards:
        a parent whose source locator, dimensions or content changed without a
        corresponding historical publication must not be presented as genuine.
        """
        if artifact_type == "source_manifest":
            assert isinstance(row, UnderwritingSourceManifestVersion)
            self._verified_manifest(row.id, revision.basis_id)
            return
        if artifact_type == "metric_definition":
            assert isinstance(row, UnderwritingMetricDefinitionVersion)
            if row.content_hash != canonical_hash(row.definition):
                raise self._parent_error("has a metric-definition content hash mismatch")
            self._verified_manifest(row.source_manifest_id, revision.basis_id)
            return
        if artifact_type == "metric_observation":
            assert isinstance(row, UnderwritingMetricObservation)
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
            if row.content_hash != canonical_hash(row.payload):
                raise self._parent_error("has a mechanism content hash mismatch")
            self._verified_manifest(row.source_manifest_id, revision.basis_id)
            return
        if artifact_type == "ledger":
            assert isinstance(row, UnderwritingLedgerEntry)
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
        if artifact_type == "answerability" and revision.version_kind == "catl_economic_model_evidence_only":
            # The CATL semantic-snapshot validator below seals every persisted
            # answerability field against its frozen fixture expectation.
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
        if artifact_type in {"mechanism", "industry_state", "ledger", "answerability"}:
            return getattr(row, "object_id")
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
        object_id = self._parent_object_id(artifact_type, row)
        if artifact_type in {
            "mechanism", "industry_state", "industry_scenario", "company_exposure",
            "earnings_engine", "forecast_input", "falsifier", "ledger", "answerability",
        } and object_id != revision.object_id:
            raise self._parent_error("belongs to another research object")
        self._verify_artifact_lineage(artifact_type, row, revision)
        return self._descriptor(artifact_type, row, reference)

    @staticmethod
    def _sort_refs(values: Iterable[RevisionArtifactRef]) -> tuple[RevisionArtifactRef, ...]:
        return tuple(sorted(values, key=lambda item: (item.artifact_type, item.identity, item.reference)))

    def _revision(self, revision_id: UUID) -> UnderwritingResearchVersion:
        row = self._session.get(UnderwritingResearchVersion, revision_id)
        if row is None:
            raise ValidationError("research revision not found")
        return row

    @staticmethod
    def _exactly_one(rows: Iterable[object], label: str) -> object:
        values = tuple(rows)
        if len(values) != 1:
            raise ValidationError(f"CATL semantic snapshot parent set has invalid {label}")
        return values[0]

    def _validate_catl_semantic_snapshot(self, revision: UnderwritingResearchVersion) -> None:
        """Seal CATL's special fixture publication against its complete parent set."""
        fixture = load_catl_fixture()
        if self._stored_datetime(fixture.cutoff) != self._stored_datetime(
            self._session.get(UnderwritingHistoricalBasis, revision.basis_id).cutoff  # type: ignore[union-attr]
        ):
            raise ValidationError("CATL semantic snapshot parent set has a different cutoff")
        manifest = self._exactly_one(
            self._session.scalars(select(UnderwritingSourceManifestVersion).where(
                UnderwritingSourceManifestVersion.basis_id == revision.basis_id,
                UnderwritingSourceManifestVersion.manifest_key == "catl-2024-economic-basis",
            )), "source manifest",
        )
        assert isinstance(manifest, UnderwritingSourceManifestVersion)
        if manifest.manifest_hash != fixture.source_manifest.manifest_hash:
            raise ValidationError("CATL semantic snapshot parent set has an unexpected source manifest")
        expected_parent_ids: set[str] = {str(manifest.id)}
        for observation in fixture.frozen_observations:
            definition = self._exactly_one(
                self._session.scalars(select(UnderwritingMetricDefinitionVersion).where(
                    UnderwritingMetricDefinitionVersion.basis_id == revision.basis_id,
                    UnderwritingMetricDefinitionVersion.source_manifest_id == manifest.id,
                    UnderwritingMetricDefinitionVersion.metric_key == observation.definition_key,
                    UnderwritingMetricDefinitionVersion.version == observation.definition_version,
                )), f"metric definition {observation.definition_key}",
            )
            assert isinstance(definition, UnderwritingMetricDefinitionVersion)
            expected_parent_ids.add(str(definition.id))
            observation_row = self._exactly_one(
                self._session.scalars(select(UnderwritingMetricObservation).where(
                    UnderwritingMetricObservation.basis_id == revision.basis_id,
                    UnderwritingMetricObservation.definition_id == definition.id,
                    UnderwritingMetricObservation.metric_key == observation.definition_key,
                    UnderwritingMetricObservation.definition_version == observation.definition_version,
                    UnderwritingMetricObservation.source_id == observation.source_id,
                    UnderwritingMetricObservation.dimension_hash == canonical_hash(dict(observation.dimensions)),
                    UnderwritingMetricObservation.observed_end == observation.observed_end,
                )), f"metric observation {observation.definition_key}",
            )
            assert isinstance(observation_row, UnderwritingMetricObservation)
            expected_parent_ids.add(str(observation_row.id))
        for mechanism in fixture.mechanisms:
            key = mechanism["key"]
            mechanism_row = self._exactly_one(
                self._session.scalars(select(UnderwritingMechanismPackVersion).where(
                    UnderwritingMechanismPackVersion.object_id == revision.object_id,
                    UnderwritingMechanismPackVersion.basis_id == revision.basis_id,
                    UnderwritingMechanismPackVersion.mechanism_key == key,
                    UnderwritingMechanismPackVersion.version == 1,
                    UnderwritingMechanismPackVersion.status == "candidate",
                )), f"mechanism {key}",
            )
            assert isinstance(mechanism_row, UnderwritingMechanismPackVersion)
            expected_parent_ids.add(str(mechanism_row.id))
        answerability = self._exactly_one(
            self._session.scalars(select(UnderwritingAnswerabilityEvaluation).where(
                UnderwritingAnswerabilityEvaluation.object_id == revision.object_id,
                UnderwritingAnswerabilityEvaluation.basis_id == revision.basis_id,
                UnderwritingAnswerabilityEvaluation.version == 1,
            )), "answerability",
        )
        assert isinstance(answerability, UnderwritingAnswerabilityEvaluation)
        if (
            answerability.state != "not_answerable"
            or tuple(answerability.blockers) != ("missing_key_baseline", "mechanism_unidentified")
            or tuple(answerability.research_debt_keys) != (
                "industry.capacity_utilization_price_cost_baseline", "formal_mechanism_review",
            )
            or not answerability.resolvable_within_mandate
            or answerability.allowed_action != "wait_for_validation"
            or tuple(answerability.resolution_requirements) != (
                "collect comparable capacity, utilization, price, and cost evidence",
                "complete independent mechanism review before formalization",
            )
        ):
            raise ValidationError("CATL semantic snapshot parent set has an unexpected answerability record")
        expected_parent_ids.add(str(answerability.id))
        snapshot_hash, preview_hash = CatlBaselineService._semantic_preview_hash(fixture)
        expected_parent_ids.add(f"semantic_snapshot:{snapshot_hash}")
        if set(revision.parent_ids) != expected_parent_ids:
            raise ValidationError("CATL semantic snapshot parent set does not match frozen fixture")
        if revision.content_hash != preview_hash:
            raise ValidationError("CATL semantic snapshot content hash does not match frozen fixture")

    def _validate_revision_content(self, revision: UnderwritingResearchVersion) -> None:
        if not isinstance(revision.parent_ids, list) or not all(isinstance(value, str) for value in revision.parent_ids):
            raise ValidationError("research revision parents are malformed")
        if len(set(revision.parent_ids)) != len(revision.parent_ids):
            raise ValidationError("research revision parent set is duplicated")
        # Versions written by the generic kernel seal their current historical
        # snapshot plus a canonical parent set.  A semantic snapshot denotes a
        # separately governed fixture publication (such as CATL) whose payload
        # has its own persisted fixture seal, so this generic formula must not
        # be substituted for it.
        snapshot_tokens = tuple(value for value in revision.parent_ids if _SNAPSHOT_TOKEN.fullmatch(value))
        if snapshot_tokens:
            if revision.version_kind != "catl_economic_model_evidence_only" or len(snapshot_tokens) != 1:
                raise ValidationError("research revision semantic snapshot is not governed")
            self._validate_catl_semantic_snapshot(revision)
            return
        kernel = UnderwritingKernelService(self._session, now=lambda: self._stored_datetime(revision.created_at))
        expected = kernel.preview_research_version_hash(
            revision.object_id, revision.basis_id, revision.version_kind, list(revision.parent_ids),
        )
        if revision.content_hash != expected:
            raise ValidationError("research revision content hash does not match frozen parent set")

    def revision_summary(self, revision_id: UUID) -> ResearchRevisionSummary:
        """Describe only parents explicitly frozen into ``revision_id``."""
        with self._session.no_autoflush:
            revision = self._revision(revision_id)
            basis = self._session.get(UnderwritingHistoricalBasis, revision.basis_id)
            if basis is None:
                raise ValidationError("research revision basis is missing")
            self._validate_revision_content(revision)
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
        with self._session.no_autoflush:
            rows = self._family_rows(object_id, version_kind)
            return RevisionHistory(object_id, version_kind, tuple(self.revision_summary(row.id) for row in rows))

    def effective_revision(self, object_id: UUID, version_kind: str) -> ResearchRevisionSummary:
        with self._session.no_autoflush:
            rows = self._family_rows(object_id, version_kind)
            return self.revision_summary(rows[-1].id)
