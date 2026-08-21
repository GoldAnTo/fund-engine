"""Append-only repository for replayable Wave 2 economic research.

The ORM rows intentionally retain JSON payloads, but this repository never
reuses a caller-owned JSON container: inputs are copied before they enter the
unit of work.  Every correction is a successor row; none of these methods
updates, deletes, or commits.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
import re
from typing import Mapping, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.persistence.models import UnderwritingHistoricalBasis
from app.underwriting.persistence.repository import StaleParentError
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


RowT = TypeVar("RowT")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class UnderwritingResearchRepository:
    """Write version chains and replay them as of a historical basis cutoff."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _basis_cutoff(self, basis_id: UUID) -> datetime:
        with self._session.no_autoflush:
            basis = self._session.scalar(
                select(UnderwritingHistoricalBasis).where(
                    UnderwritingHistoricalBasis.id == basis_id
                )
            )
        if basis is None:
            raise ValidationError("historical basis does not exist")
        return self._utc(basis.cutoff, "basis cutoff")

    @staticmethod
    def _utc(value: datetime, field: str) -> datetime:
        if not isinstance(value, datetime):
            raise ValidationError(f"{field} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _hash(value: str, field: str = "content_hash") -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValidationError(f"{field} must be a lowercase SHA-256")
        return value

    @staticmethod
    def _require_expected_parent(
        current_id: UUID | None, expected_parent_id: UUID | None
    ) -> None:
        if current_id != expected_parent_id:
            raise StaleParentError("expected parent is not the effective family version")

    @staticmethod
    def _json(value: Mapping[str, object]) -> dict[str, object]:
        """Detach persisted JSON from the caller's mutable input."""
        return deepcopy(dict(value))

    def _latest(self, statement) -> RowT | None:
        with self._session.no_autoflush:
            return self._session.scalar(statement.limit(1))

    def _append(self, row: RowT) -> RowT:
        """Flush an append in a savepoint and map CAS-race conflicts.

        A unique-version collision after the predecessor check means another
        writer won the successor race.  The caller must reread and retry with
        the new effective parent rather than seeing a raw database error.
        """
        from sqlalchemy.exc import IntegrityError

        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            raise StaleParentError(
                "expected parent is not the effective family version"
            ) from exc
        return row

    def _source_ids_in_manifest(
        self, manifest: Mapping[str, object], cutoff: datetime
    ) -> set[str]:
        sources = manifest.get("sources", [])
        if not isinstance(sources, list):
            raise ValidationError("source manifest sources must be a list")
        source_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, Mapping):
                raise ValidationError("source manifest source must be an object")
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValidationError("source_id must not be empty")
            first_available_at = source.get("first_available_at")
            if not isinstance(first_available_at, str):
                raise ValidationError("source first_available_at is required")
            try:
                available_at = datetime.fromisoformat(first_available_at)
            except ValueError as exc:
                raise ValidationError(
                    "source first_available_at must be an ISO-8601 timestamp"
                ) from exc
            if self._utc(available_at, "source first_available_at") > cutoff:
                raise ValidationError("source is unavailable at basis cutoff")
            if source_id in source_ids:
                raise ValidationError("source_id must be unique")
            source_ids.add(source_id)
        return source_ids

    def _manifest_for_basis(
        self, manifest_id: UUID, basis_id: UUID, cutoff: datetime
    ) -> UnderwritingSourceManifestVersion:
        row = self._session.get(UnderwritingSourceManifestVersion, manifest_id)
        if row is None:
            raise ValidationError("source manifest does not exist")
        if row.basis_id != basis_id:
            raise ValidationError("source manifest must belong to the target basis")
        if self._utc(row.created_at, "source manifest created_at") > cutoff:
            raise ValidationError("source manifest is unavailable at basis cutoff")
        self._source_ids_in_manifest(row.manifest, cutoff)
        return row

    def _definition_for_basis(
        self,
        definition_id: UUID,
        basis_id: UUID,
        cutoff: datetime,
        *,
        metric_key: str | None = None,
        version: int | None = None,
    ) -> UnderwritingMetricDefinitionVersion:
        row = self._session.get(UnderwritingMetricDefinitionVersion, definition_id)
        if row is None:
            raise ValidationError("metric definition does not exist")
        if row.basis_id != basis_id:
            raise ValidationError("metric definition must belong to the target basis")
        if metric_key is not None and row.metric_key != metric_key:
            raise ValidationError("metric definition does not match metric key")
        if version is not None and row.version != version:
            raise ValidationError("metric definition does not match definition version")
        if self._utc(row.created_at, "metric definition created_at") > cutoff:
            raise ValidationError("metric definition is unavailable at basis cutoff")
        self._manifest_for_basis(row.source_manifest_id, basis_id, cutoff)
        return row

    def _mechanism_for_scope(
        self, mechanism_id: UUID, object_id: UUID, basis_id: UUID, cutoff: datetime
    ) -> UnderwritingMechanismPackVersion:
        row = self._session.get(UnderwritingMechanismPackVersion, mechanism_id)
        if row is None:
            raise ValidationError("mechanism does not exist")
        if row.object_id != object_id or row.basis_id != basis_id:
            raise ValidationError("mechanism must belong to the target object and basis")
        if self._utc(row.created_at, "mechanism created_at") > cutoff:
            raise ValidationError("mechanism is unavailable at basis cutoff")
        return row

    def _industry_state_for_basis(
        self, industry_state_id: UUID, basis_id: UUID, cutoff: datetime
    ) -> UnderwritingIndustryStateVersion:
        row = self._session.get(UnderwritingIndustryStateVersion, industry_state_id)
        if row is None:
            raise ValidationError("industry state does not exist")
        if row.basis_id != basis_id:
            raise ValidationError("industry state must belong to the target basis")
        if self._utc(row.created_at, "industry state created_at") > cutoff:
            raise ValidationError("industry state is unavailable at basis cutoff")
        return row

    def add_source_manifest(
        self,
        *,
        manifest_key: str,
        basis_id: UUID,
        manifest: Mapping[str, object],
        manifest_hash: str,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingSourceManifestVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        if created_at > cutoff:
            raise ValidationError("source manifest is unavailable at basis cutoff")
        self._hash(manifest_hash, "manifest_hash")
        self._hash(content_hash)
        self._source_ids_in_manifest(manifest, cutoff)
        current = self._latest(
            select(UnderwritingSourceManifestVersion)
            .where(UnderwritingSourceManifestVersion.manifest_key == manifest_key)
            .order_by(
                UnderwritingSourceManifestVersion.version.desc(),
                UnderwritingSourceManifestVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingSourceManifestVersion(
            manifest_key=manifest_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            manifest=self._json(manifest),
            manifest_hash=manifest_hash,
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_metric_definition(
        self,
        *,
        metric_key: str,
        basis_id: UUID,
        source_manifest_id: UUID,
        definition: Mapping[str, object],
        unit: str,
        period_semantics: str,
        source_role: str,
        aggregation: str,
        reconciliation_tolerance: Decimal,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingMetricDefinitionVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        if created_at > cutoff:
            raise ValidationError("metric definition is unavailable at basis cutoff")
        self._hash(content_hash)
        self._manifest_for_basis(source_manifest_id, basis_id, cutoff)
        current = self._latest(
            select(UnderwritingMetricDefinitionVersion)
            .where(UnderwritingMetricDefinitionVersion.metric_key == metric_key)
            .order_by(
                UnderwritingMetricDefinitionVersion.version.desc(),
                UnderwritingMetricDefinitionVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingMetricDefinitionVersion(
            metric_key=metric_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            source_manifest_id=source_manifest_id,
            definition=self._json(definition),
            unit=unit,
            period_semantics=period_semantics,
            source_role=source_role,
            aggregation=aggregation,
            reconciliation_tolerance=reconciliation_tolerance,
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def add_metric_observation(
        self,
        *,
        metric_key: str,
        definition_version: int,
        basis_id: UUID,
        definition_id: UUID,
        source_manifest_id: UUID,
        source_id: str,
        value: Decimal,
        unit: str,
        observed_start: datetime,
        observed_end: datetime,
        effective_at: datetime,
        available_at: datetime,
        source_locator: str,
        dimensions: Mapping[str, object],
        dimension_hash: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingMetricObservation:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        observed_start = self._utc(observed_start, "observed_start")
        observed_end = self._utc(observed_end, "observed_end")
        effective_at = self._utc(effective_at, "effective_at")
        available_at = self._utc(available_at, "available_at")
        if observed_start > observed_end:
            raise ValidationError("observed_start must not exceed observed_end")
        if available_at > cutoff:
            raise ValidationError("available_at must not exceed basis cutoff")
        self._hash(dimension_hash, "dimension_hash")
        self._hash(content_hash)
        manifest = self._manifest_for_basis(source_manifest_id, basis_id, cutoff)
        definition = self._definition_for_basis(
            definition_id, basis_id, cutoff,
            metric_key=metric_key, version=definition_version,
        )
        if definition.source_manifest_id != source_manifest_id:
            raise ValidationError("metric definition must reference the target source manifest")
        if source_id not in self._source_ids_in_manifest(manifest.manifest, cutoff):
            raise ValidationError("observation references unknown source")
        row = UnderwritingMetricObservation(
            metric_key=metric_key,
            definition_version=definition_version,
            version=1,
            basis_id=basis_id,
            definition_id=definition_id,
            source_manifest_id=source_manifest_id,
            source_id=source_id,
            value=value,
            unit=unit,
            observed_start=observed_start,
            observed_end=observed_end,
            effective_at=effective_at,
            available_at=available_at,
            source_locator=source_locator,
            dimensions=self._json(dimensions),
            dimension_hash=dimension_hash,
            content_hash=content_hash,
            supersedes_id=None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def append_mechanism(
        self,
        *,
        mechanism_key: str,
        object_id: UUID,
        basis_id: UUID,
        source_manifest_id: UUID,
        status: str,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
        source_ids: list[str] | None = None,
        definition_ids: list[str] | None = None,
    ) -> UnderwritingMechanismPackVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        manifest = self._manifest_for_basis(source_manifest_id, basis_id, cutoff)
        for definition_id in definition_ids or []:
            definition = self._definition_for_basis(UUID(definition_id), basis_id, cutoff)
            if definition.source_manifest_id != source_manifest_id:
                raise ValidationError("metric definition must reference the target source manifest")
        known_source_ids = self._source_ids_in_manifest(manifest.manifest, cutoff)
        if not set(source_ids or []).issubset(known_source_ids):
            raise ValidationError("mechanism references unknown source")
        current = self._latest(
            select(UnderwritingMechanismPackVersion)
            .where(UnderwritingMechanismPackVersion.mechanism_key == mechanism_key)
            .order_by(
                UnderwritingMechanismPackVersion.version.desc(),
                UnderwritingMechanismPackVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingMechanismPackVersion(
            mechanism_key=mechanism_key,
            version=(current.version + 1) if current else 1,
            object_id=object_id,
            basis_id=basis_id,
            source_manifest_id=source_manifest_id,
            status=status,
            payload=self._json(payload),
            source_ids=list(source_ids or []),
            definition_ids=list(definition_ids or []),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_industry_state(
        self,
        *,
        object_id: UUID,
        basis_id: UUID,
        mechanism_id: UUID,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingIndustryStateVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        self._mechanism_for_scope(mechanism_id, object_id, basis_id, cutoff)
        current = self._latest(
            select(UnderwritingIndustryStateVersion)
            .where(
                UnderwritingIndustryStateVersion.object_id == object_id,
                UnderwritingIndustryStateVersion.basis_id == basis_id,
            )
            .order_by(
                UnderwritingIndustryStateVersion.version.desc(),
                UnderwritingIndustryStateVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingIndustryStateVersion(
            object_id=object_id,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            mechanism_id=mechanism_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_industry_scenario(
        self,
        *,
        industry_state_id: UUID,
        scenario_key: str,
        basis_id: UUID,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingIndustryScenarioVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        self._industry_state_for_basis(industry_state_id, basis_id, cutoff)
        current = self._latest(
            select(UnderwritingIndustryScenarioVersion)
            .where(
                UnderwritingIndustryScenarioVersion.industry_state_id == industry_state_id,
                UnderwritingIndustryScenarioVersion.scenario_key == scenario_key,
            )
            .order_by(
                UnderwritingIndustryScenarioVersion.version.desc(),
                UnderwritingIndustryScenarioVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingIndustryScenarioVersion(
            industry_state_id=industry_state_id,
            scenario_key=scenario_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_company_exposure(
        self,
        *,
        company_id: UUID,
        industry_state_id: UUID,
        exposure_key: str,
        basis_id: UUID,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingCompanyExposureVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        self._industry_state_for_basis(industry_state_id, basis_id, cutoff)
        current = self._latest(
            select(UnderwritingCompanyExposureVersion)
            .where(
                UnderwritingCompanyExposureVersion.company_id == company_id,
                UnderwritingCompanyExposureVersion.industry_state_id == industry_state_id,
                UnderwritingCompanyExposureVersion.exposure_key == exposure_key,
            )
            .order_by(
                UnderwritingCompanyExposureVersion.version.desc(),
                UnderwritingCompanyExposureVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingCompanyExposureVersion(
            company_id=company_id,
            industry_state_id=industry_state_id,
            exposure_key=exposure_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_earnings_engine(
        self,
        *,
        company_id: UUID,
        basis_id: UUID,
        industry_state_id: UUID | None,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingEarningsEngineVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        if industry_state_id is not None:
            self._industry_state_for_basis(industry_state_id, basis_id, cutoff)
        current = self._latest(
            select(UnderwritingEarningsEngineVersion)
            .where(
                UnderwritingEarningsEngineVersion.company_id == company_id,
                UnderwritingEarningsEngineVersion.basis_id == basis_id,
            )
            .order_by(
                UnderwritingEarningsEngineVersion.version.desc(),
                UnderwritingEarningsEngineVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingEarningsEngineVersion(
            company_id=company_id,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            industry_state_id=industry_state_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_forecast_input(
        self,
        *,
        company_id: UUID,
        basis_id: UUID,
        input_key: str,
        earnings_engine_id: UUID | None,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingForecastInputVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        if earnings_engine_id is not None:
            engine = self._session.get(UnderwritingEarningsEngineVersion, earnings_engine_id)
            if engine is None:
                raise ValidationError("earnings engine does not exist")
            if engine.company_id != company_id or engine.basis_id != basis_id:
                raise ValidationError("earnings engine must belong to the target company and basis")
            if self._utc(engine.created_at, "earnings engine created_at") > cutoff:
                raise ValidationError("earnings engine is unavailable at basis cutoff")
        current = self._latest(
            select(UnderwritingForecastInputVersion)
            .where(
                UnderwritingForecastInputVersion.company_id == company_id,
                UnderwritingForecastInputVersion.basis_id == basis_id,
                UnderwritingForecastInputVersion.input_key == input_key,
            )
            .order_by(
                UnderwritingForecastInputVersion.version.desc(),
                UnderwritingForecastInputVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingForecastInputVersion(
            company_id=company_id,
            input_key=input_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            earnings_engine_id=earnings_engine_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def append_falsifier(
        self,
        *,
        mechanism_id: UUID,
        falsifier_key: str,
        basis_id: UUID,
        payload: Mapping[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingFalsifierVersion:
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._utc(created_at, "created_at")
        self._hash(content_hash)
        mechanism = self._session.get(UnderwritingMechanismPackVersion, mechanism_id)
        if mechanism is None:
            raise ValidationError("mechanism does not exist")
        if mechanism.basis_id != basis_id:
            raise ValidationError("mechanism must belong to the target basis")
        if self._utc(mechanism.created_at, "mechanism created_at") > cutoff:
            raise ValidationError("mechanism is unavailable at basis cutoff")
        current = self._latest(
            select(UnderwritingFalsifierVersion)
            .where(
                UnderwritingFalsifierVersion.mechanism_id == mechanism_id,
                UnderwritingFalsifierVersion.falsifier_key == falsifier_key,
            )
            .order_by(
                UnderwritingFalsifierVersion.version.desc(),
                UnderwritingFalsifierVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        row = UnderwritingFalsifierVersion(
            mechanism_id=mechanism_id,
            falsifier_key=falsifier_key,
            version=(current.version + 1) if current else 1,
            basis_id=basis_id,
            payload=self._json(payload),
            content_hash=content_hash,
            supersedes_id=current.id if current else None,
            created_at=created_at,
        )
        return self._append(row)

    def effective_metric_definitions_at(
        self, basis_id: UUID
    ) -> list[UnderwritingMetricDefinitionVersion]:
        cutoff = self._basis_cutoff(basis_id)
        rows = self._session.scalars(
            select(UnderwritingMetricDefinitionVersion)
            .where(UnderwritingMetricDefinitionVersion.created_at <= cutoff)
            .order_by(
                UnderwritingMetricDefinitionVersion.metric_key,
                UnderwritingMetricDefinitionVersion.version,
                UnderwritingMetricDefinitionVersion.id,
            )
        )
        effective: dict[str, UnderwritingMetricDefinitionVersion] = {}
        for row in rows:
            effective[row.metric_key] = row
        return [effective[key] for key in sorted(effective)]

    def effective_observations_at(
        self, object_id: UUID, basis_id: UUID
    ) -> list[UnderwritingMetricObservation]:
        # Metric observations are globally reusable; object scope lives in the
        # consuming mechanism/industry artefact.  Still validate the supplied
        # basis and make the cutoff explicit so callers cannot read ahead.
        del object_id
        cutoff = self._basis_cutoff(basis_id)
        return list(
            self._session.scalars(
                select(UnderwritingMetricObservation)
                .where(UnderwritingMetricObservation.available_at <= cutoff)
                .order_by(
                    UnderwritingMetricObservation.metric_key,
                    UnderwritingMetricObservation.observed_end,
                    UnderwritingMetricObservation.dimension_hash,
                    UnderwritingMetricObservation.source_id,
                    UnderwritingMetricObservation.id,
                )
            )
        )

    def formal_mechanisms_at(
        self, object_id: UUID, basis_id: UUID
    ) -> list[UnderwritingMechanismPackVersion]:
        cutoff = self._basis_cutoff(basis_id)
        rows = self._session.scalars(
            select(UnderwritingMechanismPackVersion)
            .where(
                UnderwritingMechanismPackVersion.object_id == object_id,
                UnderwritingMechanismPackVersion.created_at <= cutoff,
            )
            .order_by(
                UnderwritingMechanismPackVersion.mechanism_key,
                UnderwritingMechanismPackVersion.version,
                UnderwritingMechanismPackVersion.id,
            )
        )
        effective: dict[str, UnderwritingMechanismPackVersion] = {}
        for row in rows:
            effective[row.mechanism_key] = row
        return [row for _, row in sorted(effective.items()) if row.status == "formal"]

    def latest_industry_state(
        self, object_id: UUID, basis_id: UUID
    ) -> UnderwritingIndustryStateVersion | None:
        cutoff = self._basis_cutoff(basis_id)
        return self._latest(
            select(UnderwritingIndustryStateVersion)
            .where(
                UnderwritingIndustryStateVersion.object_id == object_id,
                UnderwritingIndustryStateVersion.basis_id == basis_id,
                UnderwritingIndustryStateVersion.created_at <= cutoff,
            )
            .order_by(
                UnderwritingIndustryStateVersion.version.desc(),
                UnderwritingIndustryStateVersion.id.desc(),
            )
        )

    def latest_earnings_engine(
        self, company_id: UUID, basis_id: UUID
    ) -> UnderwritingEarningsEngineVersion | None:
        cutoff = self._basis_cutoff(basis_id)
        return self._latest(
            select(UnderwritingEarningsEngineVersion)
            .where(
                UnderwritingEarningsEngineVersion.company_id == company_id,
                UnderwritingEarningsEngineVersion.basis_id == basis_id,
                UnderwritingEarningsEngineVersion.created_at <= cutoff,
            )
            .order_by(
                UnderwritingEarningsEngineVersion.version.desc(),
                UnderwritingEarningsEngineVersion.id.desc(),
            )
        )
