from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine, delete, inspect, update
from sqlalchemy.orm import Session

from app.models import Base
from app.models.ledger import IMMUTABLE_TABLES, ImmutableLedgerError
from app.underwriting.domain.types import HistoricalBasisInput
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.repository import (
    StaleParentError,
    UnderwritingRepository,
)


NOW = datetime(2026, 8, 21, tzinfo=UTC)


UNDERWRITING_TABLES = frozenset(
    {
        "uw_research_objects",
        "uw_object_relations",
        "uw_mandate_versions",
        "uw_historical_bases",
        "uw_ledger_entries",
        "uw_research_versions",
        "uw_answerability_evaluations",
    }
)


def test_underwriting_kernel_tables_are_registered_with_metadata_and_immutable_guard():
    assert UNDERWRITING_TABLES <= set(Base.metadata.tables)
    assert UNDERWRITING_TABLES <= IMMUTABLE_TABLES


def test_underwriting_research_objects_reject_update_and_delete():
    engine = create_engine("sqlite://")
    table = UnderwritingResearchObject.__table__
    Base.metadata.create_all(engine, tables=[table])
    try:
        with Session(engine) as session:
            research_object = UnderwritingResearchObject(
                kind="company",
                external_key="acme-001",
                canonical_name="Acme",
                created_at=datetime.now(timezone.utc),
            )
            session.add(research_object)
            session.commit()

            with pytest.raises(ImmutableLedgerError):
                session.execute(
                    update(UnderwritingResearchObject)
                    .where(UnderwritingResearchObject.id == research_object.id)
                    .values(canonical_name="Acme Holdings")
                )
            with pytest.raises(ImmutableLedgerError):
                session.execute(
                    delete(UnderwritingResearchObject).where(
                        UnderwritingResearchObject.id == research_object.id
                    )
                )
    finally:
        Base.metadata.drop_all(engine, tables=[table])


def test_underwriting_kernel_metadata_can_create_and_drop_in_sqlite():
    engine = create_engine("sqlite://")
    tables = [Base.metadata.tables[name] for name in UNDERWRITING_TABLES]

    Base.metadata.create_all(engine, tables=tables)
    assert UNDERWRITING_TABLES <= set(inspect(engine).get_table_names())

    Base.metadata.drop_all(engine, tables=tables)
    assert not UNDERWRITING_TABLES & set(inspect(engine).get_table_names())


@pytest.fixture
def repository(session: Session) -> UnderwritingRepository:
    return UnderwritingRepository(session)


@pytest.fixture
def kernel_company(repository: UnderwritingRepository):
    return repository.add_object(
        kind="company",
        external_key="CN:300750:COMPANY",
        canonical_name="CATL",
        created_at=NOW,
    )


@pytest.fixture
def kernel_security(repository: UnderwritingRepository):
    return repository.add_object(
        kind="security",
        external_key="SZSE:300750",
        canonical_name="CATL A Shares",
        created_at=NOW,
    )


@pytest.fixture
def other_kernel_company(repository: UnderwritingRepository):
    return repository.add_object(
        kind="company",
        external_key="US:GOOGL:COMPANY",
        canonical_name="Alphabet",
        created_at=NOW,
    )


@pytest.fixture
def kernel_basis(repository: UnderwritingRepository):
    return repository.add_basis(
        HistoricalBasisInput(NOW, NOW, "a" * 64), created_at=NOW
    )


def append_claim(
    repository: UnderwritingRepository,
    object_id: uuid.UUID,
    basis_id: uuid.UUID,
    expected_parent_id: uuid.UUID | None,
    content_hash: str,
    *,
    ledger_kind: str = "belief",
    family_key: str = "catl:unit-margin",
    available_at: datetime = NOW,
):
    return repository.append_ledger_entry(
        object_id=object_id,
        basis_id=basis_id,
        ledger_kind=ledger_kind,
        family_key=family_key,
        entry_type="claim",
        payload={"statement": content_hash},
        effective_at=NOW,
        available_at=available_at,
        source_boundary="fixture",
        content_hash=content_hash * 64,
        expected_parent_id=expected_parent_id,
        created_at=NOW,
    )


def test_append_assigns_monotonic_ledger_family_versions(
    repository: UnderwritingRepository, kernel_company, kernel_basis
) -> None:
    first = append_claim(repository, kernel_company.id, kernel_basis.id, None, "a")
    second = append_claim(
        repository, kernel_company.id, kernel_basis.id, first.id, "b"
    )

    assert (first.version, second.version, second.supersedes_id) == (1, 2, first.id)


def test_append_rejects_stale_ledger_expected_parent(
    repository: UnderwritingRepository, kernel_company, kernel_basis
) -> None:
    first = append_claim(repository, kernel_company.id, kernel_basis.id, None, "a")
    append_claim(repository, kernel_company.id, kernel_basis.id, first.id, "b")

    with pytest.raises(StaleParentError):
        append_claim(repository, kernel_company.id, kernel_basis.id, first.id, "c")


@pytest.mark.parametrize(
    ("object_selector", "ledger_kind", "family_key"),
    [
        ("company", "reality", "catl:unit-margin"),
        ("other_company", "belief", "catl:unit-margin"),
        ("company", "belief", "catl:shipment"),
    ],
)
def test_ledger_parent_cannot_cross_its_family(
    repository: UnderwritingRepository,
    kernel_company,
    other_kernel_company,
    kernel_basis,
    object_selector: str,
    ledger_kind: str,
    family_key: str,
) -> None:
    first = append_claim(repository, kernel_company.id, kernel_basis.id, None, "a")
    object_id = (
        kernel_company.id if object_selector == "company" else other_kernel_company.id
    )

    with pytest.raises(StaleParentError):
        append_claim(
            repository,
            object_id,
            kernel_basis.id,
            first.id,
            "b",
            ledger_kind=ledger_kind,
            family_key=family_key,
        )


def test_first_ledger_version_rejects_non_null_parent(
    repository: UnderwritingRepository, kernel_company, kernel_security, kernel_basis
) -> None:
    other_entry = append_claim(
        repository, kernel_security.id, kernel_basis.id, None, "a"
    )

    with pytest.raises(StaleParentError):
        append_claim(
            repository, kernel_company.id, kernel_basis.id, other_entry.id, "b"
        )


def test_effective_entries_at_excludes_future_rows_and_folds_each_family(
    repository: UnderwritingRepository, kernel_company, kernel_basis
) -> None:
    first = append_claim(repository, kernel_company.id, kernel_basis.id, None, "a")
    second = append_claim(
        repository,
        kernel_company.id,
        kernel_basis.id,
        first.id,
        "b",
        available_at=NOW + timedelta(days=1),
    )
    shipment = append_claim(
        repository,
        kernel_company.id,
        kernel_basis.id,
        None,
        "c",
        ledger_kind="reality",
        family_key="catl:shipment",
    )

    assert repository.effective_entries_at(kernel_company.id, NOW) == [first, shipment]
    assert repository.effective_entries_at(kernel_company.id, NOW + timedelta(days=1)) == [
        second,
        shipment,
    ]


def test_mandate_research_and_answerability_successor_chains_increment(
    repository: UnderwritingRepository, kernel_company, kernel_basis
) -> None:
    mandate_first = repository.append_mandate_version(
        mandate_key="personal-cny-5y",
        horizon_years=5,
        base_currency="CNY",
        required_return=Decimal("0.12"),
        permanent_loss_limit=Decimal("0.25"),
        comparison_set=["CSI300"],
        expected_parent_id=None,
        created_at=NOW,
    )
    mandate_second = repository.append_mandate_version(
        mandate_key="personal-cny-5y",
        horizon_years=5,
        base_currency="CNY",
        required_return=Decimal("0.13"),
        permanent_loss_limit=Decimal("0.25"),
        comparison_set=["CSI300"],
        expected_parent_id=mandate_first.id,
        created_at=NOW,
    )
    research_first = repository.append_research_version(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        version_kind="valuation",
        content_hash="b" * 64,
        parent_ids=["source-a"],
        expected_parent_id=None,
        created_at=NOW,
    )
    research_second = repository.append_research_version(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        version_kind="valuation",
        content_hash="c" * 64,
        parent_ids=["source-a", "source-b"],
        expected_parent_id=research_first.id,
        created_at=NOW,
    )
    answerability_first = repository.append_answerability_evaluation(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        state="answerable",
        blockers=[],
        research_debt_keys=[],
        resolvable_within_mandate=True,
        allowed_action="observe",
        resolution_requirements=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    answerability_second = repository.append_answerability_evaluation(
        object_id=kernel_company.id,
        basis_id=kernel_basis.id,
        state="answerable",
        blockers=[],
        research_debt_keys=[],
        resolvable_within_mandate=True,
        allowed_action="observe",
        resolution_requirements=[],
        expected_parent_id=answerability_first.id,
        created_at=NOW,
    )

    assert (mandate_first.version, mandate_second.version) == (1, 2)
    assert mandate_second.supersedes_id == mandate_first.id
    assert (research_first.sequence, research_second.sequence) == (1, 2)
    assert research_second.supersedes_id == research_first.id
    assert (answerability_first.version, answerability_second.version) == (1, 2)
    assert answerability_second.supersedes_id == answerability_first.id
