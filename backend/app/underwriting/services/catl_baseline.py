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
from app.underwriting.fixtures.catl_baseline import CatlBaselineFixture
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash
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


@dataclass(frozen=True, slots=True)
class CatlBaselineImport:
    industry: object
    company: object
    security: object
    basis: object
    source_manifest: object
    metric_observations: tuple[object, ...]
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

    @staticmethod
    def _manifest_payload(fixture: CatlBaselineFixture) -> dict[str, object]:
        return {
            "schema_version": fixture.source_manifest.schema_version,
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

    def _append_reality(self, *, object_id: UUID, basis_id: UUID, observation) -> object:
        return self._kernel.append_ledger_entry(
            object_id,
            basis_id,
            LedgerEntryInput(
                LedgerKind.REALITY,
                f"metric:{observation.definition_key}",
                "reported_observation",
                {
                    "metric_key": observation.definition_key,
                    "observation_id": str(observation.observation_id),
                    "content_hash": observation.content_hash,
                    "source_id": observation.source_id,
                    "value": str(observation.value),
                },
                observation.effective_at,
                observation.available_at,
                "frozen_source_manifest",
            ),
            None,
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
        validate_frozen_source_manifest(fixture.source_manifest, cutoff=fixture.cutoff)
        validate_frozen_observation_set(
            fixture.frozen_observations,
            source_manifest=fixture.source_manifest,
            cutoff=fixture.cutoff,
        )
        if self._now() > fixture.cutoff:
            raise ValidationError("CATL import created_at must not exceed fixture cutoff")

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
            parent_ids: list[str] = [str(manifest.id)]
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
                target_object = industry.id if observation.definition_key.startswith("industry.") else company.id
                self._append_reality(object_id=target_object, basis_id=basis.id, observation=observation)

            mechanism_keys = {str(item.get("key")) for item in fixture.mechanisms}
            if mechanism_keys != _MECHANISM_KEYS or len(fixture.mechanisms) != len(_MECHANISM_KEYS):
                raise ValidationError("CATL fixture must contain the six governed mechanism keys")
            candidates: list[object] = []
            for mechanism in sorted(fixture.mechanisms, key=lambda item: str(item["key"])):
                if mechanism.get("status") != "candidate":
                    raise ValidationError("evidence-only CATL import accepts candidate mechanisms only")
                source_ids = list(mechanism.get("source_references", []))
                row = self._research.append_mechanism(
                    mechanism_key=str(mechanism["key"]),
                    object_id=company.id,
                    basis_id=basis.id,
                    source_manifest_id=manifest.id,
                    status="candidate",
                    payload=mechanism,
                    source_ids=source_ids,
                    definition_ids=[],
                    content_hash=canonical_hash(mechanism),
                    expected_parent_id=None,
                    created_at=self._now(),
                )
                candidates.append(row)
                parent_ids.append(str(row.id))
                self._append_candidate_belief(company_id=company.id, basis_id=basis.id, mechanism=mechanism)

            answerability = self._kernel.record_answerability(
                company.id,
                basis.id,
                (BlockerCode.MISSING_KEY_BASELINE, BlockerCode.MECHANISM_UNIDENTIFIED),
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
            snapshot = self._kernel.snapshot(company.id, basis.id)
            preview_hash = self._kernel.preview_research_version_hash(
                company.id, basis.id, "catl_economic_model_evidence_only", parent_ids
            )
            research_version = self._kernel.publish_research_version(
                company.id,
                basis.id,
                "catl_economic_model_evidence_only",
                parent_ids,
                expected_parent_id=None,
            )
            return CatlBaselineImport(
                industry=industry,
                company=company,
                security=security,
                basis=basis,
                source_manifest=manifest,
                metric_observations=tuple(persisted_observations),
                candidate_mechanisms=tuple(candidates),
                formal_mechanisms=tuple(),
                industry_state=None,
                earnings_engine=None,
                answerability=answerability,
                research_version=research_version,
                snapshot_hash=snapshot.snapshot_hash,
                preview_hash=preview_hash,
            )
