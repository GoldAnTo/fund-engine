from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import (
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.product_models import UnderwritingResearchProject
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

    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(CompanyResearchPreparation))
        == 0
    )


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

    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchProject))
        == 0
    )
    assert (
        session.scalar(select(func.count()).select_from(CompanyResearchPreparation))
        == 0
    )
