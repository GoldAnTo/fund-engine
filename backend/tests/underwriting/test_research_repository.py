"""Successor-chain and cutoff-read contract for Wave 2 research rows."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.underwriting.domain.types import HistoricalBasisInput
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.persistence.repository import StaleParentError, UnderwritingRepository


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


@pytest.fixture
def kernel(session: Session) -> UnderwritingRepository:
    return UnderwritingRepository(session)


@pytest.fixture
def repository(session: Session) -> UnderwritingResearchRepository:
    return UnderwritingResearchRepository(session)


@pytest.fixture
def company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)


@pytest.fixture
def other_company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "US:GOOGL:COMPANY", "Alphabet", NOW)


@pytest.fixture
def basis(kernel: UnderwritingRepository):
    return kernel.add_basis(HistoricalBasisInput(NOW, NOW, "a" * 64), NOW)


def _manifest(repository: UnderwritingResearchRepository, basis_id: uuid.UUID):
    return repository.add_source_manifest(
        manifest_key="catl-baseline",
        basis_id=basis_id,
        manifest={"sources": []},
        manifest_hash="a" * 64,
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )


def _definition(
    repository: UnderwritingResearchRepository,
    basis_id: uuid.UUID,
    manifest_id: uuid.UUID,
    *,
    metric_key: str = "company.revenue",
    expected_parent_id: uuid.UUID | None = None,
    content_hash: str = "b" * 64,
    created_at: datetime = NOW,
):
    return repository.append_metric_definition(
        metric_key=metric_key,
        basis_id=basis_id,
        source_manifest_id=manifest_id,
        definition={"label": metric_key},
        unit="CNY",
        period_semantics="flow",
        source_role="reported",
        aggregation="sum",
        reconciliation_tolerance=Decimal("1000"),
        content_hash=content_hash,
        expected_parent_id=expected_parent_id,
        created_at=created_at,
    )


def _mechanism_payload(key: str) -> dict[str, object]:
    return {"key": key, "financial_mappings": [{"target": "company.revenue"}]}


def test_mechanism_successor_requires_current_parent(
    repository: UnderwritingResearchRepository, company, basis
) -> None:
    manifest = _manifest(repository, basis.id)
    first = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company.id,
        basis_id=basis.id,
        source_manifest_id=manifest.id,
        status="candidate",
        payload=_mechanism_payload("demand_to_shipments"),
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company.id,
        basis_id=basis.id,
        source_manifest_id=manifest.id,
        status="adapted",
        payload=_mechanism_payload("demand_to_shipments"),
        content_hash="b" * 64,
        expected_parent_id=first.id,
        created_at=NOW,
    )

    with pytest.raises(StaleParentError):
        repository.append_mechanism(
            mechanism_key="demand_to_shipments",
            object_id=company.id,
            basis_id=basis.id,
            source_manifest_id=manifest.id,
            status="adapted",
            payload=_mechanism_payload("demand_to_shipments"),
            content_hash="c" * 64,
            expected_parent_id=first.id,
            created_at=NOW,
        )

    assert (second.version, second.supersedes_id) == (2, first.id)


def test_first_successor_rejects_parent_from_a_different_family(
    repository: UnderwritingResearchRepository, company, basis
) -> None:
    manifest = _manifest(repository, basis.id)
    parent = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company.id,
        basis_id=basis.id,
        source_manifest_id=manifest.id,
        status="candidate",
        payload=_mechanism_payload("demand_to_shipments"),
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )

    with pytest.raises(StaleParentError):
        repository.append_mechanism(
            mechanism_key="cost_to_margin",
            object_id=company.id,
            basis_id=basis.id,
            source_manifest_id=manifest.id,
            status="candidate",
            payload=_mechanism_payload("cost_to_margin"),
            content_hash="b" * 64,
            expected_parent_id=parent.id,
            created_at=NOW,
        )


def test_observation_identity_is_append_only_and_payload_input_is_not_aliased(
    repository: UnderwritingResearchRepository, basis
) -> None:
    manifest = _manifest(repository, basis.id)
    definition = _definition(repository, basis.id, manifest.id)
    dimensions = {"segment": "power_battery"}
    row = repository.add_metric_observation(
        metric_key=definition.metric_key,
        definition_version=definition.version,
        basis_id=basis.id,
        definition_id=definition.id,
        source_manifest_id=manifest.id,
        source_id="catl-2024-ar",
        value=Decimal("253041337000"),
        unit="CNY",
        observed_start=datetime(2024, 1, 1, tzinfo=UTC),
        observed_end=datetime(2024, 12, 31, tzinfo=UTC),
        effective_at=datetime(2024, 12, 31, tzinfo=UTC),
        available_at=NOW,
        source_locator="p18",
        dimensions=dimensions,
        dimension_hash="d" * 64,
        content_hash="e" * 64,
        created_at=NOW,
    )
    dimensions["segment"] = "rewritten"
    assert row.dimensions == {"segment": "power_battery"}

    with pytest.raises(IntegrityError):
        repository.add_metric_observation(
            metric_key=definition.metric_key,
            definition_version=definition.version,
            basis_id=basis.id,
            definition_id=definition.id,
            source_manifest_id=manifest.id,
            source_id="catl-2024-ar",
            value=Decimal("253041337000"),
            unit="CNY",
            observed_start=datetime(2024, 1, 1, tzinfo=UTC),
            observed_end=datetime(2024, 12, 31, tzinfo=UTC),
            effective_at=datetime(2024, 12, 31, tzinfo=UTC),
            available_at=NOW,
            source_locator="p18",
            dimensions={"segment": "power_battery"},
            dimension_hash="d" * 64,
            content_hash="f" * 64,
            created_at=NOW,
        )


def test_effective_cutoff_reads_fold_families_and_sort_stably(
    repository: UnderwritingResearchRepository, company, basis, kernel
) -> None:
    later_basis = kernel.add_basis(
        HistoricalBasisInput(NOW + timedelta(days=2), NOW + timedelta(days=2), "b" * 64),
        NOW + timedelta(days=2),
    )
    manifest = _manifest(repository, basis.id)
    revenue = _definition(repository, basis.id, manifest.id, metric_key="company.revenue")
    later_revenue = _definition(
        repository,
        basis.id,
        manifest.id,
        metric_key="company.revenue",
        expected_parent_id=revenue.id,
        content_hash="c" * 64,
        created_at=NOW + timedelta(days=1),
    )
    assets = _definition(repository, basis.id, manifest.id, metric_key="company.assets", content_hash="d" * 64)
    assert repository.effective_metric_definitions_at(basis.id) == [assets, revenue]
    assert repository.effective_metric_definitions_at(later_basis.id) == [assets, later_revenue]

    repository.add_metric_observation(
        metric_key="company.revenue",
        definition_version=revenue.version,
        basis_id=basis.id,
        definition_id=revenue.id,
        source_manifest_id=manifest.id,
        source_id="catl-2024-ar",
        value=Decimal("1"), unit="CNY",
        observed_start=NOW, observed_end=NOW, effective_at=NOW,
        available_at=NOW + timedelta(days=1), source_locator="p1",
        dimensions={}, dimension_hash="e" * 64, content_hash="e" * 64,
        created_at=NOW,
    )
    assert repository.effective_observations_at(company.id, basis.id) == []
    observations = repository.effective_observations_at(company.id, later_basis.id)
    assert [item.metric_key for item in observations] == ["company.revenue"]


def test_formal_and_latest_reads_are_scoped_to_object_and_basis(
    repository: UnderwritingResearchRepository, company, other_company, basis, kernel
) -> None:
    manifest = _manifest(repository, basis.id)
    formal = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        status="formal", payload=_mechanism_payload("demand_to_shipments"),
        content_hash="a" * 64, expected_parent_id=None, created_at=NOW,
    )
    repository.append_mechanism(
        mechanism_key="other_mechanism",
        object_id=other_company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        status="formal", payload=_mechanism_payload("other_mechanism"),
        content_hash="b" * 64, expected_parent_id=None, created_at=NOW,
    )
    state = repository.append_industry_state(
        object_id=company.id, basis_id=basis.id, mechanism_id=formal.id,
        payload={"utilization": "0.8"}, content_hash="c" * 64,
        expected_parent_id=None, created_at=NOW,
    )
    next_state = repository.append_industry_state(
        object_id=company.id, basis_id=basis.id, mechanism_id=formal.id,
        payload={"utilization": "0.9"}, content_hash="d" * 64,
        expected_parent_id=state.id, created_at=NOW,
    )
    earnings = repository.append_earnings_engine(
        company_id=company.id, basis_id=basis.id, industry_state_id=state.id,
        payload={"revenue": "1"}, content_hash="e" * 64,
        expected_parent_id=None, created_at=NOW,
    )
    later_basis = kernel.add_basis(
        HistoricalBasisInput(NOW + timedelta(days=1), NOW + timedelta(days=1), "f" * 64),
        NOW + timedelta(days=1),
    )
    assert repository.formal_mechanisms_at(company.id, basis.id) == [formal]
    assert repository.latest_industry_state(company.id, basis.id) == next_state
    assert repository.latest_industry_state(company.id, later_basis.id) is None
    assert repository.latest_earnings_engine(company.id, basis.id) == earnings

