"""Publication contract for immutable investment-research revisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    CapitalStructureSnapshotInput,
    PriceSnapshotInput,
    ProductHistoricalBasisInput,
    PublicationStatus,
    ResearchAgendaInput,
    ResearchScopeInput,
    SecurityRightsInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import AnswerabilityState, InvestmentMandateInput
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAssessmentVersion,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.market_snapshots import MarketSnapshotService
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.research_revision_diff import (
    ProductResearchRevisionSummary,
    ResearchRevisionDiffService,
)
from app.underwriting.services.revision_publisher import (
    PRODUCT_MANIFEST_SCHEMA,
    RevisionPublisher,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService


NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
MARKET = datetime(2026, 8, 20, 7, 30, tzinfo=UTC)
EFFECTIVE = datetime(2020, 1, 1, tzinfo=UTC)
A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64
D64 = "d" * 64


def _object(session, kind: str, key: str):
    row = UnderwritingResearchObject(
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
) -> dict[str, object]:
    projects = ResearchProjectService(session, now=lambda: NOW)
    market = MarketSnapshotService(session, now=lambda: NOW)
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    company = _object(session, "company", f"Company-{suffix}")
    security = _object(session, "security", f"SEC-{suffix}")
    session.add(
        UnderwritingObjectRelation(
            parent_id=company.id,
            child_id=security.id,
            relation_type="company_has_security",
            created_at=NOW,
        )
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
    projects.append_identity_version(
        object_id=security.id,
        canonical_name=security.canonical_name,
        symbol="TEST",
        exchange="TEST",
        share_class="ordinary",
        trading_currency="CNY",
        effective_from=EFFECTIVE,
        effective_to=None,
        expected_parent_id=None,
    )
    project = projects.create_project(company.id, (security.id,))
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
        ResearchScopeInput(company.id, (security.id,), (), ("core",), None, ()),
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
                None,
                agenda_items_hash(items),
            ),
        ),
        None,
    )
    basis = projects.create_historical_basis(
        ProductHistoricalBasisInput(datetime(2026, 8, 19, tzinfo=UTC), A64, B64, C64)
    )
    price = market.freeze_price(
        PriceSnapshotInput(
            security.id,
            Decimal("100.0000000000"),
            "CNY",
            "close",
            "unadjusted",
            MARKET,
            MARKET + timedelta(minutes=5),
            "exchange",
            A64,
        )
    )
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
    draft = drafts.create(project.id)
    draft = drafts.save(
        project.id,
        expected_lock_version=draft.lock_version,
        patch={
            "mandate_id": mandate.id,
            "scope_id": scope.id,
            "agenda_id": agenda.id,
            "historical_basis_id": basis.id,
            "price_snapshot_ids": (price.id,),
            "capital_structure_snapshot_id": capital.id,
            "security_rights_ids": (rights.id,),
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
    assert preview.manifest["project_id"] == str(summary.project_id)

    _ready_graph(session, suffix="later")
    assert (
        ResearchRevisionDiffService(session).revision_summary(published.id) == summary
    )


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
