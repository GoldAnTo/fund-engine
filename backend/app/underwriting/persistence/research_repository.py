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
import hashlib
import re
from typing import Mapping, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import bindparam, func, select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.persistence.models import UnderwritingHistoricalBasis
from app.underwriting.persistence.repository import StaleParentError
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
)
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceDossier,
    CandidateEvidenceReview,
)
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_freeze import freeze_manifest


RowT = TypeVar("RowT")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CANDIDATE_DOSSIER_FAMILY_LOCK_DOMAIN = b"underwriting:candidate-dossier-family:v1\x00"
_SUCCESSOR_TABLES = frozenset(
    {
        "uw_source_manifest_versions",
        "uw_metric_definition_versions",
        "uw_mechanism_pack_versions",
        "uw_industry_state_versions",
        "uw_industry_scenario_versions",
        "uw_company_exposure_versions",
        "uw_earnings_engine_versions",
        "uw_forecast_input_versions",
        "uw_falsifier_versions",
        "uw_evidence_candidate_dossier_versions",
    }
)
_SUCCESSOR_CONSTRAINTS = frozenset(
    {
        "uq_uw_source_manifest_version",
        "uq_uw_metric_definition_version",
        "uq_uw_mechanism_pack_version",
        "uq_uw_industry_state_version",
        "uq_uw_industry_scenario_version",
        "uq_uw_company_exposure_version",
        "uq_uw_earnings_engine_version",
        "uq_uw_forecast_input_version",
        "uq_uw_falsifier_version",
        "uq_uw_evidence_candidate_dossier_version",
    }
)
_MECHANISM_NEXT_STATUS = {
    None: "candidate",
    "candidate": "adapted",
    "adapted": "calibrated",
    "calibrated": "human_confirmed",
    "human_confirmed": "formal",
    "formal": "challenged",
    "challenged": "retired_or_replaced",
}


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
    def _decimal(value: Decimal, field: str) -> Decimal:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValidationError(f"{field} must be a finite Decimal")
        return value

    def _created_at_at_basis(self, value: datetime, cutoff: datetime) -> datetime:
        created_at = self._utc(value, "created_at")
        if created_at > cutoff:
            raise ValidationError("created_at must not exceed basis cutoff")
        return created_at

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

    @staticmethod
    def _mechanism_review_proof(payload: Mapping[str, object]) -> tuple[str, str]:
        """Extract the immutable human-confirmation evidence in a payload."""
        identity = payload.get("human_confirmation_identity")
        evidence_id = payload.get("review_evidence_id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValidationError("mechanism review identity is required")
        if not isinstance(evidence_id, str):
            raise ValidationError("mechanism review evidence is required")
        try:
            canonical_evidence_id = str(UUID(evidence_id))
        except ValueError as exc:
            raise ValidationError("mechanism review evidence must be a UUID") from exc
        if evidence_id != canonical_evidence_id:
            raise ValidationError("mechanism review evidence must be a canonical UUID")
        return identity.strip(), canonical_evidence_id

    def _latest(self, statement) -> RowT | None:
        with self._session.no_autoflush:
            return self._session.scalar(statement.limit(1))

    @staticmethod
    def postgresql_candidate_dossier_family_lock(
        *, object_id: UUID, basis_id: UUID, dossier_key: str
    ) -> tuple[object, int]:
        """Build the transaction-scoped lock for one candidate dossier family."""
        identity = f"{object_id}:{basis_id}:{dossier_key}".encode("utf-8")
        lock_id = int.from_bytes(
            hashlib.sha256(_CANDIDATE_DOSSIER_FAMILY_LOCK_DOMAIN + identity).digest()[:8],
            byteorder="big",
            signed=True,
        )
        return (
            select(
                func.pg_advisory_xact_lock(
                    bindparam("candidate_dossier_family_lock_id", value=lock_id)
                )
            ),
            lock_id,
        )

    def _lock_candidate_dossier_family(
        self, *, object_id: UUID, basis_id: UUID, dossier_key: str
    ) -> None:
        """Serialize review and successor writes before either reads current.

        A row lock alone cannot protect a successor insert from a concurrent
        current-row read under PostgreSQL READ COMMITTED.  The namespaced
        advisory lock covers the family even before its first row exists.
        SQLite's single-writer transaction model remains safe for its test
        harness, so it deliberately needs no PostgreSQL-specific statement.
        """
        dialect = self._session.get_bind().dialect.name
        if dialect == "sqlite":
            return
        if dialect != "postgresql":
            raise RuntimeError("database dialect cannot serialize candidate dossier family")
        statement, _ = self.postgresql_candidate_dossier_family_lock(
            object_id=object_id, basis_id=basis_id, dossier_key=dossier_key,
        )
        self._session.execute(statement)

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
            table_name = getattr(row, "__tablename__", "")
            detail = str(exc).lower()
            is_version_conflict = (
                any(constraint in detail for constraint in _SUCCESSOR_CONSTRAINTS)
                or (
                    table_name in _SUCCESSOR_TABLES
                    and table_name in detail
                    and "unique constraint" in detail
                )
            )
            if is_version_conflict:
                raise StaleParentError(
                    "expected parent is not the effective family version"
                ) from exc
            raise
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

    def _verified_candidate_manifest(
        self, manifest_id: UUID, basis_id: UUID, cutoff: datetime
    ) -> UnderwritingSourceManifestVersion:
        """Return the selected frozen manifest after sealing every boundary.

        Candidate evidence carries a source locator, so source-ID membership is
        not sufficient.  Re-freezing the persisted manifest makes the locator
        lookup authoritative and ensures the manifest remains the one sealed by
        the target historical basis.
        """
        manifest = self._manifest_for_basis(manifest_id, basis_id, cutoff)
        basis = self._session.get(UnderwritingHistoricalBasis, basis_id)
        if basis is None:  # ``_basis_cutoff`` already checks this; stay defensive.
            raise ValidationError("historical basis does not exist")
        try:
            frozen = freeze_manifest(manifest.manifest, cutoff)
        except ValidationError as exc:
            raise ValidationError("source manifest is not a verified frozen manifest") from exc
        if frozen.manifest_hash != manifest.manifest_hash:
            raise ValidationError("source manifest frozen hash does not match content")
        if manifest.manifest_hash != basis.source_manifest_hash:
            raise ValidationError("source manifest hash does not match historical basis")
        if manifest.content_hash != canonical_hash(manifest.manifest):
            raise ValidationError("source manifest content hash does not match content")
        return manifest

    @staticmethod
    def _candidate_contract_payload(
        payload: Mapping[str, object]
    ) -> CandidateEvidenceDossier:
        contract = CandidateEvidenceDossier.from_canonical_payload(payload)
        if dict(payload) != contract.canonical_payload:
            raise ValidationError("dossier payload does not match canonical contract")
        return contract

    @staticmethod
    def _review_contract_payload(payload: Mapping[str, object]) -> CandidateEvidenceReview:
        contract = CandidateEvidenceReview.from_canonical_payload(payload)
        if dict(payload) != contract.canonical_payload:
            raise ValidationError("review payload does not match canonical contract")
        return contract

    def _validate_candidate_item_sources(
        self, contract: CandidateEvidenceDossier, manifest: UnderwritingSourceManifestVersion,
        cutoff: datetime,
    ) -> None:
        sources = manifest.manifest.get("sources") if isinstance(manifest.manifest, Mapping) else None
        if not isinstance(sources, list):
            raise ValidationError("source manifest sources must be a list")
        manifest_sources: dict[str, str] = {}
        for source in sources:
            if not isinstance(source, Mapping):
                raise ValidationError("source manifest source must be an object")
            source_id = source.get("source_id")
            locator = source.get("locator")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValidationError("source_id must not be empty")
            if not isinstance(locator, str) or not locator.strip():
                raise ValidationError("source locator is required")
            if source_id in manifest_sources:
                raise ValidationError("source_id must be unique")
            manifest_sources[source_id] = locator
        for item in contract.items:
            if item.available_at > cutoff:
                raise ValidationError("candidate item available_at must not exceed basis cutoff")
            if item.source_id not in manifest_sources:
                raise ValidationError("candidate item references unknown source")
            if item.source_locator != manifest_sources[item.source_id]:
                raise ValidationError("candidate item source_locator does not match source manifest")

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

    def append_candidate_dossier(
        self,
        *,
        object_id: UUID,
        basis_id: UUID,
        source_manifest_id: UUID,
        dossier_key: str,
        payload: Mapping[str, object],
        created_at: datetime,
        expected_parent_id: UUID | None,
    ) -> UnderwritingEvidenceCandidateDossierVersion:
        """Append one source-bound candidate dossier without promoting it.

        The supplied payload is the contract's complete canonical form.  The
        repository derives its version and predecessor from the sealed family,
        rather than trusting an unverified correction chain from the caller.
        """
        cutoff = self._basis_cutoff(basis_id)
        created_at = self._created_at_at_basis(created_at, cutoff)
        contract = self._candidate_contract_payload(payload)
        if (
            contract.object_id != object_id
            or contract.basis_id != basis_id
            or contract.source_manifest_id != source_manifest_id
            or contract.dossier_key != dossier_key
            or contract.created_at != created_at
        ):
            raise ValidationError("dossier arguments do not match canonical payload")
        manifest = self._verified_candidate_manifest(source_manifest_id, basis_id, cutoff)
        if contract.source_manifest_hash != manifest.manifest_hash:
            raise ValidationError("dossier source_manifest_hash does not match source manifest")
        self._validate_candidate_item_sources(contract, manifest, cutoff)
        self._lock_candidate_dossier_family(
            object_id=object_id, basis_id=basis_id, dossier_key=dossier_key,
        )

        if expected_parent_id is not None:
            parent = self._session.get(
                UnderwritingEvidenceCandidateDossierVersion, expected_parent_id
            )
            if parent is not None and (
                parent.object_id != object_id or parent.basis_id != basis_id
            ):
                raise ValidationError("dossier successor must share object and basis")
            if parent is not None and (
                parent.dossier_key != dossier_key
                or parent.source_manifest_id != source_manifest_id
                or parent.source_manifest_hash != contract.source_manifest_hash
            ):
                raise ValidationError("dossier successor must preserve immutable references")
        current = self._latest(
            select(UnderwritingEvidenceCandidateDossierVersion)
            .where(
                UnderwritingEvidenceCandidateDossierVersion.object_id == object_id,
                UnderwritingEvidenceCandidateDossierVersion.basis_id == basis_id,
                UnderwritingEvidenceCandidateDossierVersion.dossier_key == dossier_key,
            )
            .order_by(
                UnderwritingEvidenceCandidateDossierVersion.version.desc(),
                UnderwritingEvidenceCandidateDossierVersion.id.desc(),
            )
        )
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        expected_version = (current.version + 1) if current else 1
        expected_supersedes_id = current.id if current else None
        if (
            contract.version != expected_version
            or contract.supersedes_id != expected_supersedes_id
        ):
            raise ValidationError("dossier successor version or predecessor is not canonical")
        return self._append(
            UnderwritingEvidenceCandidateDossierVersion.from_contract(
                id=uuid4(), contract=contract
            )
        )

    def append_candidate_review(
        self,
        *,
        dossier_id: UUID,
        dossier_content_hash: str,
        reviewer_identity: str,
        reviewer_role: str,
        decision: str,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> UnderwritingEvidenceCandidateReviewVersion:
        """Append an independently identified review of one exact dossier."""
        dossier = self._session.get(UnderwritingEvidenceCandidateDossierVersion, dossier_id)
        if dossier is None:
            raise ValidationError("candidate dossier does not exist")
        self._lock_candidate_dossier_family(
            object_id=dossier.object_id,
            basis_id=dossier.basis_id,
            dossier_key=dossier.dossier_key,
        )
        current = self._latest(
            select(UnderwritingEvidenceCandidateDossierVersion)
            .where(
                UnderwritingEvidenceCandidateDossierVersion.object_id == dossier.object_id,
                UnderwritingEvidenceCandidateDossierVersion.basis_id == dossier.basis_id,
                UnderwritingEvidenceCandidateDossierVersion.dossier_key == dossier.dossier_key,
            )
            .order_by(
                UnderwritingEvidenceCandidateDossierVersion.version.desc(),
                UnderwritingEvidenceCandidateDossierVersion.id.desc(),
            )
        )
        if current is None or current.id != dossier.id:
            raise ValidationError("candidate dossier is no longer current")
        cutoff = self._basis_cutoff(dossier.basis_id)
        created_at = self._created_at_at_basis(created_at, cutoff)
        contract = self._review_contract_payload(payload)
        if (
            contract.dossier_id != dossier_id
            or contract.dossier_content_hash != dossier_content_hash
            or contract.reviewer_identity != reviewer_identity
            or contract.reviewer_role != reviewer_role
            or contract.decision != decision
            or contract.reviewed_at != created_at
        ):
            raise ValidationError("review arguments do not match canonical payload")
        self._hash(dossier_content_hash, "dossier_content_hash")
        if dossier.content_hash != dossier_content_hash:
            raise ValidationError("review dossier_content_hash does not match dossier")
        if contract.reviewed_at > cutoff:
            raise ValidationError("reviewed_at must not exceed basis cutoff")
        same_identity = self._session.scalar(
            select(UnderwritingEvidenceCandidateReviewVersion.id).where(
                UnderwritingEvidenceCandidateReviewVersion.dossier_id == dossier_id,
                UnderwritingEvidenceCandidateReviewVersion.reviewer_identity == reviewer_identity,
            )
        )
        if same_identity is not None:
            raise ValidationError("candidate reviews require different reviewer identities")
        same_role = self._session.scalar(
            select(UnderwritingEvidenceCandidateReviewVersion.id).where(
                UnderwritingEvidenceCandidateReviewVersion.dossier_id == dossier_id,
                UnderwritingEvidenceCandidateReviewVersion.reviewer_role == reviewer_role,
            )
        )
        if same_role is not None:
            raise ValidationError("candidate dossier already has a review for this role")
        return self._append(
            UnderwritingEvidenceCandidateReviewVersion.from_contract(
                id=uuid4(), contract=contract
            )
        )

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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
        reconciliation_tolerance = self._decimal(
            reconciliation_tolerance, "reconciliation_tolerance"
        )
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
        created_at = self._created_at_at_basis(created_at, cutoff)
        observed_start = self._utc(observed_start, "observed_start")
        observed_end = self._utc(observed_end, "observed_end")
        effective_at = self._utc(effective_at, "effective_at")
        available_at = self._utc(available_at, "available_at")
        if observed_start > observed_end:
            raise ValidationError("observed_start must not exceed observed_end")
        if available_at > cutoff:
            raise ValidationError("available_at must not exceed basis cutoff")
        value = self._decimal(value, "value")
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        if current is not None and (
            current.object_id != object_id or current.basis_id != basis_id
        ):
            raise ValidationError("mechanism successor must share object and basis")
        self._require_expected_parent(current.id if current else None, expected_parent_id)
        if status == "formal" and (current is None or current.status != "human_confirmed"):
            raise ValidationError("formal mechanism requires a human_confirmed predecessor")
        if _MECHANISM_NEXT_STATUS.get(current.status if current else None) != status:
            raise ValidationError("mechanism lifecycle transition is not allowed")
        if status == "human_confirmed":
            self._mechanism_review_proof(payload)
        if status == "formal":
            if current is None:
                raise AssertionError("formal predecessor was validated above")
            if current.object_id != object_id or current.basis_id != basis_id:
                raise ValidationError("formal mechanism predecessor must share object and basis")
            current_identity, current_evidence_id = self._mechanism_review_proof(current.payload)
            identity, evidence_id = self._mechanism_review_proof(payload)
            if (identity, evidence_id) != (current_identity, current_evidence_id):
                raise ValidationError("formal mechanism review proof must match human_confirmed predecessor")
            if payload.get("predecessor_status") != "human_confirmed":
                raise ValidationError("formal mechanism payload must name human_confirmed predecessor")
            if payload.get("predecessor_version") != current.version:
                raise ValidationError("formal mechanism predecessor version does not match")
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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
        created_at = self._created_at_at_basis(created_at, cutoff)
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

    def effective_candidate_reviews(
        self, dossier_id: UUID
    ) -> list[UnderwritingEvidenceCandidateReviewVersion]:
        """Read reviews bound to this dossier only; successors never inherit them."""
        return list(
            self._session.scalars(
                select(UnderwritingEvidenceCandidateReviewVersion)
                .where(UnderwritingEvidenceCandidateReviewVersion.dossier_id == dossier_id)
                .order_by(
                    UnderwritingEvidenceCandidateReviewVersion.reviewer_role,
                    UnderwritingEvidenceCandidateReviewVersion.reviewed_at,
                    UnderwritingEvidenceCandidateReviewVersion.id,
                )
            )
        )
