from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.underwriting.fixtures.product_foundation as product_foundation_fixture
import app.underwriting.persistence.product_repository as product_repository
from app.models.ledger import Base, ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    ProductHistoricalBasisInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    SecurityRightsInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import InvestmentMandateInput, ResearchObjectKind
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingObjectIdentityVersion,
    UnderwritingResearchObjectAlias,
    UnderwritingResearchObjectSearchTerm,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingSecurityRightsVersion,
)
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.market_snapshots import MarketSnapshotService
from app.underwriting.services.product_project import ResearchProjectService


NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
OLD_FROM = datetime(2020, 1, 1, tzinfo=UTC)
OLD_TO = datetime(2022, 1, 1, tzinfo=UTC)
NEW_FROM = datetime(2022, 2, 1, tzinfo=UTC)
A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64


@pytest.fixture
def service(session) -> ResearchProjectService:
    return ResearchProjectService(session, now=lambda: NOW)


def _object(session, kind: str, key: str, name: str) -> UnderwritingResearchObject:
    row = UnderwritingResearchObject(
        kind=kind,
        external_key=key,
        canonical_name=name,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _relation(session, parent: UUID, child: UUID, relation_type: str) -> None:
    session.add(
        UnderwritingObjectRelation(
            parent_id=parent,
            child_id=child,
            relation_type=relation_type,
            created_at=NOW,
        )
    )
    session.flush()


def _identity(
    service: ResearchProjectService,
    row: UnderwritingResearchObject,
    *,
    name: str | None = None,
    symbol: str | None = None,
    exchange: str | None = None,
    share_class: str | None = None,
    currency: str | None = None,
    effective_from: datetime = OLD_FROM,
    effective_to: datetime | None = None,
    expected_parent_id: UUID | None = None,
) -> UnderwritingObjectIdentityVersion:
    if row.kind == ResearchObjectKind.SECURITY.value:
        symbol = symbol or row.external_key.split(".", 1)[0]
        exchange = exchange or "TEST"
        share_class = share_class or "ordinary"
        currency = currency or "CNY"
    return service.append_identity_version(
        object_id=row.id,
        canonical_name=name or row.canonical_name,
        symbol=symbol,
        exchange=exchange,
        share_class=share_class,
        trading_currency=currency,
        effective_from=effective_from,
        effective_to=effective_to,
        expected_parent_id=expected_parent_id,
    )


def _company_group(
    session,
    service: ResearchProjectService,
    *,
    key: str,
    company_name: str,
    securities: tuple[tuple[str, str, str], ...],
) -> tuple[UnderwritingResearchObject, ...]:
    company = _object(session, "company", f"{key}:company", company_name)
    _identity(service, company)
    members = [company]
    for security_key, security_name, symbol in securities:
        security = _object(session, "security", security_key, security_name)
        _identity(service, security, symbol=symbol, currency="USD")
        _relation(session, company.id, security.id, "company_has_security")
        members.append(security)
    return tuple(members)


def _seed_project_graph(session, service: ResearchProjectService) -> dict[str, object]:
    industry = _object(session, "industry", "battery", "Battery Industry")
    unrelated_industry = _object(session, "industry", "cloud", "Cloud Industry")
    catl = _object(session, "company", "company:catl", "CATL")
    catl_security = _object(session, "security", "300750.SZ", "CATL A")
    alphabet = _object(session, "company", "company:alphabet", "Alphabet")
    googl = _object(session, "security", "GOOGL.NASDAQ", "Alphabet Class A")
    goog = _object(session, "security", "GOOG.NASDAQ", "Alphabet Class C")
    other_company = _object(session, "company", "company:other", "Other Company")
    other_security = _object(session, "security", "OTHER.TEST", "Other Security")
    for row in (
        industry,
        unrelated_industry,
        catl,
        catl_security,
        alphabet,
        googl,
        goog,
        other_company,
        other_security,
    ):
        _identity(
            service,
            row,
            currency="USD" if row in (googl, goog, other_security) else None,
        )
    _relation(session, industry.id, catl.id, "industry_exposes_company")
    _relation(session, catl.id, catl_security.id, "company_has_security")
    _relation(session, alphabet.id, googl.id, "company_has_security")
    _relation(session, alphabet.id, goog.id, "company_has_security")
    _relation(session, other_company.id, other_security.id, "company_has_security")
    # A reverse relation must not satisfy project validation.
    _relation(session, other_security.id, catl.id, "company_has_security")
    return locals()


def _project(
    session,
    service: ResearchProjectService,
    *,
    alphabet: bool = False,
):
    graph = _seed_project_graph(session, service)
    if alphabet:
        project = service.create_project(
            primary_company_id=graph["alphabet"].id,
            target_security_ids=(graph["goog"].id, graph["googl"].id),
        )
    else:
        project = service.create_project(
            primary_company_id=graph["catl"].id,
            target_security_ids=(graph["catl_security"].id,),
        )
    return graph, project


def test_identity_is_resolved_at_requested_instant_and_gaps_are_honest(
    session, service
) -> None:
    company = _object(session, "company", "company:rename", "Old Name")
    first = _identity(
        service,
        company,
        name="Old Name",
        effective_from=OLD_FROM,
        effective_to=OLD_TO,
    )
    second = _identity(
        service,
        company,
        name="New Name",
        effective_from=NEW_FROM,
        expected_parent_id=first.id,
    )

    assert (
        service.effective_identity(company.id, datetime(2019, 12, 31, tzinfo=UTC))
        is None
    )
    assert (
        service.effective_identity(company.id, datetime(2021, 1, 1, tzinfo=UTC)).id
        == first.id
    )
    assert (
        service.effective_identity(company.id, datetime(2022, 1, 15, tzinfo=UTC))
        is None
    )
    assert (
        service.effective_identity(company.id, datetime(2023, 1, 1, tzinfo=UTC)).id
        == second.id
    )


def test_identity_successors_reject_overlap_regression_and_stale_parent(
    session, service
) -> None:
    security = _object(session, "security", "OLD.TEST", "Old Security")
    first = _identity(
        service,
        security,
        symbol="OLD",
        effective_from=OLD_FROM,
        effective_to=OLD_TO,
    )
    with pytest.raises(ValidationError, match="overlap|regress"):
        _identity(
            service,
            security,
            symbol="NEW",
            effective_from=datetime(2021, 12, 1, tzinfo=UTC),
            expected_parent_id=first.id,
        )
    second = _identity(
        service,
        security,
        symbol="NEW",
        effective_from=OLD_TO,
        effective_to=NEW_FROM,
        expected_parent_id=first.id,
    )
    with pytest.raises(ConflictError, match="parent|head"):
        _identity(
            service,
            security,
            symbol="STALE",
            effective_from=NEW_FROM,
            expected_parent_id=first.id,
        )
    assert second.version == 2
    with pytest.raises(StaleParentError):
        ProductRepository(session).append_identity_version(
            object_id=security.id,
            canonical_name="Stale",
            symbol="STALE",
            exchange="TEST",
            share_class="ordinary",
            trading_currency="USD",
            effective_from=NEW_FROM,
            effective_to=None,
            content_hash=A64,
            expected_parent_id=first.id,
            created_at=NOW,
        )


def test_version_flush_does_not_misclassify_check_failure_and_keeps_session_usable(
    session,
) -> None:
    company = _object(session, "company", "company:bad-hash", "Bad Hash")
    repository = ProductRepository(session)

    with pytest.raises(IntegrityError) as caught:
        repository.append_identity_version(
            object_id=company.id,
            canonical_name="Bad Hash",
            symbol=None,
            exchange=None,
            share_class=None,
            trading_currency=None,
            effective_from=OLD_FROM,
            effective_to=None,
            content_hash="not-a-sha256",
            expected_parent_id=None,
            created_at=NOW,
        )

    assert not isinstance(caught.value, StaleParentError)
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert session.get(UnderwritingResearchObject, company.id) is company


def test_version_flush_maps_sqlite_cas_unique_and_keeps_session_usable(
    session, service
) -> None:
    security = _object(session, "security", "RACE.TEST", "Race Security")
    parent = _identity(
        service,
        security,
        symbol="RACE",
        effective_from=OLD_FROM,
        effective_to=OLD_TO,
        currency="USD",
    )
    winner = _identity(
        service,
        security,
        symbol="WIN",
        effective_from=OLD_TO,
        expected_parent_id=parent.id,
        currency="USD",
    )
    loser = UnderwritingObjectIdentityVersion(
        object_id=security.id,
        version=winner.version,
        canonical_name="Loser",
        symbol="LOSE",
        exchange="TEST",
        share_class="ordinary",
        trading_currency="USD",
        effective_from=OLD_TO,
        effective_to=None,
        supersedes_id=parent.id,
        content_hash=A64,
        created_at=NOW,
    )

    with pytest.raises(StaleParentError):
        ProductRepository(session)._flush_version(loser)

    assert session.get(UnderwritingObjectIdentityVersion, winner.id) is winner
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 2
    )


def test_open_ended_identity_head_can_be_superseded_by_a_later_version(
    session, service
) -> None:
    security = _object(session, "security", "OPEN.TEST", "Open Identity")
    first = _identity(
        service,
        security,
        symbol="OPEN",
        effective_from=OLD_FROM,
        effective_to=None,
        currency="USD",
    )
    switch_at = datetime(2021, 6, 1, tzinfo=UTC)
    second = _identity(
        service,
        security,
        name="Renamed Identity",
        symbol="RENAMED",
        effective_from=switch_at,
        effective_to=None,
        expected_parent_id=first.id,
        currency="USD",
    )

    assert (
        service.effective_identity(
            security.id, datetime(2021, 5, 31, 23, 59, tzinfo=UTC)
        ).id
        == first.id
    )
    assert service.effective_identity(security.id, switch_at).id == second.id
    assert service.effective_identity(security.id, NOW).id == second.id

    with pytest.raises(ValidationError, match="advance"):
        _identity(
            service,
            security,
            symbol="SAME",
            effective_from=switch_at,
            expected_parent_id=second.id,
            currency="USD",
        )
    with pytest.raises(ValidationError, match="advance"):
        _identity(
            service,
            security,
            symbol="REGRESS",
            effective_from=datetime(2021, 1, 1, tzinfo=UTC),
            expected_parent_id=second.id,
            currency="USD",
        )
    with pytest.raises(ConflictError, match="parent|head"):
        _identity(
            service,
            security,
            symbol="STALE",
            effective_from=datetime(2022, 1, 1, tzinfo=UTC),
            expected_parent_id=first.id,
            currency="USD",
        )


def test_finite_identity_successor_does_not_resurrect_open_ended_parent(
    session, service
) -> None:
    security = _object(session, "security", "stable-key", "Legacy Identity")
    first = _identity(
        service,
        security,
        name="Legacy Identity",
        symbol="OLD",
        effective_from=OLD_FROM,
        effective_to=None,
        currency="USD",
    )
    switch_at = datetime(2021, 1, 1, tzinfo=UTC)
    expires_at = datetime(2022, 1, 1, tzinfo=UTC)
    second = _identity(
        service,
        security,
        name="Replacement Identity",
        symbol="NEW",
        effective_from=switch_at,
        effective_to=expires_at,
        expected_parent_id=first.id,
        currency="USD",
    )

    assert (
        service.effective_identity(security.id, datetime(2020, 12, 31, tzinfo=UTC)).id
        == first.id
    )
    assert service.effective_identity(security.id, switch_at).id == second.id
    assert service.effective_identity(security.id, expires_at) is None
    assert service.effective_identity(security.id, NOW) is None
    assert service.search_objects("stable-key", expires_at, 10) == ()
    assert service.search_objects("legacy", NOW, 10) == ()


@pytest.mark.parametrize("kind", ["company", "industry"])
def test_non_security_identity_rejects_security_fields(session, service, kind) -> None:
    row = _object(session, kind, f"{kind}:one", kind.title())
    with pytest.raises(ValidationError, match="security fields"):
        _identity(service, row, symbol="FAKE")


def test_security_identity_requires_complete_supported_trading_identity(
    session, service
) -> None:
    row = _object(session, "security", "one.test", "One")
    with pytest.raises(ValidationError, match="symbol"):
        service.append_identity_version(
            object_id=row.id,
            canonical_name="One",
            symbol=None,
            exchange="TEST",
            share_class="ordinary",
            trading_currency="EUR",
            effective_from=OLD_FROM,
            effective_to=None,
            expected_parent_id=None,
        )
    with pytest.raises(ValidationError, match="supported"):
        _identity(service, row, currency="EUR")


def test_search_uses_historical_name_and_symbol_with_stable_typed_results(
    session, service
) -> None:
    industry = _object(session, "industry", "alpha-industry", "Alpha Materials")
    company = _object(session, "company", "alpha-company", "Alpha Holdings")
    security = _object(session, "security", "ALPHA.TEST", "Alpha Share")
    _identity(service, industry, effective_to=None)
    _identity(service, company, name="Former Alpha", effective_to=OLD_TO)
    company_new = _identity(
        service,
        company,
        name="Renamed Holdings",
        effective_from=OLD_TO,
        expected_parent_id=service.effective_identity(
            company.id, datetime(2021, 1, 1, tzinfo=UTC)
        ).id,
    )
    old_security = _identity(
        service,
        security,
        symbol="ALPHA",
        effective_to=OLD_TO,
        currency="USD",
    )
    _identity(
        service,
        security,
        symbol="BETA",
        effective_from=OLD_TO,
        expected_parent_id=old_security.id,
        currency="USD",
    )

    historical = service.search_objects(" alpha ", datetime(2021, 1, 1, tzinfo=UTC), 20)
    assert [result.kind for result in historical] == [
        ResearchObjectKind.SECURITY,
        ResearchObjectKind.COMPANY,
        ResearchObjectKind.INDUSTRY,
    ]
    assert [result.object_id for result in historical] == [
        security.id,
        company.id,
        industry.id,
    ]
    assert (
        next(result for result in historical if result.object_id == security.id).symbol
        == "ALPHA"
    )
    assert service.search_objects("former", datetime(2023, 1, 1, tzinfo=UTC), 20) == ()
    current_external_key_matches = service.search_objects(
        "ALPHA", datetime(2023, 1, 1, tzinfo=UTC), 20
    )
    assert (
        next(
            result
            for result in current_external_key_matches
            if result.object_id == security.id
        ).symbol
        == "BETA"
    )
    current = service.search_objects("renamed", datetime(2023, 1, 1, tzinfo=UTC), 20)
    assert current[0].identity_version_id == company_new.id
    assert (
        service.search_objects("alpha-industry", NOW, 20)[0].kind
        is ResearchObjectKind.INDUSTRY
    )


@pytest.mark.parametrize(
    ("query", "limit"), [("", 10), ("   ", 10), ("x", 0), ("x", 101)]
)
def test_search_validates_query_limit_and_as_of(service, query, limit) -> None:
    with pytest.raises(ValidationError):
        service.search_objects(query, NOW, limit)
    with pytest.raises(ValidationError, match="timezone"):
        service.search_objects("x", NOW.replace(tzinfo=None), 10)


def test_project_validation_rejects_industry_wrong_kinds_relations_and_duplicates(
    session, service
) -> None:
    graph = _seed_project_graph(session, service)
    failures = (
        (graph["industry"].id, (graph["catl_security"].id,)),
        (uuid4(), (graph["catl_security"].id,)),
        (graph["catl"].id, (graph["catl"].id,)),
        (graph["catl"].id, (graph["other_security"].id,)),
        (graph["catl"].id, (graph["catl_security"].id, graph["catl_security"].id)),
        (graph["catl"].id, ()),
    )
    for company_id, security_ids in failures:
        before = session.scalar(
            select(func.count()).select_from(UnderwritingResearchProject)
        )
        with pytest.raises(ValidationError):
            service.create_project(company_id, security_ids)
        assert (
            session.scalar(
                select(func.count()).select_from(UnderwritingResearchProject)
            )
            == before
        )
        assert (
            session.scalar(
                select(func.count()).select_from(UnderwritingResearchProjectSecurity)
            )
            == 0
        )


def test_valid_single_and_multi_security_projects_read_and_list_deterministically(
    session, service
) -> None:
    graph = _seed_project_graph(session, service)
    catl_project = service.create_project(
        graph["catl"].id, (graph["catl_security"].id,)
    )
    alphabet_project = service.create_project(
        graph["alphabet"].id, (graph["googl"].id, graph["goog"].id)
    )
    second_alphabet = service.create_project(graph["alphabet"].id, (graph["goog"].id,))

    assert service.project(catl_project.id) == catl_project
    assert alphabet_project.target_security_ids == tuple(
        sorted((graph["googl"].id, graph["goog"].id), key=str)
    )
    assert second_alphabet.primary_company_id == alphabet_project.primary_company_id
    projects = service.list_projects(limit=10)
    assert projects == tuple(
        sorted(
            projects,
            key=lambda item: (item.created_at, str(item.id)),
            reverse=True,
        )
    )
    assert service.list_projects(limit=2) == projects[:2]


def test_list_projects_batches_memberships_without_project_lookup(
    session, service, monkeypatch
) -> None:
    graph = _seed_project_graph(session, service)
    service.create_project(graph["catl"].id, (graph["catl_security"].id,))
    service.create_project(graph["alphabet"].id, (graph["googl"].id, graph["goog"].id))
    service.create_project(graph["alphabet"].id, (graph["goog"].id,))

    def reject_project_lookup(*_args, **_kwargs):
        raise AssertionError("list_projects must batch memberships")

    monkeypatch.setattr(service._repository, "project", reject_project_lookup)

    projects = service.list_projects(limit=2)

    assert len(projects) == 2
    assert all(project.target_security_ids for project in projects)


def test_project_creation_is_caller_transactional_and_rollback_removes_links(
    session, service
) -> None:
    graph = _seed_project_graph(session, service)
    session.commit()
    service.create_project(graph["catl"].id, (graph["catl_security"].id,))
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == 1
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchProjectSecurity)
        )
        == 1
    )
    session.rollback()
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchProjectSecurity)
        )
        == 0
    )


def _mandate(base_currency: str = "USD") -> InvestmentMandateInput:
    return InvestmentMandateInput(
        mandate_key="caller-key-is-not-authoritative",
        horizon_years=5,
        base_currency=base_currency,
        required_return=Decimal("0.12"),
        permanent_loss_limit=Decimal("0.30"),
        comparison_set=("cash", "broad-index"),
    )


def test_product_mandate_chain_hashes_every_field_and_has_no_market_cutoff(
    session, service
) -> None:
    _graph, project = _project(session, service)
    first = service.append_product_mandate(
        project_id=project.id,
        value=_mandate(),
        benchmark_key="broad-index",
        required_excess_return=Decimal("0.03"),
        effective_at=OLD_FROM,
        expires_at=OLD_TO,
        expected_parent_id=None,
    )
    expected = canonical_hash(
        {
            "schema_version": "product.investment-mandate.v1",
            "project_id": str(project.id),
            "mandate_key": f"product.project:{project.id}",
            "horizon_years": 5,
            "base_currency": "USD",
            "required_return": "0.12000000",
            "permanent_loss_limit": "0.30000000",
            "comparison_set": ["broad-index", "cash"],
            "benchmark_key": "broad-index",
            "required_excess_return": "0.03000000",
            "effective_at": OLD_FROM.isoformat(),
            "expires_at": OLD_TO.isoformat(),
        }
    )
    assert first.content_hash == expected
    assert not hasattr(first, "cutoff") and not hasattr(first, "price_as_of")
    second = service.append_product_mandate(
        project_id=project.id,
        value=_mandate("CNY"),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=NEW_FROM,
        expires_at=None,
        expected_parent_id=first.id,
    )
    assert (second.version, second.supersedes_id) == (2, first.id)
    with pytest.raises(ConflictError):
        service.append_product_mandate(
            project_id=project.id,
            value=_mandate(),
            benchmark_key=None,
            required_excess_return=None,
            effective_at=NOW,
            expires_at=None,
            expected_parent_id=first.id,
        )


def test_product_mandate_reads_exact_id_with_project_ownership(
    session, service
) -> None:
    graph = _seed_project_graph(session, service)
    project = service.create_project(graph["catl"].id, (graph["catl_security"].id,))
    other_project = service.create_project(graph["alphabet"].id, (graph["goog"].id,))
    first = service.append_product_mandate(
        project_id=project.id,
        value=_mandate(),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=OLD_FROM,
        expires_at=None,
        expected_parent_id=None,
    )
    second = service.append_product_mandate(
        project_id=project.id,
        value=_mandate("CNY"),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=OLD_TO,
        expires_at=None,
        expected_parent_id=first.id,
    )

    assert service.product_mandate(project.id, first.id) is first
    assert service.product_mandate(project.id, second.id) is second
    assert service.product_mandate(other_project.id, first.id) is None
    assert service.product_mandate(project.id, uuid4()) is None


def test_product_mandate_normalizes_numeric_scale_before_hashing(
    session, service
) -> None:
    graph, project = _project(session, service)
    session.commit()
    first = service.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            mandate_key="ignored",
            horizon_years=5,
            base_currency="USD",
            required_return=Decimal("0.12"),
            permanent_loss_limit=Decimal("0.3"),
            comparison_set=("cash",),
        ),
        benchmark_key="index",
        required_excess_return=Decimal("0.03"),
        effective_at=OLD_FROM,
        expires_at=None,
        expected_parent_id=None,
    )
    first_hash = first.content_hash
    session.rollback()

    equivalent = service.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            mandate_key="ignored-again",
            horizon_years=5,
            base_currency="USD",
            required_return=Decimal("0.12000000"),
            permanent_loss_limit=Decimal("0.300000000"),
            comparison_set=("cash",),
        ),
        benchmark_key="index",
        required_excess_return=Decimal("0.030000000"),
        effective_at=OLD_FROM,
        expires_at=None,
        expected_parent_id=None,
    )

    assert equivalent.content_hash == first_hash
    session.commit()
    session.expire_all()
    persisted = service.product_mandate(project.id, equivalent.id)
    assert persisted is not None
    assert persisted.required_return.as_tuple().exponent == -8
    assert persisted.permanent_loss_limit.as_tuple().exponent == -8
    assert persisted.required_excess_return.as_tuple().exponent == -8
    persisted_hash = canonical_hash(
        {
            "schema_version": "product.investment-mandate.v1",
            "project_id": str(project.id),
            "mandate_key": persisted.mandate_key,
            "horizon_years": persisted.horizon_years,
            "base_currency": persisted.base_currency,
            "required_return": format(persisted.required_return, ".8f"),
            "permanent_loss_limit": format(persisted.permanent_loss_limit, ".8f"),
            "comparison_set": persisted.comparison_set,
            "benchmark_key": persisted.benchmark_key,
            "required_excess_return": format(persisted.required_excess_return, ".8f"),
            "effective_at": persisted.effective_at.replace(tzinfo=UTC).isoformat(),
            "expires_at": None,
        }
    )
    assert persisted.content_hash == persisted_hash


def test_product_mandate_canonicalizes_signed_zero_before_hash_and_reload(
    session, service
) -> None:
    _graph, project = _project(session, service)
    session.commit()
    hashes: list[str] = []
    canonical_id = None
    for raw_zero in ("-0", "-0.00000000", "0"):
        row = service.append_product_mandate(
            project_id=project.id,
            value=InvestmentMandateInput(
                mandate_key="ignored",
                horizon_years=5,
                base_currency="USD",
                required_return=Decimal(raw_zero),
                permanent_loss_limit=Decimal(raw_zero),
                comparison_set=("cash",),
            ),
            benchmark_key="index",
            required_excess_return=Decimal(raw_zero),
            effective_at=OLD_FROM,
            expires_at=None,
            expected_parent_id=None,
        )
        assert not row.required_return.is_signed()
        assert not row.permanent_loss_limit.is_signed()
        assert not row.required_excess_return.is_signed()
        hashes.append(row.content_hash)
        if raw_zero == "0":
            canonical_id = row.id
        else:
            session.rollback()

    assert len(set(hashes)) == 1
    assert canonical_id is not None
    session.commit()
    session.expire_all()
    persisted = service.product_mandate(project.id, canonical_id)
    assert persisted is not None
    assert format(persisted.required_return, ".8f") == "0.00000000"
    assert format(persisted.permanent_loss_limit, ".8f") == "0.00000000"
    assert format(persisted.required_excess_return, ".8f") == "0.00000000"
    assert persisted.content_hash == canonical_hash(
        {
            "schema_version": "product.investment-mandate.v1",
            "project_id": str(project.id),
            "mandate_key": persisted.mandate_key,
            "horizon_years": persisted.horizon_years,
            "base_currency": persisted.base_currency,
            "required_return": "0.00000000",
            "permanent_loss_limit": "0.00000000",
            "comparison_set": persisted.comparison_set,
            "benchmark_key": persisted.benchmark_key,
            "required_excess_return": "0.00000000",
            "effective_at": persisted.effective_at.replace(tzinfo=UTC).isoformat(),
            "expires_at": None,
        }
    )


def test_product_mandate_rejects_meaningful_precision_beyond_storage_scale(
    session, service
) -> None:
    _graph, project = _project(session, service)
    with pytest.raises(ValidationError, match="8 decimal places"):
        service.append_product_mandate(
            project_id=project.id,
            value=InvestmentMandateInput(
                mandate_key="ignored",
                horizon_years=5,
                base_currency="USD",
                required_return=Decimal("0.123456789"),
                permanent_loss_limit=Decimal("0.30"),
                comparison_set=("cash",),
            ),
            benchmark_key=None,
            required_excess_return=None,
            effective_at=OLD_FROM,
            expires_at=None,
            expected_parent_id=None,
        )


@pytest.mark.parametrize(
    ("benchmark", "excess", "effective", "expires", "currency"),
    [
        ("index", None, OLD_FROM, None, "USD"),
        (None, Decimal("0.01"), OLD_FROM, None, "USD"),
        ("index", Decimal("1"), OLD_FROM, None, "USD"),
        (None, None, OLD_TO, OLD_FROM, "USD"),
        (None, None, OLD_FROM.replace(tzinfo=None), None, "USD"),
        (None, None, OLD_FROM, None, "EUR"),
    ],
)
def test_product_mandate_rejects_invalid_pair_interval_excess_and_currency(
    session, service, benchmark, excess, effective, expires, currency
) -> None:
    _graph, project = _project(session, service)
    with pytest.raises(ValidationError):
        service.append_product_mandate(
            project_id=project.id,
            value=_mandate(currency),
            benchmark_key=benchmark,
            required_excess_return=excess,
            effective_at=effective,
            expires_at=expires,
            expected_parent_id=None,
        )


def test_scope_uses_project_identity_security_subset_and_controlled_industry_relation(
    session, service
) -> None:
    graph, project = _project(session, service, alphabet=True)
    _relation(
        session, graph["industry"].id, graph["alphabet"].id, "industry_exposes_company"
    )
    scope_value = ResearchScopeInput(
        primary_company_id=graph["alphabet"].id,
        target_security_ids=(graph["googl"].id,),
        industry_ids=(graph["industry"].id,),
        covered_segments=("Cloud", "Ads"),
        user_focus=" durability ",
        exclusions=("Trading", "Timing"),
    )
    scope = service.append_scope(project.id, scope_value, expected_parent_id=None)
    assert scope.payload == {
        "primary_company_id": str(graph["alphabet"].id),
        "target_security_ids": [str(graph["googl"].id)],
        "industry_ids": [str(graph["industry"].id)],
        "covered_segments": ["Ads", "Cloud"],
        "user_focus": "durability",
        "exclusions": ["Timing", "Trading"],
    }
    assert scope.content_hash == canonical_hash(
        {
            "schema_version": "product.research-scope.v1",
            "project_id": str(project.id),
            "scope": scope.payload,
        }
    )

    reordered = ResearchScopeInput(
        primary_company_id=graph["alphabet"].id,
        target_security_ids=(graph["googl"].id,),
        industry_ids=(graph["industry"].id,),
        covered_segments=("Ads", "Cloud"),
        user_focus="durability",
        exclusions=("Timing", "Trading"),
    )
    successor = service.append_scope(project.id, reordered, expected_parent_id=scope.id)
    assert successor.content_hash == scope.content_hash
    with pytest.raises(ConflictError):
        service.append_scope(project.id, reordered, expected_parent_id=scope.id)


def test_scope_rejects_cross_project_identity_nonmember_and_unrelated_industry(
    session, service
) -> None:
    graph, project = _project(session, service, alphabet=True)
    invalid_values = (
        ResearchScopeInput(
            graph["catl"].id,
            (graph["goog"].id,),
            (),
            (),
            None,
            (),
        ),
        ResearchScopeInput(
            graph["alphabet"].id,
            (graph["catl_security"].id,),
            (),
            (),
            None,
            (),
        ),
        ResearchScopeInput(
            graph["alphabet"].id,
            (graph["goog"].id,),
            (graph["unrelated_industry"].id,),
            (),
            None,
            (),
        ),
        ResearchScopeInput(
            graph["alphabet"].id,
            (graph["goog"].id,),
            (graph["catl"].id,),
            (),
            None,
            (),
        ),
    )
    for value in invalid_values:
        with pytest.raises(ValidationError):
            service.append_scope(project.id, value, expected_parent_id=None)
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchScopeVersion)
        )
        == 0
    )


def _deterministic_agenda(
    scope_id: UUID, items: tuple[str, ...]
) -> ResearchAgendaInput:
    return ResearchAgendaInput(
        scope_id=scope_id,
        items=items,
        generator=AgendaGeneratorInput(
            method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
            template_key="foundation",
            template_version="v1",
            model_name=None,
            prompt_template_version=None,
            input_summary_hash=A64,
            output_hash=agenda_items_hash(items),
        ),
    )


def test_agenda_preserves_deterministic_and_ai_provenance_and_hashes_it(
    session, service
) -> None:
    graph, project = _project(session, service)
    scope = service.append_scope(
        project.id,
        ResearchScopeInput(
            graph["catl"].id,
            (graph["catl_security"].id,),
            (graph["industry"].id,),
            ("Core",),
            None,
            (),
        ),
        expected_parent_id=None,
    )
    first_value = _deterministic_agenda(scope.id, ("Identity", "Risks"))
    first = service.append_agenda(project.id, first_value, expected_parent_id=None)
    assert first.payload == {"items": ["Identity", "Risks"]}
    assert first.generator_provenance == {
        "method": "deterministic_template",
        "template_key": "foundation",
        "template_version": "v1",
        "model_name": None,
        "prompt_template_version": None,
        "input_summary_hash": A64,
        "output_hash": agenda_items_hash(first_value.items),
    }
    assert first.content_hash == canonical_hash(
        {
            "schema_version": "product.research-agenda.v1",
            "project_id": str(project.id),
            "scope_id": str(scope.id),
            "items": ["Identity", "Risks"],
            "generator": first.generator_provenance,
        }
    )

    ai_items = ("Competition",)
    ai_value = ResearchAgendaInput(
        scope.id,
        ai_items,
        AgendaGeneratorInput(
            AgendaGenerationMethod.AI_GENERATED,
            None,
            None,
            "model-test",
            "prompt-v2",
            A64,
            agenda_items_hash(ai_items),
        ),
    )
    second = service.append_agenda(project.id, ai_value, expected_parent_id=first.id)
    assert second.generator_provenance["model_name"] == "model-test"
    assert second.generator_provenance["input_summary_hash"] == A64
    with pytest.raises(ConflictError):
        service.append_agenda(project.id, ai_value, expected_parent_id=first.id)


def test_agenda_rejects_scope_from_another_project(session, service) -> None:
    graph = _seed_project_graph(session, service)
    catl = service.create_project(graph["catl"].id, (graph["catl_security"].id,))
    alphabet = service.create_project(graph["alphabet"].id, (graph["goog"].id,))
    scope = service.append_scope(
        catl.id,
        ResearchScopeInput(
            graph["catl"].id,
            (graph["catl_security"].id,),
            (graph["industry"].id,),
            (),
            None,
            (),
        ),
        None,
    )
    with pytest.raises(ValidationError, match="same project"):
        service.append_agenda(
            alphabet.id,
            _deterministic_agenda(scope.id, ("Cross project",)),
            None,
        )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchAgendaVersion)
        )
        == 0
    )


def test_scope_and_agenda_reads_are_exact_and_project_scoped(session, service) -> None:
    graph = _seed_project_graph(session, service)
    project = service.create_project(graph["catl"].id, (graph["catl_security"].id,))
    other_project = service.create_project(graph["alphabet"].id, (graph["goog"].id,))
    first_scope = service.append_scope(
        project.id,
        ResearchScopeInput(
            graph["catl"].id,
            (graph["catl_security"].id,),
            (graph["industry"].id,),
            ("Core",),
            None,
            (),
        ),
        None,
    )
    second_scope = service.append_scope(
        project.id,
        ResearchScopeInput(
            graph["catl"].id,
            (graph["catl_security"].id,),
            (graph["industry"].id,),
            ("Core", "Risks"),
            None,
            (),
        ),
        first_scope.id,
    )
    first_agenda = service.append_agenda(
        project.id,
        _deterministic_agenda(first_scope.id, ("First",)),
        None,
    )
    second_agenda = service.append_agenda(
        project.id,
        _deterministic_agenda(second_scope.id, ("Second",)),
        first_agenda.id,
    )

    assert service.scope(project.id, first_scope.id) is first_scope
    assert service.scope(project.id, second_scope.id) is second_scope
    assert service.scope(other_project.id, first_scope.id) is None
    assert service.scope(project.id, uuid4()) is None
    assert service.agenda(project.id, first_agenda.id) is first_agenda
    assert service.agenda(project.id, second_agenda.id) is second_agenda
    assert service.agenda(other_project.id, first_agenda.id) is None
    assert service.agenda(project.id, uuid4()) is None


@pytest.mark.parametrize(
    "cutoff",
    [datetime(1999, 1, 1, tzinfo=UTC), datetime(2099, 1, 1, tzinfo=UTC)],
)
def test_product_historical_basis_has_exact_boundary_hashes_and_no_price(
    session, service, cutoff
) -> None:
    value = ProductHistoricalBasisInput(cutoff, A64, B64, C64)
    basis = service.create_historical_basis(value)
    assert basis.cutoff == cutoff.replace(tzinfo=None) or basis.cutoff == cutoff
    assert basis.price_as_of is None
    assert basis.source_manifest_hash == A64
    assert basis.definition_bundle_hash == B64
    assert basis.parser_bundle_hash == C64
    assert basis.boundary_schema_version == "product.historical-basis.v1"
    assert basis.content_hash == canonical_hash(
        {
            "schema_version": "product.historical-basis.v1",
            "cutoff_at": cutoff.isoformat(),
            "source_manifest_hash": A64,
            "definition_bundle_hash": B64,
            "parser_bundle_hash": C64,
        }
    )
    assert session.get(UnderwritingHistoricalBasis, basis.id) is basis
    assert service.historical_basis(basis.id) is basis
    assert service.historical_basis(uuid4()) is None


def test_historical_basis_read_rejects_legacy_and_non_product_rows(
    session, service
) -> None:
    legacy_priced = UnderwritingHistoricalBasis(
        cutoff=NOW,
        price_as_of=NOW,
        source_manifest_hash=A64,
        definition_bundle_hash=None,
        parser_bundle_hash=None,
        boundary_schema_version=None,
        content_hash=None,
        created_at=NOW,
    )
    schema_less = UnderwritingHistoricalBasis(
        cutoff=NOW,
        price_as_of=None,
        source_manifest_hash=A64,
        definition_bundle_hash=B64,
        parser_bundle_hash=C64,
        boundary_schema_version=None,
        content_hash=A64,
        created_at=NOW,
    )
    product_schema_with_price = UnderwritingHistoricalBasis(
        cutoff=NOW,
        price_as_of=NOW,
        source_manifest_hash=A64,
        definition_bundle_hash=B64,
        parser_bundle_hash=C64,
        boundary_schema_version="product.historical-basis.v1",
        content_hash=A64,
        created_at=NOW,
    )
    session.add_all((legacy_priced, schema_less, product_schema_with_price))
    session.flush()

    assert service.historical_basis(legacy_priced.id) is None
    assert service.historical_basis(schema_less.id) is None
    assert service.historical_basis(product_schema_with_price.id) is None


def test_product_project_modules_stay_isolated_and_only_borrow_hash_helper() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "underwriting"
    service_source = (root / "services" / "product_project.py").read_text()
    repository_source = (root / "persistence" / "product_repository.py").read_text()
    combined = service_source + repository_source
    assert "event_research" not in combined
    assert "fund" not in combined.casefold()
    assert "tenant" not in combined.casefold()
    assert "simulated" not in combined.casefold()

    tree = ast.parse(service_source)
    kernel_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.underwriting.services.kernel"
    ]
    assert len(kernel_imports) == 1
    assert [alias.name for alias in kernel_imports[0].names] == ["canonical_hash"]


def test_foundation_fixture_loads_exact_temporal_identities_relations_and_rights(
    session,
) -> None:
    fixture = load_product_foundation_fixture()
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(fixture)
    projects = ResearchProjectService(session, now=lambda: NOW)

    assert fixture.schema_version == "product.foundation-identities.v1"
    assert {
        (alias.object_key, alias.alias, alias.locale) for alias in fixture.aliases
    } == {
        ("US:ALPHABET:COMPANY", "Google", "en"),
        ("US:ALPHABET:COMPANY", "谷歌", "zh-CN"),
    }
    assert set(loaded.objects) == {
        "CN:300750:COMPANY",
        "US:ALPHABET:COMPANY",
        "GLOBAL:INTERNET_SERVICES:INDUSTRY",
        "SZSE:300750",
        "NASDAQ:GOOGL",
        "NASDAQ:GOOG",
    }
    assert loaded.objects["CN:300750:COMPANY"].kind == "company"
    assert loaded.objects["US:ALPHABET:COMPANY"].kind == "company"
    assert loaded.objects["GLOBAL:INTERNET_SERVICES:INDUSTRY"].kind == "industry"
    assert loaded.objects["SZSE:300750"].kind == "security"
    assert loaded.objects["US:ALPHABET:COMPANY"].canonical_name == "Alphabet Inc."
    assert loaded.identities["US:ALPHABET:COMPANY"].canonical_name == "Alphabet Inc."
    assert {
        (row.alias, row.normalized_alias, row.locale) for row in loaded.aliases.values()
    } == {
        ("Google", "google", "en"),
        ("谷歌", "谷歌", "zh-CN"),
    }

    before_listing = datetime(2018, 6, 10, 15, 59, 59, tzinfo=UTC)
    at_listing = datetime(2018, 6, 10, 16, tzinfo=UTC)
    assert (
        projects.effective_identity(loaded.objects["SZSE:300750"].id, before_listing)
        is None
    )
    catl_identity = projects.effective_identity(
        loaded.objects["SZSE:300750"].id, at_listing
    )
    assert (catl_identity.symbol, catl_identity.exchange) == ("300750", "SZSE")
    assert (catl_identity.share_class, catl_identity.trading_currency) == ("A", "CNY")
    assert loaded.identities["NASDAQ:GOOGL"].share_class == "Class A"
    assert loaded.identities["NASDAQ:GOOG"].share_class == "Class C"
    assert loaded.identities["NASDAQ:GOOGL"].trading_currency == "USD"
    assert loaded.identities["NASDAQ:GOOG"].trading_currency == "USD"
    assert {item.external_key for item in projects.search_objects("300750", NOW)} == {
        "CN:300750:COMPANY",
        "SZSE:300750",
    }
    assert {item.external_key for item in projects.search_objects("CATL", NOW)} == {
        "CN:300750:COMPANY",
        "SZSE:300750",
    }
    relations = set(
        session.execute(
            select(
                UnderwritingObjectRelation.parent_id,
                UnderwritingObjectRelation.child_id,
                UnderwritingObjectRelation.relation_type,
            )
        )
    )
    assert relations == {
        (
            loaded.objects["CN:300750:COMPANY"].id,
            loaded.objects["SZSE:300750"].id,
            "company_has_security",
        ),
        (
            loaded.objects["US:ALPHABET:COMPANY"].id,
            loaded.objects["NASDAQ:GOOGL"].id,
            "company_has_security",
        ),
        (
            loaded.objects["US:ALPHABET:COMPANY"].id,
            loaded.objects["NASDAQ:GOOG"].id,
            "company_has_security",
        ),
        (
            loaded.objects["GLOBAL:INTERNET_SERVICES:INDUSTRY"].id,
            loaded.objects["US:ALPHABET:COMPANY"].id,
            "industry_exposes_company",
        ),
    }
    assert len({rights.id for rights in loaded.rights.values()}) == 3
    assert loaded.rights["NASDAQ:GOOGL"].id != loaded.rights["NASDAQ:GOOG"].id
    assert loaded.rights["NASDAQ:GOOGL"].votes_per_unit == Decimal("1.0000000000")
    assert loaded.rights["NASDAQ:GOOG"].votes_per_unit == Decimal("0E-10")


@pytest.mark.parametrize(
    ("query", "expected_external_keys"),
    [
        (
            "Alphabet",
            {
                "US:ALPHABET:COMPANY",
                "NASDAQ:GOOGL",
                "NASDAQ:GOOG",
            },
        ),
        (
            "Google",
            {
                "US:ALPHABET:COMPANY",
                "NASDAQ:GOOGL",
                "NASDAQ:GOOG",
            },
        ),
        (
            "谷歌",
            {
                "US:ALPHABET:COMPANY",
                "NASDAQ:GOOGL",
                "NASDAQ:GOOG",
            },
        ),
        (
            "GOOGL",
            {
                "US:ALPHABET:COMPANY",
                "NASDAQ:GOOGL",
                "NASDAQ:GOOG",
            },
        ),
        (
            "宁德时代公司",
            {
                "CN:300750:COMPANY",
                "SZSE:300750",
            },
        ),
    ],
)
def test_foundation_company_discovery_resolves_complete_company_groups(
    session,
    query,
    expected_external_keys,
) -> None:
    ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    projects = ResearchProjectService(session, now=lambda: NOW)

    assert {
        item.external_key for item in projects.search_objects(query, NOW, 10)
    } == expected_external_keys


def test_company_discovery_keeps_groups_complete_stable_and_within_limit(
    session,
) -> None:
    ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    projects = ResearchProjectService(session, now=lambda: NOW)

    assert projects.search_objects("Alphabet", NOW, 2) == ()
    assert projects.search_objects("GOOGL", NOW, 1) == ()
    assert [
        item.external_key for item in projects.search_objects("Google", NOW, 3)
    ] == [
        "US:ALPHABET:COMPANY",
        "NASDAQ:GOOGL",
        "NASDAQ:GOOG",
    ]


def test_company_suffix_fallback_does_not_retry_a_raw_match_omitted_by_limit(
    session, service
) -> None:
    _company_group(
        session,
        service,
        key="alpha",
        company_name="Alpha公司",
        securities=(
            ("ALPHA-A", "Alpha Class A", "ALPHA-A"),
            ("ALPHA-B", "Alpha Class B", "ALPHA-B"),
        ),
    )
    industry = _object(session, "industry", "alpha-mining", "Alpha Mining")
    _identity(service, industry)

    assert service.search_objects("Alpha公司", NOW, 2) == ()


def test_exact_company_match_wins_before_substring_company_group(
    session, service
) -> None:
    exact = _company_group(
        session,
        service,
        key="target-exact",
        company_name="Target",
        securities=(
            ("TARGET-EXACT-A", "Exact Class A", "TGA"),
            ("TARGET-EXACT-B", "Exact Class B", "TGB"),
        ),
    )
    _company_group(
        session,
        service,
        key="target-substring",
        company_name="A Target reseller",
        securities=(
            ("TARGET-SUB-A", "Reseller Class A", "RSA"),
            ("TARGET-SUB-B", "Reseller Class B", "RSB"),
        ),
    )

    assert {
        result.object_id for result in service.search_objects("Target", NOW, 3)
    } == {member.id for member in exact}


def test_oversized_exact_company_group_is_not_replaced_by_substring_results(
    session, service
) -> None:
    _company_group(
        session,
        service,
        key="oversized-exact",
        company_name="Target",
        securities=(
            ("OVERSIZED-A", "Oversized Class A", "OVA"),
            ("OVERSIZED-B", "Oversized Class B", "OVB"),
            ("OVERSIZED-C", "Oversized Class C", "OVC"),
        ),
    )
    industry = _object(session, "industry", "target-reseller", "A Target reseller")
    _identity(service, industry)

    assert service.search_objects("Target", NOW, 3) == ()


def test_exact_security_symbol_wins_before_substring_security_group(
    session, service
) -> None:
    exact = _company_group(
        session,
        service,
        key="symbol-exact",
        company_name="Zeta Exact Holdings",
        securities=(
            ("SYMBOL-EXACT-A", "Zeta Class A", "TARGET"),
            ("SYMBOL-EXACT-B", "Zeta Class B", "ZTB"),
        ),
    )
    _company_group(
        session,
        service,
        key="symbol-substring",
        company_name="A Holdings",
        securities=(
            ("SYMBOL-SUB-A", "A Target reseller share", "OTHER"),
            ("SYMBOL-SUB-B", "A Other share", "OTHR"),
        ),
    )

    assert {
        result.object_id for result in service.search_objects("Target", NOW, 3)
    } == {member.id for member in exact}


def test_exact_security_alias_wins_before_substring_company_group(
    session, service
) -> None:
    exact = _company_group(
        session,
        service,
        key="alias-exact",
        company_name="Zeta Alias Holdings",
        securities=(
            ("ALIAS-EXACT-A", "Zeta Alias Class A", "ZAA"),
            ("ALIAS-EXACT-B", "Zeta Alias Class B", "ZAB"),
        ),
    )
    session.add(
        UnderwritingResearchObjectAlias(
            object_id=exact[1].id,
            alias="Target",
            normalized_alias="target",
            locale="en",
            created_at=NOW,
        )
    )
    _company_group(
        session,
        service,
        key="alias-substring",
        company_name="A Target reseller",
        securities=(
            ("ALIAS-SUB-A", "Alias Reseller A", "ARA"),
            ("ALIAS-SUB-B", "Alias Reseller B", "ARB"),
        ),
    )
    session.flush()

    assert {
        result.object_id for result in service.search_objects("Target", NOW, 3)
    } == {member.id for member in exact}


def test_exact_security_external_key_wins_before_substring_company_group(
    session, service
) -> None:
    exact = _company_group(
        session,
        service,
        key="external-exact",
        company_name="Zeta External Holdings",
        securities=(
            ("Target", "Zeta External Class A", "ZEA"),
            ("EXTERNAL-EXACT-B", "Zeta External Class B", "ZEB"),
        ),
    )
    _company_group(
        session,
        service,
        key="external-substring",
        company_name="A Target reseller",
        securities=(
            ("EXTERNAL-SUB-A", "External Reseller A", "ERA"),
            ("EXTERNAL-SUB-B", "External Reseller B", "ERB"),
        ),
    )

    assert {
        result.object_id for result in service.search_objects("Target", NOW, 3)
    } == {member.id for member in exact}


def test_unicode_alias_lower_queries_are_consistent(session, service) -> None:
    group = _company_group(
        session,
        service,
        key="DE:TEST",
        company_name="Unrelated Holdings",
        securities=(("DE:TEST:A", "Unrelated Class A", "DTA"),),
    )
    session.add(
        UnderwritingResearchObjectAlias(
            object_id=group[0].id,
            alias="Straße",
            normalized_alias="straße",
            locale="de",
            created_at=NOW,
        )
    )
    session.flush()

    expected_ids = {member.id for member in group}
    for query in ("Straße", "straße"):
        assert {
            result.object_id for result in service.search_objects(query, NOW, 3)
        } == expected_ids
    assert service.search_objects("STRASSE", NOW, 3) == ()


def test_uppercase_accented_alias_is_normalized_and_discoverable(
    session, service
) -> None:
    group = _company_group(
        session,
        service,
        key="FR:ALIAS",
        company_name="Unrelated Holdings",
        securities=(("FR:ALIAS:A", "Unrelated Class A", "FRA"),),
    )
    session.add(
        UnderwritingResearchObjectAlias(
            object_id=group[0].id,
            alias="ÉCOLE",
            normalized_alias="école",
            locale="fr",
            created_at=NOW,
        )
    )
    session.flush()

    assert {result.object_id for result in service.search_objects("école", NOW, 3)} == {
        member.id for member in group
    }


def test_selected_core_nfd_alias_fails_closed(session, service) -> None:
    company = _object(session, "company", "FR:CORE:COMPANY", "Unrelated Holdings")
    _identity(service, company)
    session.execute(
        UnderwritingResearchObjectAlias.__table__.insert(),
        {
            "object_id": company.id,
            "alias": "E\N{COMBINING ACUTE ACCENT}cole",
            "normalized_alias": "e\N{COMBINING ACUTE ACCENT}cole",
            "locale": "fr",
            "created_at": NOW,
        },
    )

    with pytest.raises(RuntimeError, match="normalization"):
        service.search_objects("cole", NOW, 3)


@pytest.mark.parametrize(
    ("source", "query"),
    [
        ("canonical_name", "école"),
        ("external_key", "école"),
        ("symbol", "école"),
    ],
)
def test_unicode_non_alias_search_terms_are_discoverable(
    session, service, source, query
) -> None:
    company_name = (
        "ÉCOLE Holdings" if source == "canonical_name" else "Unrelated Holdings"
    )
    company_key = (
        "ÉCOLE:COMPANY" if source == "external_key" else f"FR:{source}:COMPANY"
    )
    symbol = "ÉCOLE" if source == "symbol" else "FRA"
    group = _company_group(
        session,
        service,
        key=company_key.removesuffix(":COMPANY"),
        company_name=company_name,
        securities=((f"FR:{source}:A", "Unrelated Class A", symbol),),
    )

    assert {result.object_id for result in service.search_objects(query, NOW, 3)} == {
        member.id for member in group
    }


def test_unicode_identity_search_terms_follow_as_of_successors(
    session, service
) -> None:
    company = _object(session, "company", "FR:TEMPORAL:COMPANY", "Unrelated")
    first = _identity(
        service,
        company,
        name="ÉCOLE Legacy",
        effective_from=OLD_FROM,
        effective_to=OLD_TO,
    )
    _identity(
        service,
        company,
        name="Unrelated Current",
        effective_from=OLD_TO,
        expected_parent_id=first.id,
    )

    historical = service.search_objects("école", datetime(2021, 1, 1, tzinfo=UTC), 3)
    assert {result.object_id for result in historical} == {company.id}
    assert service.search_objects("école", NOW, 3) == ()


def test_object_search_candidates_are_exact_first_and_bounded_in_sql(
    session, service
) -> None:
    candidate_statements: list[tuple[str, str]] = []

    def capture_candidate_sql(
        _connection, _cursor, statement, _parameters, context, _executemany
    ) -> None:
        stage = context.execution_options.get("uw_search_candidate_stage")
        if stage is not None:
            candidate_statements.append((stage, statement))

    for index in range(300):
        industry = _object(
            session,
            "industry",
            f"bounded:{index:03d}",
            f"Needle Industry {index:03d}",
        )
        _identity(service, industry)
    session.flush()

    event.listen(session.bind, "before_cursor_execute", capture_candidate_sql)
    try:
        outcome = ProductRepository(session).search_objects("needle", NOW, 100)
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_candidate_sql)

    assert product_repository._OBJECT_SEARCH_CANDIDATE_CAP == 256
    assert outcome.candidate_count == 256
    assert [stage for stage, _statement in candidate_statements] == [
        "exact",
        "substring",
    ]
    assert all(" LIMIT " in statement.upper() for _, statement in candidate_statements)
    exact_sql = next(
        statement for stage, statement in candidate_statements if stage == "exact"
    )
    assert "normalized_digest" in exact_sql
    assert "normalized_value" in exact_sql


def test_long_canonical_name_uses_fixed_digest_for_exact_discovery(
    session, service
) -> None:
    long_name = "".join(
        hashlib.sha256(str(index).encode("ascii")).hexdigest() for index in range(256)
    )
    company = _object(session, "company", "LONG:TEST:COMPANY", "Unrelated")
    _identity(service, company, name=long_name)

    results = service.search_objects(long_name, NOW, 1)

    assert [result.object_id for result in results] == [company.id]


def test_company_discovery_expands_only_identities_effective_as_of(session) -> None:
    ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    projects = ResearchProjectService(session, now=lambda: NOW)

    before_alphabet = datetime(2015, 10, 2, 3, 59, 59, tzinfo=UTC)
    assert projects.search_objects("Google", before_alphabet, 10) == ()

    before_catl_listing = datetime(2018, 6, 10, 15, 59, 59, tzinfo=UTC)
    assert [
        item.external_key
        for item in projects.search_objects("300750", before_catl_listing, 10)
    ] == ["CN:300750:COMPANY"]


def test_industry_matches_are_never_expanded_as_company_groups(
    session, service
) -> None:
    industry = _object(session, "industry", "alpha-industry", "Alpha Industry")
    security = _object(session, "security", "ALPHA", "Alpha Security")
    _identity(service, industry)
    _identity(service, security, currency="USD")
    _relation(session, industry.id, security.id, "company_has_security")

    assert [
        item.external_key for item in service.search_objects("alpha-industry", NOW, 10)
    ] == ["alpha-industry"]


def test_industry_companies_expands_only_direct_company_groups_and_honors_limit(
    session, service
) -> None:
    imported = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    industry = imported.objects["GLOBAL:INTERNET_SERVICES:INDUSTRY"]

    assert [
        item.external_key for item in service.industry_companies(industry.id, NOW, 3)
    ] == ["US:ALPHABET:COMPANY", "NASDAQ:GOOGL", "NASDAQ:GOOG"]
    assert service.industry_companies(industry.id, NOW, 2) == ()


def test_industry_companies_rejects_wrong_parent_and_corrupt_direct_child(
    session, service
) -> None:
    industry = _object(session, "industry", "industry:target", "Target Industry")
    company = _object(session, "company", "company:target", "Target Company")
    security = _object(session, "security", "security:target", "Target Security")
    for row in (industry, company, security):
        _identity(service, row, currency="USD" if row is security else None)

    assert service.industry_companies(industry.id, NOW, 10) == ()
    with pytest.raises(ValidationError, match="Industry"):
        service.industry_companies(company.id, NOW, 10)
    assert service.industry_companies(uuid4(), NOW, 10) is None

    _relation(session, industry.id, security.id, "industry_exposes_company")
    with pytest.raises(ValidationError, match="Company"):
        service.industry_companies(industry.id, NOW, 10)


def test_industry_companies_skips_an_oversized_group_to_return_a_later_group(
    session, service
) -> None:
    industry = _object(session, "industry", "industry:browse", "Browse Industry")
    _identity(service, industry)
    oversized = _company_group(
        session,
        service,
        key="a-oversized",
        company_name="A Oversized",
        securities=(
            ("A1.TEST", "A One", "A1"),
            ("A2.TEST", "A Two", "A2"),
        ),
    )[0]
    later = _company_group(
        session,
        service,
        key="b-fits",
        company_name="B Fits",
        securities=(),
    )[0]
    _relation(session, industry.id, oversized.id, "industry_exposes_company")
    _relation(session, industry.id, later.id, "industry_exposes_company")

    assert [
        item.external_key for item in service.industry_companies(industry.id, NOW, 2)
    ] == [later.external_key]


def test_foundation_fixture_is_idempotent_content_checked_and_contains_no_research_facts(
    session, tmp_path
) -> None:
    fixture = load_product_foundation_fixture()
    service = ProductFoundationFixtureService(session, now=lambda: NOW)
    first = service.load(fixture)
    second = service.load(load_product_foundation_fixture())

    assert {key: row.id for key, row in first.objects.items()} == {
        key: row.id for key, row in second.objects.items()
    }
    assert {key: row.id for key, row in first.identities.items()} == {
        key: row.id for key, row in second.identities.items()
    }
    assert {key: row.id for key, row in first.rights.items()} == {
        key: row.id for key, row in second.rights.items()
    }
    assert {key: row.id for key, row in first.aliases.items()} == {
        key: row.id for key, row in second.aliases.items()
    }
    assert (
        second.identities["CN:300750:COMPANY"].canonical_name
        == "宁德时代新能源科技股份有限公司（CATL）"
    )
    assert second.rights["SZSE:300750"].id == first.rights["SZSE:300750"].id
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchObject))
        == 6
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 6
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 4
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 3
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchObjectAlias)
        )
        == 2
    )
    forbidden = {
        "financials",
        "prices",
        "valuations",
        "assessments",
        "research",
        "generated_research",
    }
    assert forbidden.isdisjoint(fixture.raw.keys())

    manifest_path = tmp_path / "manifest.json"
    original = fixture.manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(original.replace("Class C", "Class B"), encoding="utf-8")
    with pytest.raises(ValidationError, match="content hash"):
        load_product_foundation_fixture(manifest_path)

    shifted_manifest = json.loads(original)
    shifted_manifest["rights"][0]["effective_from"] = "2018-06-12"
    shifted_manifest["content_hash"] = canonical_hash(
        {key: value for key, value in shifted_manifest.items() if key != "content_hash"}
    )
    manifest_path.write_text(
        json.dumps(shifted_manifest, ensure_ascii=False), encoding="utf-8"
    )
    shifted = load_product_foundation_fixture(manifest_path)
    assert shifted.rights[0].effective_from == datetime(2018, 6, 11, 16, tzinfo=UTC)

    forged = load_product_foundation_fixture()
    object.__setattr__(forged, "content_hash", "b" * 64)
    with pytest.raises(ValidationError, match="content hash"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(forged)


def _write_checksum_validated_manifest(tmp_path, mutate) -> Path:
    raw = json.loads(load_product_foundation_fixture().manifest_path.read_text("utf-8"))
    mutate(raw)
    raw["content_hash"] = canonical_hash(
        {key: value for key, value in raw.items() if key != "content_hash"}
    )
    path = tmp_path / f"custom-{uuid4()}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_foundation_manifest_rejects_duplicate_json_keys(tmp_path) -> None:
    original = load_product_foundation_fixture().manifest_path.read_text("utf-8")
    duplicate = original.replace(
        '"schema_version": "product.foundation-identities.v1",',
        '"schema_version": "product.foundation-identities.v1",\n'
        '  "schema_version": "product.foundation-identities.v1",',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate key"):
        load_product_foundation_fixture(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["companies"][1].__setitem__(
                "canonical_name", " Alphabet Inc."
            ),
            "leading or trailing whitespace",
        ),
        (
            lambda raw: raw["companies"][1].__setitem__(
                "canonical_name", "Alphabe\N{COMBINING ACUTE ACCENT}t Inc."
            ),
            "NFC",
        ),
        (
            lambda raw: raw["rights"][0].__setitem__("effective_from", "2018-6-11"),
            "YYYY-MM-DD",
        ),
    ],
)
def test_custom_foundation_manifest_rejects_noncanonical_text_and_dates(
    tmp_path, mutate, message
) -> None:
    path = _write_checksum_validated_manifest(tmp_path, mutate)

    with pytest.raises(ValidationError, match=message):
        load_product_foundation_fixture(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["aliases"][0].__setitem__("unexpected", True),
            "invalid fields",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("object_key", "UNKNOWN"),
            "unknown object",
        ),
        (
            lambda raw: raw["aliases"].append(
                {
                    "object_key": "US:ALPHABET:COMPANY",
                    "alias": "GOOGLE",
                    "locale": "en-US",
                }
            ),
            "duplicate alias",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("alias", " Google"),
            "leading or trailing whitespace",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__(
                "alias", "Googl\N{COMBINING ACUTE ACCENT}e"
            ),
            "NFC",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("alias", "x" * 161),
            "too long",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("alias", "İ" * 160),
            "normalized alias is too long",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("locale", "english_US"),
            "locale",
        ),
        (
            lambda raw: raw["aliases"][0].__setitem__("locale", "zh-Hans-CN-toolong"),
            "locale",
        ),
    ],
)
def test_custom_foundation_manifest_strictly_validates_aliases(
    tmp_path, mutate, message
) -> None:
    path = _write_checksum_validated_manifest(tmp_path, mutate)

    with pytest.raises(ValidationError, match=message):
        load_product_foundation_fixture(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw.__setitem__("industries", []),
            "industries must be a nonempty array",
        ),
        (
            lambda raw: raw["industries"][0].__setitem__(
                "external_key", "GLOBAL:INTERNET_SERVICES:COMPANY"
            ),
            "wrong object kind",
        ),
        (
            lambda raw: raw["industry_company_relations"].append(
                raw["industry_company_relations"][0].copy()
            ),
            "duplicate industry company relation",
        ),
        (
            lambda raw: raw["industry_company_relations"][0].__setitem__(
                "industry_key", "US:ALPHABET:COMPANY"
            ),
            "wrong object kind",
        ),
        (
            lambda raw: raw["industry_company_relations"][0].__setitem__(
                "company_key", "GLOBAL:INTERNET_SERVICES:INDUSTRY"
            ),
            "wrong object kind",
        ),
    ],
)
def test_custom_foundation_manifest_strictly_validates_industry_relations(
    tmp_path, mutate, message
) -> None:
    path = _write_checksum_validated_manifest(tmp_path, mutate)

    with pytest.raises(ValidationError, match=message):
        load_product_foundation_fixture(path)


def test_bundled_foundation_manifest_requires_a_fixed_trusted_content_digest(
    tmp_path, monkeypatch
) -> None:
    path = _write_checksum_validated_manifest(
        tmp_path,
        lambda raw: raw["companies"][1].__setitem__(
            "canonical_name", "Alphabet Holdings Inc."
        ),
    )
    path.rename(tmp_path / "manifest.json")
    bundled_path = tmp_path / "manifest.json"
    monkeypatch.setattr(product_foundation_fixture, "_ROOT", tmp_path)

    with pytest.raises(ValidationError, match="trusted content digest"):
        load_product_foundation_fixture()

    custom = load_product_foundation_fixture(bundled_path)
    assert custom.companies[1].canonical_name == "Alphabet Holdings Inc."


def test_foundation_fixture_two_real_sqlite_sessions_converge_on_identical_roots(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'product-foundation-concurrency.sqlite'}",
        future=True,
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    barrier = Barrier(2)

    def load_and_commit():
        worker = sessions()
        try:
            barrier.wait(timeout=5)
            loaded = ProductFoundationFixtureService(worker, now=lambda: NOW).load(
                load_product_foundation_fixture()
            )
            ids = (
                {key: row.id for key, row in loaded.objects.items()},
                {key: row.id for key, row in loaded.identities.items()},
                {key: row.id for key, row in loaded.aliases.items()},
                {key: row.id for key, row in loaded.rights.items()},
            )
            worker.commit()
            return ids
        except Exception as exc:  # Assertions inspect leaked concurrency failures.
            worker.rollback()
            return exc
        finally:
            worker.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(load_and_commit) for _ in range(2)]
            results = tuple(future.result(timeout=20) for future in futures)
        assert all(not isinstance(result, Exception) for result in results), results
        assert results[0] == results[1]
        observer = sessions()
        try:
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingResearchObject)
                )
                == 6
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingObjectIdentityVersion)
                )
                == 6
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingObjectRelation)
                )
                == 4
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingSecurityRightsVersion)
                )
                == 3
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingResearchObjectAlias)
                )
                == 2
            )
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_foundation_fixture_load_never_commits_the_caller_transaction(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'product-foundation-rollback.sqlite'}", future=True
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    worker = sessions()
    try:
        ProductFoundationFixtureService(worker, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )
        assert (
            worker.scalar(select(func.count()).select_from(UnderwritingResearchObject))
            == 6
        )
        worker.rollback()
        observer = sessions()
        try:
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingResearchObject)
                )
                == 0
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingSecurityRightsVersion)
                )
                == 0
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingResearchObjectAlias)
                )
                == 0
            )
        finally:
            observer.close()
    finally:
        worker.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_foundation_fixture_postgres_lock_is_transaction_scoped() -> None:
    statement, lock_id = (
        ProductFoundationFixtureService.postgresql_load_lock_statement()
    )
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "pg_advisory_xact_lock" in compiled
    assert str(lock_id) in compiled


def test_foundation_fixture_adopts_existing_catl_objects_and_reuses_rights(
    session,
) -> None:
    company = _object(
        session,
        "company",
        "CN:300750:COMPANY",
        "宁德时代",
    )
    security = _object(
        session,
        "security",
        "SZSE:300750",
        "宁德时代（CATL）A 股",
    )
    _relation(session, company.id, security.id, "company_has_security")

    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    repeated = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )

    assert loaded.objects["CN:300750:COMPANY"].id == company.id
    assert loaded.objects["SZSE:300750"].id == security.id
    assert (
        loaded.identities["CN:300750:COMPANY"].canonical_name
        == "宁德时代新能源科技股份有限公司（CATL）"
    )
    assert loaded.rights["SZSE:300750"].id == repeated.rights["SZSE:300750"].id


def test_foundation_fixture_reuses_roots_after_legal_identity_and_rights_successors(
    session,
) -> None:
    fixture = load_product_foundation_fixture()
    service = ProductFoundationFixtureService(session, now=lambda: NOW)
    first = service.load(fixture)
    security = first.objects["SZSE:300750"]
    identity_root = first.identities["SZSE:300750"]
    rights_root = first.rights["SZSE:300750"]
    successor_at = datetime(2030, 1, 1, tzinfo=UTC)
    identity_successor = ResearchProjectService(
        session, now=lambda: NOW
    ).append_identity_version(
        object_id=security.id,
        canonical_name="宁德时代 A 股（后继名称）",
        symbol="300750",
        exchange="SZSE",
        share_class="A",
        trading_currency="CNY",
        effective_from=successor_at,
        effective_to=None,
        expected_parent_id=identity_root.id,
    )
    rights_successor = MarketSnapshotService(
        session, now=lambda: NOW
    ).freeze_security_rights(
        SecurityRightsInput(
            security_identity_id=security.id,
            economic_units=Decimal("1"),
            votes_per_unit=Decimal("1"),
            conversion_ratio=Decimal("1"),
            adr_ratio=Decimal("1"),
            dividend_rights_per_unit=Decimal("1"),
            effective_from=successor_at,
            effective_to=None,
            source_id="successor-test",
            raw_hash=B64,
        ),
        expected_parent_id=rights_root.id,
    )
    before = {
        "identities": session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        ),
        "rights": session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        ),
    }

    repeated = service.load(load_product_foundation_fixture())

    assert repeated.identities["SZSE:300750"].id == identity_root.id
    assert repeated.rights["SZSE:300750"].id == rights_root.id
    assert (
        ProductRepository(session).identity_head(security.id).id
        == identity_successor.id
    )
    assert ProductRepository(session).rights_head(security.id).id == rights_successor.id
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == before["identities"]
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == before["rights"]
    )


def test_foundation_fixture_repairs_only_missing_search_projection_rows(
    session,
) -> None:
    fixture = load_product_foundation_fixture()
    service = ProductFoundationFixtureService(session, now=lambda: NOW)
    loaded = service.load(fixture)
    company = loaded.objects["US:ALPHABET:COMPANY"]
    identity = loaded.identities["US:ALPHABET:COMPANY"]
    session.connection().exec_driver_sql(
        "DELETE FROM uw_research_object_search_terms "
        "WHERE object_id = ? AND identity_version_id = ? "
        "AND term_kind = 'canonical_name'",
        (company.id.hex, identity.id.hex),
    )
    session.expire_all()

    service.load(load_product_foundation_fixture())

    assert (
        session.execute(
            select(
                Base.metadata.tables["uw_research_object_search_terms"].c.raw_value
            ).where(
                Base.metadata.tables["uw_research_object_search_terms"].c.object_id
                == company.id,
                Base.metadata.tables[
                    "uw_research_object_search_terms"
                ].c.identity_version_id
                == identity.id,
                Base.metadata.tables["uw_research_object_search_terms"].c.term_kind
                == "canonical_name",
            )
        ).scalar_one()
        == "Alphabet Inc."
    )


@pytest.mark.parametrize(
    "corruption",
    ["wrong", "extra", "digest_mismatch", "digest_collision"],
)
def test_foundation_fixture_rejects_inconsistent_search_projection_atomically(
    session, corruption
) -> None:
    fixture = load_product_foundation_fixture()
    service = ProductFoundationFixtureService(session, now=lambda: NOW)
    loaded = service.load(fixture)
    missing_company = loaded.objects["CN:300750:COMPANY"]
    missing_identity = loaded.identities["CN:300750:COMPANY"]
    company = loaded.objects["US:ALPHABET:COMPANY"]
    identity = loaded.identities["US:ALPHABET:COMPANY"]
    session.connection().exec_driver_sql(
        "DELETE FROM uw_research_object_search_terms "
        "WHERE object_id = ? AND identity_version_id = ? "
        "AND term_kind = 'canonical_name'",
        (missing_company.id.hex, missing_identity.id.hex),
    )
    if corruption == "wrong":
        session.connection().exec_driver_sql(
            "UPDATE uw_research_object_search_terms "
            "SET raw_value = 'Unrelated', normalized_value = 'unrelated', "
            "normalized_digest = ? "
            "WHERE identity_version_id = ? AND term_kind = 'canonical_name'",
            (hashlib.sha256(b"unrelated").hexdigest(), identity.id.hex),
        )
    elif corruption == "extra":
        session.connection().exec_driver_sql(
            "INSERT INTO uw_research_object_search_terms "
            "(id, object_id, identity_version_id, term_kind, raw_value, "
            "normalized_value, normalized_digest, created_at) "
            "VALUES (?, ?, ?, 'symbol', 'EXTRA', 'extra', ?, ?)",
            (
                uuid4().hex,
                company.id.hex,
                identity.id.hex,
                hashlib.sha256(b"extra").hexdigest(),
                NOW,
            ),
        )
    elif corruption == "digest_mismatch":
        session.connection().exec_driver_sql(
            "UPDATE uw_research_object_search_terms "
            "SET normalized_digest = ? "
            "WHERE identity_version_id = ? AND term_kind = 'canonical_name'",
            (hashlib.sha256(b"unrelated").hexdigest(), identity.id.hex),
        )
    else:
        session.connection().exec_driver_sql(
            "UPDATE uw_research_object_search_terms "
            "SET raw_value = 'Unrelated', normalized_value = 'unrelated' "
            "WHERE identity_version_id = ? AND term_kind = 'canonical_name'",
            (identity.id.hex,),
        )
    session.expire_all()

    before = session.scalar(
        select(func.count()).select_from(UnderwritingResearchObject)
    )
    with pytest.raises(ValidationError, match="search term conflict"):
        service.load(load_product_foundation_fixture())
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchObject))
        == before
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(UnderwritingResearchObjectSearchTerm)
            .where(
                UnderwritingResearchObjectSearchTerm.object_id == missing_company.id,
                UnderwritingResearchObjectSearchTerm.identity_version_id
                == missing_identity.id,
                UnderwritingResearchObjectSearchTerm.term_kind == "canonical_name",
            )
        )
        == 0
    )


def test_foundation_fixture_rejects_a_non_contiguous_successor_chain(session) -> None:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    root = loaded.identities["SZSE:300750"]
    session.add(
        UnderwritingObjectIdentityVersion(
            object_id=loaded.objects["SZSE:300750"].id,
            version=3,
            canonical_name="Malformed successor",
            symbol="300750",
            exchange="SZSE",
            share_class="A",
            trading_currency="CNY",
            effective_from=datetime(2030, 1, 1, tzinfo=UTC),
            effective_to=None,
            supersedes_id=root.id,
            content_hash=A64,
            created_at=NOW,
        )
    )
    session.flush()

    with pytest.raises(ValidationError, match="identity chain conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )


def test_foundation_fixture_rejects_a_tampered_fixture_root(session) -> None:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_object_identity_versions SET canonical_name = ? WHERE id = ?",
        ("Tampered root", loaded.identities["SZSE:300750"].id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="identity chain conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )


def test_foundation_fixture_conflict_fails_closed_and_rolls_back_partial_import(
    session,
) -> None:
    session.add(
        UnderwritingResearchObject(
            kind="industry",
            external_key="US:ALPHABET:COMPANY",
            canonical_name="Conflicting row",
            created_at=NOW,
        )
    )
    session.flush()

    with pytest.raises(ValidationError, match="conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert (
        session.scalar(
            select(func.count())
            .select_from(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.external_key == "CN:300750:COMPANY")
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )


def test_foundation_fixture_alias_conflict_fails_closed_without_partial_import(
    session,
) -> None:
    alphabet = _object(
        session,
        "company",
        "US:ALPHABET:COMPANY",
        "Alphabet Inc.",
    )
    existing_alias = UnderwritingResearchObjectAlias(
        object_id=alphabet.id,
        alias="Alphabet Holdings",
        normalized_alias="alphabet holdings",
        locale="en",
        created_at=NOW,
    )
    session.add(existing_alias)
    session.flush()

    with pytest.raises(ValidationError, match="alias conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert {
        row.external_key for row in session.scalars(select(UnderwritingResearchObject))
    } == {"US:ALPHABET:COMPANY"}
    assert tuple(session.scalars(select(UnderwritingResearchObjectAlias))) == (
        existing_alias,
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )


def test_foundation_fixture_rejects_alias_owned_by_another_object_atomically(
    session,
) -> None:
    other = _object(session, "company", "US:OTHER:COMPANY", "Other Inc.")
    session.add(
        UnderwritingResearchObjectAlias(
            object_id=other.id,
            alias="Google",
            normalized_alias="google",
            locale="en",
            created_at=NOW,
        )
    )
    session.flush()

    with pytest.raises(ValidationError, match="alias conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert {
        row.external_key for row in session.scalars(select(UnderwritingResearchObject))
    } == {"US:OTHER:COMPANY"}
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchObjectAlias)
        )
        == 1
    )


def test_foundation_fixture_rejects_non_manifest_company_parent_for_fixture_security(
    session,
) -> None:
    other_company = _object(session, "company", "OTHER:COMPANY", "Other Company")
    catl_security = _object(session, "security", "SZSE:300750", "宁德时代 A 股")
    _relation(
        session,
        other_company.id,
        catl_security.id,
        "company_has_security",
    )

    with pytest.raises(ValidationError, match="relation conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert {
        row.external_key for row in session.scalars(select(UnderwritingResearchObject))
    } == {"OTHER:COMPANY", "SZSE:300750"}
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 1
    )


@pytest.mark.parametrize(
    ("parent_key", "child_key", "relation_type"),
    [
        (
            "GLOBAL:INTERNET_SERVICES:INDUSTRY",
            "CN:300750:COMPANY",
            "industry_exposes_company",
        ),
        (
            "US:ALPHABET:COMPANY",
            "GLOBAL:INTERNET_SERVICES:INDUSTRY",
            "industry_exposes_company",
        ),
        (
            "GLOBAL:INTERNET_SERVICES:INDUSTRY",
            "US:ALPHABET:COMPANY",
            "company_has_security",
        ),
    ],
)
def test_foundation_fixture_rejects_non_exact_industry_company_relations_atomically(
    session, parent_key, child_key, relation_type
) -> None:
    industry = _object(
        session,
        "industry",
        "GLOBAL:INTERNET_SERVICES:INDUSTRY",
        "Internet Services",
    )
    alphabet = _object(
        session,
        "company",
        "US:ALPHABET:COMPANY",
        "Alphabet Inc.",
    )
    catl = _object(
        session,
        "company",
        "CN:300750:COMPANY",
        "宁德时代新能源科技股份有限公司（CATL）",
    )
    objects = {
        industry.external_key: industry,
        alphabet.external_key: alphabet,
        catl.external_key: catl,
    }
    _relation(session, objects[parent_key].id, objects[child_key].id, relation_type)

    with pytest.raises(ValidationError, match="relation conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert {
        row.external_key for row in session.scalars(select(UnderwritingResearchObject))
    } == {
        "GLOBAL:INTERNET_SERVICES:INDUSTRY",
        "US:ALPHABET:COMPANY",
        "CN:300750:COMPANY",
    }
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 1
    )


def test_foundation_fixture_rejects_fixture_company_relation_to_extra_security(
    session,
) -> None:
    catl_company = _object(session, "company", "CN:300750:COMPANY", "宁德时代")
    extra_security = _object(session, "security", "OTHER:SECURITY", "Other Security")
    _relation(
        session,
        catl_company.id,
        extra_security.id,
        "company_has_security",
    )

    with pytest.raises(ValidationError, match="relation conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert {
        row.external_key for row in session.scalars(select(UnderwritingResearchObject))
    } == {"CN:300750:COMPANY", "OTHER:SECURITY"}
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 1
    )


def test_foundation_fixture_rejects_reversed_relation_between_fixture_objects(
    session,
) -> None:
    catl_company = _object(session, "company", "CN:300750:COMPANY", "宁德时代")
    catl_security = _object(session, "security", "SZSE:300750", "宁德时代 A 股")
    _relation(
        session,
        catl_security.id,
        catl_company.id,
        "company_has_security",
    )

    with pytest.raises(ValidationError, match="relation conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 1
    )


def test_foundation_fixture_rejects_external_parent_for_fixture_company(
    session,
) -> None:
    catl_company = _object(session, "company", "CN:300750:COMPANY", "宁德时代")
    external_security = _object(
        session, "security", "EXTERNAL:SECURITY", "External Security"
    )
    _relation(
        session,
        external_security.id,
        catl_company.id,
        "company_has_security",
    )

    with pytest.raises(ValidationError, match="relation conflict"):
        ProductFoundationFixtureService(session, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )

    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingObjectIdentityVersion)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingObjectRelation))
        == 1
    )


def test_foundation_fixture_supports_alphabet_multi_security_project_membership(
    session,
) -> None:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    project = ResearchProjectService(session, now=lambda: NOW).create_project(
        loaded.objects["US:ALPHABET:COMPANY"].id,
        (
            loaded.objects["NASDAQ:GOOGL"].id,
            loaded.objects["NASDAQ:GOOG"].id,
        ),
    )

    assert set(project.target_security_ids) == {
        loaded.objects["NASDAQ:GOOGL"].id,
        loaded.objects["NASDAQ:GOOG"].id,
    }
    assert {identity.symbol for identity in project.security_identities} == {
        "GOOGL",
        "GOOG",
    }
