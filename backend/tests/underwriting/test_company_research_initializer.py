from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.models import (
    UnderwritingMandateVersion,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)

NOW = datetime(2026, 8, 25, 9, tzinfo=UTC)


def _initializer(session) -> tuple[CompanyResearchInitializer, object]:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    return CompanyResearchInitializer(session, now=lambda: NOW), loaded.objects[
        "US:ALPHABET:COMPANY"
    ]


def _seeded_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-initializer.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as seed:
        ProductFoundationFixtureService(seed, now=lambda: NOW).load(
            load_product_foundation_fixture()
        )
        seed.commit()
    return engine, sessions


def _fresh_initializer(session) -> tuple[CompanyResearchInitializer, object]:
    alphabet = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "US:ALPHABET:COMPANY"
        )
    )
    assert alphabet is not None
    return CompanyResearchInitializer(session, now=lambda: NOW), alphabet


def _assert_no_initialized_foundation(session) -> None:
    """The initializer must leave no partial durable foundation on failure."""
    for model in (
        UnderwritingResearchProject,
        UnderwritingResearchProjectSecurity,
        UnderwritingMandateVersion,
        UnderwritingResearchScopeVersion,
        UnderwritingResearchAgendaVersion,
        UnderwritingWorkspaceDraft,
        CompanyResearchPreparation,
        CompanyResearchEvent,
        Job,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0


def test_preview_is_read_only_and_resolves_all_effective_alphabet_securities(
    session,
) -> None:
    initializer, alphabet = _initializer(session)
    before = session.scalar(
        select(func.count()).select_from(UnderwritingResearchProject)
    )

    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)

    assert preview.company.external_key == "US:ALPHABET:COMPANY"
    assert preview.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == before
    )
    assert not session.new


def test_initialize_creates_the_complete_company_research_foundation(session) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)

    result = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="alphabet-initialization-1",
    )

    assert result.project.primary_company_id == alphabet.id
    assert result.preparation.project_id == result.project.id
    assert result.preparation.request_hash == preview.input_hash
    assert result.draft.content.mandate_id == result.mandate.id
    assert result.draft.content.scope_id == result.scope.id
    assert result.draft.content.agenda_id == result.agenda.id
    assert result.draft.content.price_snapshot_ids == ()
    assert result.draft.content.fx_snapshot_ids == ()
    assert result.draft.content.capital_structure_snapshot_id is None
    assert result.draft.content.security_rights_ids == ()
    assert result.job.target_id == result.preparation.id
    assert result.job.research_case_id is None
    assert [
        event.event_type
        for event in session.scalars(
            select(CompanyResearchEvent).where(
                CompanyResearchEvent.preparation_id == result.preparation.id
            )
        )
    ] == ["initialized"]
    assert (
        session.scalar(select(func.count()).select_from(CompanyResearchPreparation))
        == 1
    )


def test_initialize_replays_the_original_foundation_for_the_same_key(session) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
    first = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="alphabet-replay",
    )

    replay = initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="alphabet-replay",
    )

    assert replay.project.id == first.project.id
    assert replay.preparation.id == first.preparation.id
    assert replay.job.id == first.job.id


def test_initialize_recovers_a_lost_response_in_a_fresh_session(tmp_path) -> None:
    engine, sessions = _seeded_session_factory(tmp_path)
    try:
        with sessions() as first_session:
            initializer, alphabet = _fresh_initializer(first_session)
            preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
            created = initializer.initialize(
                preview_hash=preview.input_hash,
                company_id=alphabet.id,
                cutoff_at=NOW,
                idempotency_key="alphabet-fresh-session-replay",
            )
            created_project_id = created.project.id
            created_preparation_id = created.preparation.id
            first_session.commit()

        with sessions() as replay_session:
            initializer, alphabet = _fresh_initializer(replay_session)
            preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
            replay = initializer.initialize(
                preview_hash=preview.input_hash,
                company_id=alphabet.id,
                cutoff_at=NOW,
                idempotency_key="alphabet-fresh-session-replay",
            )

            assert replay.project.id == created_project_id
            assert replay.preparation.id == created_preparation_id
            assert (
                replay_session.scalar(
                    select(func.count()).select_from(UnderwritingResearchProject)
                )
                == 1
            )
            assert (
                replay_session.scalar(
                    select(func.count()).select_from(CompanyResearchPreparation)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_concurrent_different_keys_converge_or_conflict_without_raw_sqlite_error(
    tmp_path,
) -> None:
    engine, sessions = _seeded_session_factory(tmp_path)
    barrier = Barrier(2)

    def initialize(key: str):
        with sessions() as worker:
            initializer, alphabet = _fresh_initializer(worker)
            preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
            barrier.wait()
            try:
                result = initializer.initialize(
                    preview_hash=preview.input_hash,
                    company_id=alphabet.id,
                    cutoff_at=NOW,
                    idempotency_key=key,
                )
                worker.commit()
                return ("created", result.project.id, result.preparation.id)
            except ConflictError:
                worker.rollback()
                return ("conflict", None, None)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(
                pool.map(initialize, ("alphabet-concurrent-a", "alphabet-concurrent-b"))
            )

        assert {outcome[0] for outcome in outcomes} == {"created", "conflict"}
        with sessions() as observer:
            assert (
                observer.scalar(
                    select(func.count()).select_from(UnderwritingResearchProject)
                )
                == 1
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(CompanyResearchPreparation)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_initialize_rejects_an_idempotency_key_reused_for_a_different_request(
    session,
) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
    initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="alphabet-conflict",
    )
    later = NOW + timedelta(days=1)
    later_preview = initializer.preview(company_id=alphabet.id, cutoff_at=later)

    with pytest.raises(ConflictError, match="idempotency_key"):
        initializer.initialize(
            preview_hash=later_preview.input_hash,
            company_id=alphabet.id,
            cutoff_at=later,
            idempotency_key="alphabet-conflict",
        )


def test_preview_rejects_a_security_id_as_a_company(session) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
    security_id = preview.securities[0].object_id

    with pytest.raises(ValidationError, match="Company"):
        initializer.preview(company_id=security_id, cutoff_at=NOW)


def test_caller_rollback_removes_every_initialized_product_row(session) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
    initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="alphabet-rollback",
    )

    session.rollback()

    _assert_no_initialized_foundation(session)


@pytest.mark.parametrize("failing_stage", ("project_creation", "job_add", "job_flush"))
def test_initialize_rolls_back_a_partial_foundation_at_write_boundaries(
    session, monkeypatch, failing_stage: str
) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)

    def fail_after_project_creation(*args, **kwargs):
        original_create_project(*args, **kwargs)
        raise RuntimeError("injected project creation failure")

    def fail_after_job_add(instance, **kwargs):
        original_add(instance, **kwargs)
        if isinstance(instance, Job):
            raise RuntimeError("injected job add failure")

    def fail_after_job_flush(objects=None):
        original_flush(objects)
        if objects and any(isinstance(value, Job) for value in objects):
            raise RuntimeError("injected job flush failure")

    original_create_project = initializer._products.create_project
    original_add = session.add
    original_flush = session.flush
    target, attribute, replacement = {
        "project_creation": (
            initializer._products,
            "create_project",
            fail_after_project_creation,
        ),
        "job_add": (session, "add", fail_after_job_add),
        "job_flush": (session, "flush", fail_after_job_flush),
    }[failing_stage]
    monkeypatch.setattr(target, attribute, replacement)

    with pytest.raises(RuntimeError, match="injected .* failure"):
        initializer.initialize(
            preview_hash=preview.input_hash,
            company_id=alphabet.id,
            cutoff_at=NOW,
            idempotency_key=f"alphabet-write-boundary-{failing_stage}",
        )

    _assert_no_initialized_foundation(session)


@pytest.mark.parametrize(
    "failing_method",
    (
        "append_product_mandate",
        "append_scope",
        "append_agenda",
        "create_draft",
        "add_preparation",
        "append_event",
    ),
)
def test_initialize_rolls_back_all_prior_stages_when_one_stage_fails(
    session, monkeypatch, failing_method: str
) -> None:
    initializer, alphabet = _initializer(session)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected stage failure")

    target = {
        "append_product_mandate": initializer._products,
        "append_scope": initializer._products,
        "append_agenda": initializer._products,
        "create_draft": initializer._drafts,
        "add_preparation": initializer._company_repository,
        "append_event": initializer._company_repository,
    }[failing_method]
    attribute = "create" if failing_method == "create_draft" else failing_method
    monkeypatch.setattr(target, attribute, fail)

    with pytest.raises(RuntimeError, match="injected stage failure"):
        initializer.initialize(
            preview_hash=preview.input_hash,
            company_id=alphabet.id,
            cutoff_at=NOW,
            idempotency_key=f"alphabet-stage-{failing_method}",
        )

    _assert_no_initialized_foundation(session)
