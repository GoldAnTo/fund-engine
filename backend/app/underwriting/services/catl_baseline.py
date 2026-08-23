"""Publish the CATL fixture without upgrading missing evidence into a model.

The frozen 2024 fixture is intentionally incomplete: it has candidate
mechanisms and explicit industry-data gaps.  This service persists what is
actually known, records those candidate beliefs, and publishes only an
evidence-only historical research version.  It must never manufacture the
industry inputs or human-review proof needed for a formal state/earnings model.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.types import (
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.fixtures.catl_baseline import CatlBaselineFixture, thaw_fixture_json, validate_full_observation_fixture
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.persistence.repository import UnderwritingRepository
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingHistoricalBasis,
    UnderwritingLedgerEntry,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.research_models import (
    UnderwritingMechanismPackVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash
from app.underwriting.services.revision_parent_seal import (
    CATL_PARENT_SET_ENTRY_TYPE,
    CATL_PARENT_SET_FAMILY,
    answerability_content_hash,
    catl_parent_set_seal_payload,
    catl_revision_content_hash,
    parent_set_semantic_hash,
)
from app.underwriting.services.source_freeze import (
    validate_frozen_observation_set,
    validate_frozen_source_manifest,
)


_MECHANISM_KEYS = frozenset(
    {
        "ev_storage_demand_to_shipments",
        "effective_capacity_to_utilization_and_price",
        "material_cost_pass_through_to_unit_margin",
        "certification_overseas_footprint_to_obtainable_share",
        "capex_working_capital_to_free_cash_flow",
        "counter_model_customer_bargaining_and_oversupply",
    }
)
_REQUIRED_GAP_KEYS = frozenset({
    "industry.nominal_capacity_gwh", "industry.effective_capacity_gwh",
})


@dataclass(frozen=True, slots=True)
class CatlBaselineImport:
    industry: object
    company: object
    security: object
    basis: object
    source_manifest: object
    metric_observations: tuple[object, ...]
    evidence_gaps: tuple[object, ...]
    candidate_mechanisms: tuple[object, ...]
    formal_mechanisms: tuple[object, ...]
    industry_state: None
    earnings_engine: None
    answerability: object
    research_version: object
    snapshot_hash: str
    preview_hash: str


class CatlBaselineService:
    """Caller-owned atomic importer for the deliberately evidence-only CATL basis."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._kernel = UnderwritingKernelService(session, now=now)
        self._research = UnderwritingResearchRepository(session)
        self._repository = UnderwritingRepository(session)

    @staticmethod
    def _manifest_payload(fixture: CatlBaselineFixture) -> dict[str, object]:
        return {
            "schema_version": fixture.source_manifest.schema_version,
            "cutoff": fixture.source_manifest.cutoff.isoformat(),
            "sources": [dict(source) for source in fixture.source_manifest.sources],
        }

    @staticmethod
    def _definition_payload(observation, source_role: str) -> dict[str, object]:
        return {
            "metric_key": observation.definition_key,
            "definition_version": observation.definition_version,
            "unit": observation.unit,
            "period_semantics": "flow",
            "source_role": source_role,
            "aggregation": "none",
            "reconciliation_tolerance": "1000" if observation.definition_key.endswith(("revenue", "cost")) else "0",
        }

    def _append_reality(self, *, object_id: UUID, basis_id: UUID, observation, evidence) -> object:
        dimensions = dict(observation.dimensions)
        derivation = (
            {
                "formula": dimensions["_derivation_formula"],
                "parent_observation_ids": dimensions["_derivation_parent_observation_ids"].split(","),
                "parent_content_hashes": dimensions["_derivation_parent_content_hashes"].split(","),
            }
            if evidence.observation_status == "derived"
            else None
        )
        return self._kernel.append_ledger_entry(
            object_id,
            basis_id,
            LedgerEntryInput(
                LedgerKind.REALITY,
                f"metric:{observation.definition_key}",
                evidence.observation_status,
                {
                    "metric_key": observation.definition_key,
                    "observation_id": str(observation.observation_id),
                    "content_hash": observation.content_hash,
                    "source_id": observation.source_id,
                    "source_role": evidence.source_role,
                    "observation_status": evidence.observation_status,
                    "value": str(observation.value),
                    "derivation": derivation,
                },
                observation.effective_at,
                observation.available_at,
                "frozen_source_manifest",
            ),
            None,
        )

    @staticmethod
    def _semantic_snapshot_hash(fixture: CatlBaselineFixture) -> str:
        return canonical_hash({
            "source_manifest_hash": fixture.source_manifest.manifest_hash,
            "observation_content_hashes": sorted(item.content_hash for item in fixture.frozen_observations),
            "evidence_gaps": sorted(
                canonical_hash({
                    "metric_key": item.definition_key, "unit": item.unit,
                    "observed_start": item.observed_start, "observed_end": item.observed_end,
                    "effective_at": item.effective_at, "available_at": item.available_at,
                    "source_id": item.source_id, "source_locator": item.source_locator,
                    "source_role": item.source_role, "status": item.observation_status,
                    "dimensions": dict(item.dimensions),
                }) for item in fixture.observations if item.value is None
            ),
            "candidate_mechanism_hashes": sorted(canonical_hash(item) for item in fixture.mechanisms),
            "answerability": {
                "state": "not_answerable",
                "blockers": ["missing_key_baseline", "mechanism_unidentified"],
                "allowed_action": "wait_for_validation",
            },
        })

    @classmethod
    def _semantic_preview_hash(
        cls, fixture: CatlBaselineFixture, *, parent_set_semantic_hash_value: str
    ) -> tuple[str, str]:
        snapshot_hash = cls._semantic_snapshot_hash(fixture)
        token = f"semantic_snapshot:{snapshot_hash}"
        return snapshot_hash, catl_revision_content_hash(
            semantic_snapshot_token=token, parent_set_semantic_hash=parent_set_semantic_hash_value,
        )

    @staticmethod
    def _required_gaps(fixture: CatlBaselineFixture) -> tuple[object, ...]:
        gaps = tuple(item for item in fixture.observations if item.value is None)
        gap_keys = {item.definition_key for item in gaps}
        if not _REQUIRED_GAP_KEYS.issubset(gap_keys):
            raise ValidationError("required evidence gaps are missing from CATL fixture")
        if any(item.observation_status != "unknown" for item in gaps):
            raise ValidationError("evidence gaps must have unknown status")
        return gaps

    def _append_evidence_gap(self, *, industry_id: UUID, basis_id: UUID, gap) -> object:
        return self._kernel.append_ledger_entry(
            industry_id, basis_id,
            LedgerEntryInput(
                LedgerKind.REALITY, f"evidence_gap:{gap.definition_key}", "unknown_evidence_gap",
                {"metric_key": gap.definition_key, "unit": gap.unit, "source_id": gap.source_id,
                 "source_locator": gap.source_locator, "observed_start": gap.observed_start.isoformat(),
                 "observed_end": gap.observed_end.isoformat(), "effective_at": gap.effective_at.isoformat(),
                 "available_at": gap.available_at.isoformat(), "source_role": gap.source_role,
                 "observation_status": gap.observation_status, "dimensions": dict(gap.dimensions)},
                gap.effective_at, gap.available_at, "frozen_source_manifest",
            ), None,
        )

    def _existing_import(self, fixture: CatlBaselineFixture) -> CatlBaselineImport | None:
        company = self._session.scalar(select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.kind == ResearchObjectKind.COMPANY.value,
            UnderwritingResearchObject.external_key == "CN:300750:COMPANY",
        ))
        if company is None:
            return None
        basis = self._session.scalar(select(UnderwritingHistoricalBasis).where(
            UnderwritingHistoricalBasis.cutoff == fixture.cutoff,
            UnderwritingHistoricalBasis.source_manifest_hash == fixture.source_manifest.manifest_hash,
        ))
        if basis is None:
            return None
        research_version = self._session.scalar(select(UnderwritingResearchVersion).where(
            UnderwritingResearchVersion.object_id == company.id,
            UnderwritingResearchVersion.basis_id == basis.id,
            UnderwritingResearchVersion.version_kind == "catl_economic_model_evidence_only",
        ))
        if research_version is None:
            return None
        industry = self._session.scalar(select(UnderwritingResearchObject).where(UnderwritingResearchObject.external_key == "POWER_BATTERY:GLOBAL"))
        security = self._session.scalar(select(UnderwritingResearchObject).where(UnderwritingResearchObject.external_key == "SZSE:300750"))
        manifest = self._session.scalar(select(UnderwritingSourceManifestVersion).where(UnderwritingSourceManifestVersion.basis_id == basis.id))
        answerability = self._session.scalar(select(UnderwritingAnswerabilityEvaluation).where(
            UnderwritingAnswerabilityEvaluation.object_id == company.id,
            UnderwritingAnswerabilityEvaluation.basis_id == basis.id,
        ))
        if None in (industry, security, manifest, answerability):
            raise ValidationError("existing CATL evidence-only import is incomplete")
        seals: list[object] = []
        for parent in research_version.parent_ids:
            if not isinstance(parent, str):
                continue
            try:
                row = self._session.get(UnderwritingLedgerEntry, UUID(parent))
            except ValueError:
                continue
            if row is not None and row.family_key == CATL_PARENT_SET_FAMILY and row.entry_type == CATL_PARENT_SET_ENTRY_TYPE:
                seals.append(row)
        if len(seals) != 1:
            raise ValidationError("existing CATL evidence-only import is missing its parent-set seal")
        seal = seals[0]
        snapshot_hash, preview_hash = self._semantic_preview_hash(
            fixture,
            parent_set_semantic_hash_value=parent_set_semantic_hash(seal.payload["parent_refs"]),
        )
        if research_version.content_hash != preview_hash:
            raise ValidationError("existing CATL evidence-only import does not match fixture semantics")
        return CatlBaselineImport(
            industry=industry, company=company, security=security, basis=basis, source_manifest=manifest,
            metric_observations=tuple(self._session.scalars(select(UnderwritingMetricObservation).where(UnderwritingMetricObservation.basis_id == basis.id))),
            evidence_gaps=tuple(self._session.scalars(select(UnderwritingLedgerEntry).where(
                UnderwritingLedgerEntry.object_id == industry.id,
                UnderwritingLedgerEntry.basis_id == basis.id,
                UnderwritingLedgerEntry.entry_type == "unknown_evidence_gap",
            ))),
            candidate_mechanisms=tuple(self._session.scalars(select(UnderwritingMechanismPackVersion).where(UnderwritingMechanismPackVersion.object_id == company.id, UnderwritingMechanismPackVersion.basis_id == basis.id))),
            formal_mechanisms=tuple(), industry_state=None, earnings_engine=None, answerability=answerability,
            research_version=research_version, snapshot_hash=snapshot_hash, preview_hash=preview_hash,
        )

    def _append_candidate_belief(self, *, company_id: UUID, basis_id: UUID, mechanism: Mapping[str, object]) -> object:
        key = str(mechanism["key"])
        return self._kernel.append_ledger_entry(
            company_id,
            basis_id,
            LedgerEntryInput(
                LedgerKind.BELIEF,
                f"mechanism:{key}",
                "candidate_mechanism",
                {
                    "mechanism_key": key,
                    "status": "candidate",
                    "source_references": list(mechanism["source_references"]),
                    "review_record": dict(mechanism["review_record"]),
                },
                self._now(),
                self._now(),
                "candidate_not_formal",
            ),
            None,
        )

    def import_fixture(self, fixture: CatlBaselineFixture) -> CatlBaselineImport:
        """Append authentic evidence in a savepoint; never commit the caller transaction."""
        if type(fixture) is not CatlBaselineFixture:
            raise ValidationError("CATL fixture is required")
        validate_full_observation_fixture(fixture)
        validate_frozen_source_manifest(fixture.source_manifest, cutoff=fixture.cutoff)
        validate_frozen_observation_set(
            fixture.frozen_observations,
            source_manifest=fixture.source_manifest,
            cutoff=fixture.cutoff,
        )
        required_gaps = self._required_gaps(fixture)
        if self._now() > fixture.cutoff:
            raise ValidationError("CATL import created_at must not exceed fixture cutoff")
        existing = self._existing_import(fixture)
        if existing is not None:
            return existing

        with self._session.begin_nested():
            industry = self._kernel.add_object(
                ResearchObjectKind.INDUSTRY, "POWER_BATTERY:GLOBAL", "Global power battery"
            )
            company = self._kernel.add_object(
                ResearchObjectKind.COMPANY, "CN:300750:COMPANY", "宁德时代"
            )
            security = self._kernel.add_object(
                ResearchObjectKind.SECURITY, "SZSE:300750", "宁德时代 A 股"
            )
            self._kernel.link_objects(industry.id, company.id, "industry_exposes_company")
            self._kernel.link_objects(company.id, security.id, "company_has_security")
            basis = self._kernel.add_basis(
                HistoricalBasisInput(fixture.cutoff, None, fixture.source_manifest.manifest_hash)
            )
            manifest_payload = self._manifest_payload(fixture)
            manifest = self._research.add_source_manifest(
                manifest_key="catl-2024-economic-basis",
                basis_id=basis.id,
                manifest=manifest_payload,
                manifest_hash=fixture.source_manifest.manifest_hash,
                content_hash=canonical_hash(manifest_payload),
                expected_parent_id=None,
                created_at=self._now(),
            )

            fixture_by_key = {item.definition_key: item for item in fixture.observations}
            definition_ids: dict[str, UUID] = {}
            persisted_observations: list[object] = []
            persisted_gaps: list[object] = []
            parent_ids: list[str] = [str(manifest.id)]
            parent_refs: list[dict[str, object]] = [{
                "reference": str(manifest.id), "artifact_type": "source_manifest",
                "identity": f"{manifest.manifest_key}|{manifest.version}",
                "content_hash": manifest.content_hash,
            }]
            for observation in fixture.frozen_observations:
                evidence = fixture_by_key.get(observation.definition_key)
                if evidence is None or evidence.value != observation.value:
                    raise ValidationError("fixture observation does not match frozen evidence batch")
                definition_payload = self._definition_payload(observation, evidence.source_role)
                definition = self._research.append_metric_definition(
                    metric_key=observation.definition_key,
                    basis_id=basis.id,
                    source_manifest_id=manifest.id,
                    definition=definition_payload,
                    unit=observation.unit,
                    period_semantics="flow",
                    source_role=evidence.source_role,
                    aggregation="none",
                    reconciliation_tolerance=Decimal(definition_payload["reconciliation_tolerance"]),
                    content_hash=canonical_hash(definition_payload),
                    expected_parent_id=None,
                    created_at=self._now(),
                )
                definition_ids[observation.definition_key] = definition.id
                row = self._research.add_metric_observation(
                    metric_key=observation.definition_key,
                    definition_version=observation.definition_version,
                    basis_id=basis.id,
                    definition_id=definition.id,
                    source_manifest_id=manifest.id,
                    source_id=observation.source_id,
                    value=observation.value,
                    unit=observation.unit,
                    observed_start=observation.observed_start,
                    observed_end=observation.observed_end,
                    effective_at=observation.effective_at,
                    available_at=observation.available_at,
                    source_locator=observation.source_locator,
                    dimensions=dict(observation.dimensions),
                    dimension_hash=canonical_hash(dict(observation.dimensions)),
                    content_hash=observation.content_hash,
                    created_at=self._now(),
                )
                persisted_observations.append(row)
                parent_ids.extend((str(definition.id), str(row.id)))
                parent_refs.extend((
                    {
                        "reference": str(definition.id), "artifact_type": "metric_definition",
                        "identity": f"{definition.metric_key}|{definition.version}",
                        "content_hash": definition.content_hash,
                    },
                    {
                        "reference": str(row.id), "artifact_type": "metric_observation",
                        "identity": "|".join((
                            row.metric_key, str(row.definition_version), row.observed_end.isoformat(),
                            row.dimension_hash, row.source_id,
                        )),
                        "content_hash": row.content_hash,
                    },
                ))
                target_object = industry.id if observation.definition_key.startswith("industry.") else company.id
                self._append_reality(object_id=target_object, basis_id=basis.id, observation=observation, evidence=evidence)
            for gap in required_gaps:
                persisted_gaps.append(self._append_evidence_gap(industry_id=industry.id, basis_id=basis.id, gap=gap))

            mechanism_keys = {str(item.get("key")) for item in fixture.mechanisms}
            if mechanism_keys != _MECHANISM_KEYS or len(fixture.mechanisms) != len(_MECHANISM_KEYS):
                raise ValidationError("CATL fixture must contain the six governed mechanism keys")
            candidates: list[object] = []
            for mechanism in sorted(fixture.mechanisms, key=lambda item: str(item["key"])):
                if mechanism.get("status") != "candidate":
                    raise ValidationError("evidence-only CATL import accepts candidate mechanisms only")
                persisted_mechanism = thaw_fixture_json(mechanism)
                assert isinstance(persisted_mechanism, dict)
                source_ids = list(persisted_mechanism.get("source_references", []))
                row = self._research.append_mechanism(
                    mechanism_key=str(persisted_mechanism["key"]),
                    object_id=company.id,
                    basis_id=basis.id,
                    source_manifest_id=manifest.id,
                    status="candidate",
                    payload=persisted_mechanism,
                    source_ids=source_ids,
                    definition_ids=[],
                    content_hash=canonical_hash(persisted_mechanism),
                    expected_parent_id=None,
                    created_at=self._now(),
                )
                candidates.append(row)
                parent_ids.append(str(row.id))
                parent_refs.append({
                    "reference": str(row.id), "artifact_type": "mechanism",
                    "identity": f"{row.mechanism_key}|{row.version}", "content_hash": row.content_hash,
                })
                self._append_candidate_belief(company_id=company.id, basis_id=basis.id, mechanism=persisted_mechanism)

            answerability = self._kernel.record_answerability(
                company.id,
                basis.id,
                tuple([BlockerCode.MISSING_KEY_BASELINE] if required_gaps else []) + (BlockerCode.MECHANISM_UNIDENTIFIED,),
                ("industry.capacity_utilization_price_cost_baseline", "formal_mechanism_review"),
                True,
                EligibleAction.OBSERVE,
                (
                    "collect comparable capacity, utilization, price, and cost evidence",
                    "complete independent mechanism review before formalization",
                ),
                None,
            )
            parent_ids.append(str(answerability.id))
            parent_refs.append({
                "reference": str(answerability.id), "artifact_type": "answerability",
                "identity": "answerability",
                "content_hash": answerability_content_hash(
                    object_id=answerability.object_id, basis_id=answerability.basis_id,
                    version=answerability.version, state=answerability.state,
                    blockers=answerability.blockers, research_debt_keys=answerability.research_debt_keys,
                    resolvable_within_mandate=answerability.resolvable_within_mandate,
                    allowed_action=answerability.allowed_action,
                    resolution_requirements=answerability.resolution_requirements,
                ),
            })
            snapshot_hash = self._semantic_snapshot_hash(fixture)
            semantic_token = f"semantic_snapshot:{snapshot_hash}"
            parent_set_seal = self._kernel.append_ledger_entry(
                company.id, basis.id,
                LedgerEntryInput(
                    LedgerKind.CALIBRATION, CATL_PARENT_SET_FAMILY, CATL_PARENT_SET_ENTRY_TYPE,
                    catl_parent_set_seal_payload(semantic_snapshot_token=semantic_token, refs=parent_refs),
                    fixture.cutoff, fixture.cutoff, "frozen_revision_parent_set",
                ),
                None,
            )
            parent_ids.append(str(parent_set_seal.id))
            _, preview_hash = self._semantic_preview_hash(
                fixture, parent_set_semantic_hash_value=parent_set_semantic_hash(parent_refs),
            )
            parent_ids.append(semantic_token)
            research_version = self._repository.append_research_version(
                object_id=company.id, basis_id=basis.id,
                version_kind="catl_economic_model_evidence_only", content_hash=preview_hash,
                parent_ids=parent_ids, expected_parent_id=None, created_at=self._now(),
            )
            return CatlBaselineImport(
                industry=industry,
                company=company,
                security=security,
                basis=basis,
                source_manifest=manifest,
                metric_observations=tuple(persisted_observations),
                evidence_gaps=tuple(persisted_gaps),
                candidate_mechanisms=tuple(candidates),
                formal_mechanisms=tuple(),
                industry_state=None,
                earnings_engine=None,
                answerability=answerability,
                research_version=research_version,
                snapshot_hash=snapshot_hash,
                preview_hash=preview_hash,
            )
