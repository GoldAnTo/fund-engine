"""Append-only repository for replayable Wave 2 economic research.

The ORM rows intentionally retain JSON payloads, but this repository never
reuses a caller-owned JSON container: inputs are copied before they enter the
unit of work.  Every correction is a successor row; none of these methods
updates, deletes, or commits.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
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


class UnderwritingResearchRepository:
    """Write version chains and replay them as of a historical basis cutoff."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _basis_cutoff(self, basis_id: UUID) -> datetime:
        basis = self._session.scalar(
            select(UnderwritingHistoricalBasis).where(
                UnderwritingHistoricalBasis.id == basis_id
            )
        )
        if basis is None:
            raise ValidationError("historical basis does not exist")
        return basis.cutoff

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
        return self._session.scalar(statement.limit(1))

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
        self._session.add(row)
        self._session.flush()
        return row

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
