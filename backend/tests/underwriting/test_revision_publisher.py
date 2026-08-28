"""Publication contract for immutable investment-research revisions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sqlite3
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    ProductHistoricalBasisInput,
    PublicationStatus,
    ResearchAgendaInput,
    ResearchScopeInput,
    SecurityRightsInput,
    agenda_items_hash,
    product_historical_basis_payload_and_hash,
)
from app.underwriting.domain.types import AnswerabilityState, InvestmentMandateInput
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAssessmentVersion,
    UnderwritingPriceSnapshot,
    UnderwritingResearchProjectSecurity,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    price_snapshot_hash,
    security_rights_hash,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from app.underwriting.services.research_revision_diff import (
    ProductResearchRevisionSummary,
    ResearchRevisionDiffService,
)
from app.underwriting.services.revision_publisher import (
    PRODUCT_MANIFEST_SCHEMA,
    RevisionPublisher,
)
import app.underwriting.services.revision_publisher as revision_publisher_module
from app.underwriting.services.workspace_draft import WorkspaceDraftService


NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
MARKET = datetime(2026, 8, 20, 7, 30, tzinfo=UTC)
EFFECTIVE = datetime(2020, 1, 1, tzinfo=UTC)
A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64
D64 = "d" * 64


def _object(session, kind: str, key: str, *, object_id: UUID | None = None):
    row = UnderwritingResearchObject(
        id=object_id,
        kind=kind,
        external_key=f"{key}:{uuid4()}",
        canonical_name=key,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _ready_graph(
    session,
    *,
    suffix: str = "main",
    rights_effective_to: datetime | None = None,
    company_identity_effective_from: datetime = EFFECTIVE,
    security_ids: tuple[UUID, ...] | None = None,
    price_ids: tuple[UUID, ...] | None = None,
    rights_ids: tuple[UUID, ...] | None = None,
    price_market_ats: tuple[datetime, ...] | None = None,
) -> dict[str, object]:
    projects = ResearchProjectService(session, now=lambda: NOW)
    market = MarketSnapshotService(session, now=lambda: NOW)
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    company = _object(session, "company", f"Company-{suffix}")
    requested_security_ids = security_ids or (None,)
    securities = tuple(
        _object(
            session,
            "security",
            f"SEC-{suffix}-{index}",
            object_id=security_id,
        )
        for index, security_id in enumerate(requested_security_ids, start=1)
    )
    session.add_all(
        UnderwritingObjectRelation(
            parent_id=company.id,
            child_id=security.id,
            relation_type="company_has_security",
            created_at=NOW,
        )
        for security in securities
    )
    session.flush()
    projects.append_identity_version(
        object_id=company.id,
        canonical_name=company.canonical_name,
        symbol=None,
        exchange=None,
        share_class=None,
        trading_currency=None,
        effective_from=company_identity_effective_from,
        effective_to=None,
        expected_parent_id=None,
    )
    for index, security in enumerate(securities, start=1):
        projects.append_identity_version(
            object_id=security.id,
            canonical_name=security.canonical_name,
            symbol=f"TEST{index}",
            exchange="TEST",
            share_class="ordinary",
            trading_currency="CNY",
            effective_from=EFFECTIVE,
            effective_to=None,
            expected_parent_id=None,
        )
    target_security_ids = tuple(sorted((item.id for item in securities), key=str))
    project = projects.create_project(company.id, target_security_ids)
    mandate = projects.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            "caller-key",
            3,
            "CNY",
            Decimal("0.10000000"),
            Decimal("0.30000000"),
            ("cash",),
        ),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=EFFECTIVE,
        expires_at=None,
        expected_parent_id=None,
    )
    scope = projects.append_scope(
        project.id,
        ResearchScopeInput(company.id, target_security_ids, (), ("core",), None, ()),
        None,
    )
    items = ("business baseline", "valuation gaps")
    agenda = projects.append_agenda(
        project.id,
        ResearchAgendaInput(
            scope.id,
            items,
            AgendaGeneratorInput(
                AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
                "foundation",
                "v1",
                None,
                None,
                A64,
                agenda_items_hash(items),
            ),
        ),
        None,
    )
    basis = projects.create_historical_basis(
        ProductHistoricalBasisInput(datetime(2026, 8, 19, tzinfo=UTC), A64, B64, C64)
    )
    prices = []
    for index, security in enumerate(securities):
        market_at = price_market_ats[index] if price_market_ats else MARKET
        if price_ids is None:
            price = market.freeze_price(
                PriceSnapshotInput(
                    security.id,
                    Decimal("100.0000000000"),
                    "CNY",
                    "close",
                    "unadjusted",
                    market_at,
                    market_at + timedelta(minutes=5),
                    "exchange",
                    A64,
                )
            )
        else:
            price = UnderwritingPriceSnapshot(
                id=price_ids[index],
                security_identity_id=security.id,
                price=Decimal("100.0000000000"),
                currency="CNY",
                price_type="close",
                adjustment_basis="unadjusted",
                market_at=market_at,
                available_at=market_at + timedelta(minutes=5),
                source_id="exchange",
                raw_hash=A64,
                content_hash=A64,
                created_at=NOW,
            )
            price.content_hash = price_snapshot_hash(price)
            session.add(price)
            session.flush()
        prices.append(price)
    capital = market.freeze_capital_structure(
        CapitalStructureSnapshotInput(
            company.id,
            "CNY",
            Decimal("10.0000000000"),
            Decimal("2.0000000000"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("100.0000000000"),
            Decimal("100.0000000000"),
            (),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            MARKET,
            MARKET + timedelta(minutes=5),
            "filing",
            C64,
        )
    )
    rights_versions = []
    for index, security in enumerate(securities):
        if rights_ids is None:
            rights = market.freeze_security_rights(
                SecurityRightsInput(
                    security.id,
                    Decimal("1.0000000000"),
                    Decimal("1.0000000000"),
                    Decimal("1.0000000000"),
                    Decimal("1.0000000000"),
                    Decimal("1.0000000000"),
                    EFFECTIVE,
                    rights_effective_to,
                    "listing-rules",
                    D64,
                ),
                expected_parent_id=None,
            )
        else:
            rights = UnderwritingSecurityRightsVersion(
                id=rights_ids[index],
                security_identity_id=security.id,
                version=1,
                economic_units=Decimal("1.0000000000"),
                votes_per_unit=Decimal("1.0000000000"),
                conversion_ratio=Decimal("1.0000000000"),
                adr_ratio=Decimal("1.0000000000"),
                dividend_rights_per_unit=Decimal("1.0000000000"),
                effective_from=EFFECTIVE,
                effective_to=rights_effective_to,
                source_id="listing-rules",
                raw_hash=D64,
                supersedes_id=None,
                content_hash=A64,
                created_at=NOW,
            )
            rights.content_hash = security_rights_hash(rights)
            session.add(rights)
            session.flush()
        rights_versions.append(rights)
    security = securities[0]
    price = prices[0]
    rights = rights_versions[0]
    draft = drafts.create(project.id)
    draft = drafts.save(
        project.id,
        expected_lock_version=draft.lock_version,
        patch={
            "mandate_id": mandate.id,
            "scope_id": scope.id,
            "agenda_id": agenda.id,
            "historical_basis_id": basis.id,
            "price_snapshot_ids": tuple(item.id for item in prices),
            "capital_structure_snapshot_id": capital.id,
            "security_rights_ids": tuple(item.id for item in rights_versions),
            "user_focus": "durable cash returns",
        },
    )
    return locals()


def _formal_counts(session) -> tuple[int, int, int, int]:
    return tuple(
        session.scalar(select(func.count()).select_from(model))
        for model in (
            UnderwritingResearchAssessmentVersion,
            UnderwritingRevisionBoundary,
            UnderwritingRevisionManifest,
            UnderwritingResearchVersion,
        )
    )  # type: ignore[return-value]


def _foundation_ready_graph(
    session, *, company_key: str, security_keys: tuple[str, ...]
) -> dict[str, object]:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    projects = ResearchProjectService(session, now=lambda: NOW)
    market = MarketSnapshotService(session, now=lambda: NOW)
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    company = loaded.objects[company_key]
    securities = tuple(loaded.objects[key] for key in security_keys)
    project = projects.create_project(
        company.id, tuple(security.id for security in securities)
    )
    mandate = projects.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            "foundation-fixture",
            3,
            "CNY",
            Decimal("0.10000000"),
            Decimal("0.30000000"),
            ("cash",),
        ),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=EFFECTIVE,
        expires_at=None,
        expected_parent_id=None,
    )
    scope = projects.append_scope(
        project.id,
        ResearchScopeInput(
            company.id,
            tuple(security.id for security in securities),
            (),
            (),
            None,
            (),
        ),
        None,
    )
    agenda_items = ("核验公司经营基线", "识别关键证据缺口")
    agenda = projects.append_agenda(
        project.id,
        ResearchAgendaInput(
            scope.id,
            agenda_items,
            AgendaGeneratorInput(
                AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
                "product.foundation.agenda",
                "1.0.0",
                None,
                None,
                A64,
                agenda_items_hash(agenda_items),
            ),
        ),
        None,
    )
    basis = projects.create_historical_basis(
        ProductHistoricalBasisInput(datetime(2026, 8, 19, tzinfo=UTC), A64, B64, C64)
    )
    prices = tuple(
        market.freeze_price(
            PriceSnapshotInput(
                security.id,
                Decimal("100.0000000000") + index,
                loaded.identities[key].trading_currency,
                "synthetic_test_close",
                "unadjusted",
                MARKET + timedelta(hours=index),
                MARKET + timedelta(hours=index, minutes=5),
                "synthetic-test-only",
                canonical_hash({"synthetic_test_price": key}),
            )
        )
        for index, (key, security) in enumerate(zip(security_keys, securities))
    )
    boundary_at = max(price.market_at for price in prices)
    foreign_currencies = {price.currency for price in prices if price.currency != "CNY"}
    fxs = tuple(
        market.freeze_fx(
            FXSnapshotInput(
                currency,
                "CNY",
                Decimal("7.100000000000"),
                FxQuoteDirection.QUOTE_PER_BASE,
                boundary_at,
                boundary_at + timedelta(minutes=5),
                "synthetic-test-only",
                canonical_hash({"synthetic_test_fx": f"{currency}/CNY"}),
            )
        )
        for currency in sorted(foreign_currencies)
    )
    capital = market.freeze_capital_structure(
        CapitalStructureSnapshotInput(
            company.id,
            "CNY",
            Decimal("10"),
            Decimal("2"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("100"),
            Decimal("100"),
            (),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            boundary_at,
            boundary_at + timedelta(minutes=5),
            "synthetic-test-only",
            canonical_hash({"synthetic_test_capital": company_key}),
        )
    )
    rights = tuple(loaded.rights[key] for key in security_keys)
    draft = drafts.create(project.id)
    draft = drafts.save(
        project.id,
        expected_lock_version=draft.lock_version,
        patch={
            "mandate_id": mandate.id,
            "scope_id": scope.id,
            "agenda_id": agenda.id,
            "historical_basis_id": basis.id,
            "price_snapshot_ids": tuple(price.id for price in prices),
            "fx_snapshot_ids": tuple(fx.id for fx in fxs),
            "capital_structure_snapshot_id": capital.id,
            "security_rights_ids": tuple(item.id for item in rights),
            "user_focus": None,
        },
    )
    return locals()


def test_foundation_golden_path_publishes_only_insufficient_evidence(session) -> None:
    graph = _foundation_ready_graph(
        session,
        company_key="CN:300750:COMPANY",
        security_keys=("SZSE:300750",),
    )
    publisher = RevisionPublisher(session, now=lambda: NOW)
    preview = publisher.preview(graph["project"].id, graph["draft"].lock_version)
    revision = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="catl-foundation-1",
    )
    summary = ResearchRevisionDiffService(session).revision_summary(revision.id)

    assert preview.assessment.answerability is AnswerabilityState.NOT_ANSWERABLE
    assert preview.assessment.direction is None
    assert preview.assessment.confidence is None
    assert summary.answerability is AnswerabilityState.NOT_ANSWERABLE
    assert summary.direction is None
    assert summary.confidence is None
    assert summary.publication_status is PublicationStatus.USER_FROZEN


def test_publisher_validates_the_historical_basis_with_the_canonical_helper(
    session, monkeypatch
) -> None:
    graph = _ready_graph(session)
    helper_calls = []

    def wrapped_helper(value):
        helper_calls.append(value)
        return product_historical_basis_payload_and_hash(value)

    monkeypatch.setattr(
        revision_publisher_module,
        "product_historical_basis_payload_and_hash",
        wrapped_helper,
    )

    RevisionPublisher(session, now=lambda: NOW).preview(
        graph["project"].id,
        graph["draft"].lock_version,
    )

    assert len(helper_calls) == 1


def test_product_revision_reader_validates_historical_basis_with_canonical_helper(
    session, monkeypatch
) -> None:
    graph = _ready_graph(session)
    revision = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="canonical-basis-reader",
    )
    import app.underwriting.services.research_revision_diff as revision_diff_module

    helper_calls = []

    def wrapped_helper(value):
        helper_calls.append(value)
        return product_historical_basis_payload_and_hash(value)

    monkeypatch.setattr(
        revision_diff_module,
        "product_historical_basis_payload_and_hash",
        wrapped_helper,
    )

    revision_diff_module.ResearchRevisionDiffService(session).revision_summary(
        revision.id
    )

    assert len(helper_calls) == 1


def test_publisher_preserves_hash_mismatch_for_an_invalid_persisted_basis(
    session,
) -> None:
    graph = _ready_graph(session)
    session.connection().exec_driver_sql(
        "UPDATE uw_historical_bases SET definition_bundle_hash = ? WHERE id = ?",
        (None, graph["basis"].id.hex),
    )
    session.expire_all()

    with pytest.raises(
        ValidationError,
        match="publication boundary reference hash mismatch",
    ):
        RevisionPublisher(session, now=lambda: NOW).preview(
            graph["project"].id,
            graph["draft"].lock_version,
        )


def test_product_revision_reader_preserves_invalid_persisted_basis_error(
    session,
) -> None:
    graph = _ready_graph(session)
    revision = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="invalid-basis-reader",
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_historical_bases SET definition_bundle_hash = ? WHERE id = ?",
        (None, graph["basis"].id.hex),
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="product foundation reference is invalid"):
        ResearchRevisionDiffService(session).revision_summary(revision.id)


def test_alphabet_foundation_searches_and_previews_two_securities_without_publication(
    session,
) -> None:
    graph = _foundation_ready_graph(
        session,
        company_key="US:ALPHABET:COMPANY",
        security_keys=("NASDAQ:GOOGL", "NASDAQ:GOOG"),
    )
    projects = ResearchProjectService(session, now=lambda: NOW)
    search = projects.search_objects("Alphabet", NOW)
    before = _formal_counts(session)
    preview = RevisionPublisher(session, now=lambda: NOW).preview(
        graph["project"].id, graph["draft"].lock_version
    )

    assert {(item.kind.value, item.external_key) for item in search} == {
        ("company", "US:ALPHABET:COMPANY"),
        ("security", "NASDAQ:GOOGL"),
        ("security", "NASDAQ:GOOG"),
    }
    assert len(graph["project"].target_security_ids) == 2
    assert len({item.id for item in graph["rights"]}) == 2
    assert [(fx.base_currency, fx.quote_currency) for fx in graph["fxs"]] == [
        ("USD", "CNY")
    ]
    assert graph["fxs"][0].market_at == graph["boundary_at"]
    assert preview.boundary_as_of == graph["boundary_at"]
    assert preview.assessment.answerability is AnswerabilityState.NOT_ANSWERABLE
    assert _formal_counts(session) == before == (0, 0, 0, 0)


def test_preview_is_deterministic_fail_closed_and_has_zero_writes(session) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)
    before = _formal_counts(session)

    first = publisher.preview(graph["project"].id, graph["draft"].lock_version)
    second = publisher.preview(graph["project"].id, graph["draft"].lock_version)

    assert first == second
    assert _formal_counts(session) == before == (0, 0, 0, 0)
    assert first.assessment.answerability is AnswerabilityState.NOT_ANSWERABLE
    assert first.assessment.direction is None
    assert first.assessment.confidence is None
    assert first.assessment.publication_status is PublicationStatus.USER_FROZEN
    assert first.assessment.blockers
    assert first.assessment.resolution_requirements
    assert first.assessment.next_review_at is None
    assert first.boundary_as_of == MARKET
    assert first.manifest["schema_version"] == PRODUCT_MANIFEST_SCHEMA
    assert first.manifest["assessment_ref"] == "$assessment"
    assert first.manifest["project_ref"] == {
        "project_id": str(graph["project"].id),
        "content_hash": graph["project"].content_hash,
    }
    assert first.manifest["project_membership_refs"] == [
        {
            "membership_id": str(membership.id),
            "security_id": str(membership.security_id),
            "content_hash": membership.content_hash,
        }
        for membership in sorted(
            session.scalars(
                select(UnderwritingResearchProjectSecurity).where(
                    UnderwritingResearchProjectSecurity.project_id
                    == graph["project"].id
                )
            ),
            key=lambda row: str(row.id),
        )
    ]
    assert first.manifest["model_refs"] == []
    assert first.manifest["memo_ref"] is None
    assert first.manifest_hash == canonical_hash(first.manifest)


def test_preview_rejects_missing_cross_project_and_stale_draft_references(
    session,
) -> None:
    graph = _ready_graph(session)
    other = _ready_graph(session, suffix="other")
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    publisher = RevisionPublisher(session, now=lambda: NOW)

    missing = drafts.save(
        graph["project"].id,
        expected_lock_version=graph["draft"].lock_version,
        patch={"agenda_id": None},
    )
    with pytest.raises(ValidationError, match="agenda"):
        publisher.preview(graph["project"].id, missing.lock_version)

    crossed = drafts.save(
        graph["project"].id,
        expected_lock_version=missing.lock_version,
        patch={"agenda_id": other["agenda"].id},
    )
    with pytest.raises(ValidationError, match="agenda.*project"):
        publisher.preview(graph["project"].id, crossed.lock_version)
    with pytest.raises(ConflictError, match="draft changed"):
        publisher.preview(graph["project"].id, crossed.lock_version - 1)


def test_preview_uses_frozen_market_instant_for_rights(session) -> None:
    graph = _ready_graph(session, rights_effective_to=MARKET)

    with pytest.raises(ValidationError, match="effective rights"):
        RevisionPublisher(session, now=lambda: NOW + timedelta(days=300)).preview(
            graph["project"].id, graph["draft"].lock_version
        )


def test_preview_uses_latest_multi_security_price_as_rights_boundary(session) -> None:
    security_ids = (uuid4(), uuid4())
    later_market = MARKET + timedelta(days=1)
    expired = _ready_graph(
        session,
        suffix="multi-expired",
        security_ids=security_ids,
        price_market_ats=(MARKET, later_market),
        rights_effective_to=MARKET + timedelta(hours=12),
    )

    with pytest.raises(ValidationError, match="effective rights"):
        RevisionPublisher(session, now=lambda: NOW).preview(
            expired["project"].id, expired["draft"].lock_version
        )

    valid = _ready_graph(
        session,
        suffix="multi-valid",
        security_ids=(uuid4(), uuid4()),
        price_market_ats=(MARKET, later_market),
    )
    preview = RevisionPublisher(session, now=lambda: NOW).preview(
        valid["project"].id, valid["draft"].lock_version
    )
    assert preview.boundary_as_of == later_market


def test_preview_requires_company_and_security_identity_at_frozen_market_instant(
    session,
) -> None:
    graph = _ready_graph(
        session,
        company_identity_effective_from=MARKET + timedelta(days=1),
    )

    with pytest.raises(ValidationError, match="effective identities"):
        RevisionPublisher(session, now=lambda: NOW).preview(
            graph["project"].id, graph["draft"].lock_version
        )


def test_publish_freezes_exact_graph_and_reader_replays_without_latest_queries(
    session,
) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)
    preview = publisher.preview(graph["project"].id, graph["draft"].lock_version)

    published = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-1",
    )
    session.commit()
    session.expire_all()
    summary = ResearchRevisionDiffService(session).revision_summary(published.id)

    assert isinstance(summary, ProductResearchRevisionSummary)
    assert summary.project_id == graph["project"].id
    assert summary.object_id == graph["company"].id
    assert summary.basis_id == graph["basis"].id
    assert summary.version_kind == "independent_research"
    assert summary.sequence == 1
    assert summary.parent_revision_id is None
    assert summary.market_snapshot_ids == (
        graph["price"].id,
        graph["capital"].id,
        graph["rights"].id,
    )
    assert summary.answerability is AnswerabilityState.NOT_ANSWERABLE
    assert summary.direction is None and summary.confidence is None
    assert summary.publication_status is PublicationStatus.USER_FROZEN
    assert summary.manifest_hash == published.manifest_hash
    assert published.price_snapshot_ids == (graph["price"].id,)
    assert published.fx_snapshot_ids == ()
    assert published.capital_structure_snapshot_id == graph["capital"].id
    assert published.security_rights_ids == (graph["rights"].id,)
    assert preview.manifest["project_id"] == str(summary.project_id)

    _ready_graph(session, suffix="later")
    assert (
        ResearchRevisionDiffService(session).revision_summary(published.id) == summary
    )


def test_two_security_publication_ignores_crossed_snapshot_uuid_order(session) -> None:
    security_ids = (UUID(int=10), UUID(int=20))
    price_ids = (UUID(int=400), UUID(int=100))
    rights_ids = (UUID(int=200), UUID(int=300))
    graph = _ready_graph(
        session,
        suffix="two-security",
        security_ids=security_ids,
        price_ids=price_ids,
        rights_ids=rights_ids,
    )
    publisher = RevisionPublisher(session, now=lambda: NOW)
    preview = publisher.preview(graph["project"].id, graph["draft"].lock_version)

    assert tuple(
        item.security_identity_id
        for item in sorted(graph["prices"], key=lambda item: str(item.id))
    ) == tuple(reversed(security_ids))
    assert (
        tuple(
            item.security_identity_id
            for item in sorted(graph["rights_versions"], key=lambda item: str(item.id))
        )
        == security_ids
    )
    assert preview.boundary.price_snapshot_ids == tuple(sorted(price_ids, key=str))
    published = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-two-security",
    )
    repeated = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-two-security",
    )
    summary = ResearchRevisionDiffService(session).revision_summary(published.id)

    assert repeated == published
    assert summary.price_snapshot_ids == tuple(sorted(price_ids, key=str))
    assert summary.security_rights_ids == tuple(sorted(rights_ids, key=str))


def test_same_idempotency_key_returns_verified_revision_before_stale_lock_check(
    session,
) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)
    first = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key=" publish-1 ",
    )

    repeated = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-1",
    )

    assert repeated == first
    assert _formal_counts(session) == (1, 1, 1, 1)
    assert (
        WorkspaceDraftService(session, now=lambda: NOW)
        .read(graph["project"].id)
        .lock_version
        == graph["draft"].lock_version + 1
    )


@pytest.mark.parametrize(
    "stage",
    (
        "append_assessment",
        "append_boundary",
        "append_manifest",
        "append_product_revision",
        "reset_draft_after_publish",
    ),
)
def test_publish_rolls_back_every_partial_insert_and_keeps_session_usable(
    session, monkeypatch, stage
) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)
    original_draft = WorkspaceDraftService(session, now=lambda: NOW).read(
        graph["project"].id
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"fail-{stage}")

    monkeypatch.setattr(publisher.repository, stage, fail)
    with pytest.raises(RuntimeError, match=f"fail-{stage}"):
        publisher.publish(
            graph["project"].id,
            graph["draft"].lock_version,
            idempotency_key=f"publish-{stage}",
        )

    assert _formal_counts(session) == (0, 0, 0, 0)
    assert (
        WorkspaceDraftService(session, now=lambda: NOW).read(graph["project"].id)
        == original_draft
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingWorkspaceDraft))
        == 1
    )


def test_publish_maps_integrity_failure_to_conflict_without_partial_rows(
    session, monkeypatch
) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)

    def fail(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, sqlite3.IntegrityError("forced failure"))

    monkeypatch.setattr(publisher.repository, "append_manifest", fail)
    with pytest.raises(ConflictError, match="publication conflicted"):
        publisher.publish(
            graph["project"].id,
            graph["draft"].lock_version,
            idempotency_key="integrity-failure",
        )

    assert _formal_counts(session) == (0, 0, 0, 0)
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingWorkspaceDraft))
        == 1
    )


def test_publish_creates_exact_successor_and_rejects_second_key_for_stale_draft(
    session,
) -> None:
    graph = _ready_graph(session)
    publisher = RevisionPublisher(session, now=lambda: NOW)
    first = publisher.publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-1",
    )
    with pytest.raises(ConflictError, match="draft changed"):
        publisher.publish(
            graph["project"].id,
            graph["draft"].lock_version,
            idempotency_key="publish-other",
        )
    current = WorkspaceDraftService(session, now=lambda: NOW).read(graph["project"].id)
    second = publisher.publish(
        graph["project"].id,
        current.lock_version,
        idempotency_key="publish-2",
    )
    row = session.get(UnderwritingResearchVersion, second.id)

    assert row.sequence == 2
    assert row.supersedes_id == first.id
    assert row.parent_ids == [str(first.id)]
    assert current.base_revision_id == first.id
    assert (
        WorkspaceDraftService(session, now=lambda: NOW)
        .read(graph["project"].id)
        .base_revision_id
        == second.id
    )


@pytest.mark.parametrize("key", ("", "   ", "x" * 121, 1, None))
def test_publish_rejects_invalid_idempotency_key_without_writes(session, key) -> None:
    graph = _ready_graph(session)
    with pytest.raises(ValidationError, match="idempotency"):
        RevisionPublisher(session, now=lambda: NOW).publish(
            graph["project"].id,
            graph["draft"].lock_version,
            idempotency_key=key,
        )
    assert _formal_counts(session) == (0, 0, 0, 0)


def test_reader_fails_closed_when_manifest_or_exact_reference_is_tampered(
    session,
) -> None:
    graph = _ready_graph(session)
    published = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-1",
    )
    row = session.get(UnderwritingResearchVersion, published.id)
    manifest = session.get(UnderwritingRevisionManifest, row.manifest_id)
    tampered = dict(manifest.manifest)
    tampered["price_snapshot_ids"] = [str(uuid4())]
    manifest.manifest = tampered

    with pytest.raises(ValidationError, match="manifest|snapshot"):
        ResearchRevisionDiffService(session).revision_summary(published.id)
    with pytest.raises(ValidationError, match="manifest|snapshot"):
        RevisionPublisher(session, now=lambda: NOW).publish(
            graph["project"].id,
            graph["draft"].lock_version,
            idempotency_key="publish-1",
        )


def test_reader_uses_only_manifest_frozen_project_memberships(session) -> None:
    graph = _ready_graph(session)
    published = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-memberships",
    )
    session.commit()
    original = ResearchRevisionDiffService(session).revision_summary(published.id)
    revision = session.get(UnderwritingResearchVersion, published.id)
    manifest = session.get(UnderwritingRevisionManifest, revision.manifest_id)
    frozen_ref = manifest.manifest["project_membership_refs"][0]

    later_security = _object(session, "security", "SEC-later-membership")
    session.add(
        UnderwritingResearchProjectSecurity(
            project_id=graph["project"].id,
            security_id=later_security.id,
            content_hash=canonical_hash(
                {
                    "schema_version": "product.research-project-security.v1",
                    "project_id": str(graph["project"].id),
                    "security_id": str(later_security.id),
                }
            ),
            created_at=NOW + timedelta(days=1),
        )
    )
    session.commit()

    assert (
        ResearchRevisionDiffService(session).revision_summary(published.id) == original
    )

    frozen_membership = session.get(
        UnderwritingResearchProjectSecurity, UUID(frozen_ref["membership_id"])
    )
    frozen_membership.content_hash = B64
    with pytest.raises(ValidationError, match="project.*identity|membership"):
        ResearchRevisionDiffService(session).revision_summary(published.id)


def test_reader_rejects_deleted_exact_project_membership(session) -> None:
    graph = _ready_graph(session)
    published = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="publish-delete-membership",
    )
    revision = session.get(UnderwritingResearchVersion, published.id)
    manifest = session.get(UnderwritingRevisionManifest, revision.manifest_id)
    membership_id = UUID(
        manifest.manifest["project_membership_refs"][0]["membership_id"]
    )
    stored_id = (
        membership_id.hex
        if session.get_bind().dialect.name == "sqlite"
        else str(membership_id)
    )
    session.execute(
        text("DELETE FROM uw_research_project_securities WHERE id = :membership_id"),
        {"membership_id": stored_id},
    )
    session.flush()
    session.expire_all()

    with pytest.raises(ValidationError, match="project.*identity|membership"):
        ResearchRevisionDiffService(session).revision_summary(published.id)


def test_real_sqlite_caller_rollback_reverts_entire_publication(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-publication-rollback.sqlite'}", future=True
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    graph = _ready_graph(seed)
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    seed.commit()
    seed.close()

    publishing = sessions()
    try:
        assert (
            WorkspaceDraftService(publishing, now=lambda: NOW)
            .read(project_id)
            .lock_version
            == expected_lock
        )
        RevisionPublisher(publishing, now=lambda: NOW).publish(
            project_id, expected_lock, idempotency_key="publish-rollback"
        )
        publishing.rollback()

        observer = sessions()
        try:
            assert _formal_counts(observer) == (0, 0, 0, 0)
            draft = WorkspaceDraftService(observer, now=lambda: NOW).read(project_id)
            assert draft.lock_version == expected_lock
            assert draft.base_revision_id is None
        finally:
            observer.close()
    finally:
        publishing.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _run_concurrent_publications(sessions, project_id, expected_lock, keys):
    barrier = Barrier(2)

    def publish(key):
        worker = sessions()
        try:
            barrier.wait(timeout=5)
            result = RevisionPublisher(worker, now=lambda: NOW).publish(
                project_id, expected_lock, idempotency_key=key
            )
            worker.commit()
            return result
        except Exception as exc:  # The assertions inspect the exact domain outcome.
            worker.rollback()
            return exc
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, key) for key in keys]
        return tuple(future.result(timeout=20) for future in futures)


def test_real_sqlite_concurrent_same_key_converges_to_one_revision(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-publication-same.sqlite'}",
        future=True,
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    graph = _ready_graph(seed)
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    seed.commit()
    seed.close()
    try:
        results = _run_concurrent_publications(
            sessions, project_id, expected_lock, ("same-key", "same-key")
        )
        assert all(not isinstance(result, Exception) for result in results)
        assert results[0].id == results[1].id
        observer = sessions()
        try:
            assert _formal_counts(observer) == (1, 1, 1, 1)
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_sqlite_concurrent_different_keys_has_one_winner(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-publication-different.sqlite'}",
        future=True,
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    graph = _ready_graph(seed)
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    seed.commit()
    seed.close()
    try:
        results = _run_concurrent_publications(
            sessions, project_id, expected_lock, ("key-one", "key-two")
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        errors = [result for result in results if isinstance(result, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], ConflictError)
        observer = sessions()
        try:
            assert _formal_counts(observer) == (1, 1, 1, 1)
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_sqlite_deferred_read_transactions_never_leak_locked_errors(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-publication-deferred.sqlite'}",
        future=True,
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    graph = _ready_graph(seed, suffix="deferred")
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    seed.commit()
    seed.close()
    barrier = Barrier(2)

    def publish_from_deferred_transaction():
        worker = sessions()
        try:
            worker.connection().exec_driver_sql("BEGIN")
            WorkspaceDraftService(worker, now=lambda: NOW).read(project_id)
            barrier.wait(timeout=5)
            result = RevisionPublisher(worker, now=lambda: NOW).publish(
                project_id, expected_lock, idempotency_key="deferred-same-key"
            )
            worker.commit()
            return result
        except Exception as exc:
            worker.rollback()
            return exc
        finally:
            worker.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish_from_deferred_transaction) for _ in range(2)]
            results = tuple(future.result(timeout=20) for future in futures)
        assert sum(not isinstance(result, Exception) for result in results) == 1
        errors = tuple(result for result in results if isinstance(result, Exception))
        assert len(errors) == 1
        assert isinstance(errors[0], ConflictError)
        assert "locked" not in type(errors[0]).__name__.lower()

        retry = sessions()
        try:
            recovered = RevisionPublisher(retry, now=lambda: NOW).publish(
                project_id, expected_lock, idempotency_key="deferred-same-key"
            )
            winner = next(
                result for result in results if not isinstance(result, Exception)
            )
            assert recovered.id == winner.id
            retry.rollback()
        finally:
            retry.close()
        observer = sessions()
        try:
            assert _formal_counts(observer) == (1, 1, 1, 1)
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_public_product_read_walks_1200_parents_once_without_recursion(
    session, monkeypatch
) -> None:
    nodes = []
    for index in range(1200):
        nodes.append(
            SimpleNamespace(
                id=UUID(int=index + 1),
                supersedes_id=nodes[-1].id if nodes else None,
                manifest_schema=PRODUCT_MANIFEST_SCHEMA,
            )
        )
    by_id = {node.id: node for node in nodes}
    service = ResearchRevisionDiffService(session)
    original_get = session.get
    verified = []
    revision_gets = 0

    def fake_get(model, identity, *args, **kwargs):
        nonlocal revision_gets
        if model is UnderwritingResearchVersion:
            revision_gets += 1
            return by_id.get(identity)
        return original_get(model, identity, *args, **kwargs)

    def verify_row(revision, parent_summary, parent_assessment):
        assert parent_summary is (verified[-1] if verified else None)
        assert parent_assessment is (verified[-1] if verified else None)
        verified.append(revision)
        return revision, revision

    monkeypatch.setattr(service, "_revision", lambda revision_id: by_id[revision_id])
    monkeypatch.setattr(session, "get", fake_get)
    monkeypatch.setattr(
        service, "_product_revision_summary_row", verify_row, raising=False
    )

    assert service.revision_summary(nodes[-1].id) is nodes[-1]
    assert verified == nodes
    assert revision_gets == len(nodes) - 1


def test_public_product_read_detects_parent_cycle_before_replay(
    session, monkeypatch
) -> None:
    first = SimpleNamespace(
        id=UUID(int=1),
        supersedes_id=UUID(int=2),
        manifest_schema=PRODUCT_MANIFEST_SCHEMA,
    )
    second = SimpleNamespace(
        id=UUID(int=2),
        supersedes_id=first.id,
        manifest_schema=PRODUCT_MANIFEST_SCHEMA,
    )
    by_id = {first.id: first, second.id: second}
    service = ResearchRevisionDiffService(session)
    monkeypatch.setattr(service, "_revision", lambda _revision_id: first)
    monkeypatch.setattr(
        session,
        "get",
        lambda model, identity: (
            by_id.get(identity) if model is UnderwritingResearchVersion else None
        ),
    )

    with pytest.raises(ValidationError, match="cycle"):
        service.revision_summary(first.id)


def test_real_sqlite_two_sessions_converge_same_key_and_reject_other_key(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-publication.sqlite'}", future=True
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    graph = _ready_graph(seed)
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    seed.commit()
    seed.close()
    first = sessions()
    same_key = sessions()
    other_key = sessions()
    try:
        assert (
            WorkspaceDraftService(same_key, now=lambda: NOW)
            .read(project_id)
            .lock_version
            == expected_lock
        )
        assert (
            WorkspaceDraftService(other_key, now=lambda: NOW)
            .read(project_id)
            .lock_version
            == expected_lock
        )
        winner = RevisionPublisher(first, now=lambda: NOW).publish(
            project_id, expected_lock, idempotency_key="publish-1"
        )
        first.commit()

        recovered = RevisionPublisher(same_key, now=lambda: NOW).publish(
            project_id, expected_lock, idempotency_key="publish-1"
        )
        assert recovered.id == winner.id
        same_key.rollback()
        with pytest.raises(ConflictError, match="draft changed"):
            RevisionPublisher(other_key, now=lambda: NOW).publish(
                project_id, expected_lock, idempotency_key="publish-2"
            )
        assert (
            other_key.scalar(
                select(func.count()).select_from(UnderwritingResearchVersion)
            )
            == 1
        )
    finally:
        first.rollback()
        same_key.rollback()
        other_key.rollback()
        first.close()
        same_key.close()
        other_key.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_two_session_publication_if_configured(session, engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("TEST_DATABASE_URL is not configured")
    graph = _ready_graph(session, suffix="postgres")
    project_id = graph["project"].id
    expected_lock = graph["draft"].lock_version
    session.commit()
    sessions = sessionmaker(bind=engine, future=True)
    first = sessions()
    second = sessions()
    try:
        winner = RevisionPublisher(first, now=lambda: NOW).publish(
            project_id, expected_lock, idempotency_key="publish-pg"
        )
        first.commit()
        recovered = RevisionPublisher(second, now=lambda: NOW).publish(
            project_id, expected_lock, idempotency_key="publish-pg"
        )
        assert recovered.id == winner.id
    finally:
        first.close()
        second.close()
