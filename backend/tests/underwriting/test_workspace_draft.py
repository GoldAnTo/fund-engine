from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.underwriting.persistence import product_repository as product_repository_module
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchProject,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)

NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
LATER = datetime(2026, 8, 24, 10, tzinfo=UTC)
A64 = "a" * 64


@pytest.fixture
def service(session) -> WorkspaceDraftService:
    return WorkspaceDraftService(session, now=lambda: NOW)


def _project(session, key: str = "company:catl") -> UnderwritingResearchProject:
    company = UnderwritingResearchObject(
        kind="company",
        external_key=f"{key}:{uuid4()}",
        canonical_name=key,
        created_at=NOW,
    )
    session.add(company)
    session.flush()
    project = UnderwritingResearchProject(
        primary_company_id=company.id,
        content_hash=A64,
        created_at=NOW,
    )
    session.add(project)
    session.flush()
    return project


def _empty_content() -> dict[str, object]:
    return {
        "schema_version": "product.workspace-draft.v1",
        "publication_status": "draft",
        "mandate_id": None,
        "scope_id": None,
        "agenda_id": None,
        "historical_basis_id": None,
        "price_snapshot_ids": [],
        "fx_snapshot_ids": [],
        "capital_structure_snapshot_id": None,
        "security_rights_ids": [],
        "user_focus": None,
    }


def test_create_requires_project_and_builds_one_complete_draft(
    session, service
) -> None:
    project = _project(session)

    created = service.create(project.id)
    repeated = service.create(project.id)

    assert repeated == created
    assert created.project_id == project.id
    assert created.lock_version == 1
    assert created.base_revision_id is None
    assert created.created_at == NOW
    assert created.updated_at == NOW
    assert created.content.model_dump(mode="json") == _empty_content()
    assert (
        session.scalar(
            select(UnderwritingWorkspaceDraft).where(
                UnderwritingWorkspaceDraft.project_id == project.id
            )
        )
        is not None
    )


def test_create_can_atomically_seed_the_known_foundation_references(
    session, service
) -> None:
    project = _project(session)
    content = WorkspaceDraftContent(
        mandate_id=uuid4(),
        scope_id=uuid4(),
        agenda_id=uuid4(),
    )

    created = service.create(project.id, initial_content=content)

    assert created.content == content


def test_create_rejects_unknown_project_and_an_unaware_clock(session) -> None:
    service = WorkspaceDraftService(session, now=lambda: datetime(2026, 8, 24, 9))
    project = _project(session)

    with pytest.raises(ValidationError, match="clock.*timezone-aware"):
        service.create(project.id)
    with pytest.raises(ValidationError, match="project does not exist"):
        WorkspaceDraftService(session, now=lambda: NOW).create(uuid4())


def test_patch_contract_forbids_uncontrolled_or_formal_fields() -> None:
    for patch in (
        {"arbitrary": {"nested": "value"}},
        {"assessment": {"direction": "provisional_bullish"}},
        {"status": "complete"},
        {"publication_status": "user_frozen"},
        {"base_revision_id": uuid4()},
    ):
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            WorkspaceDraftPatch.model_validate(patch)


def test_patch_contract_rejects_coerced_ids_and_non_text_focus() -> None:
    identifier = uuid4()
    invalid_patches = (
        {"mandate_id": str(identifier)},
        {"mandate_id": True},
        {"price_snapshot_ids": (identifier, str(uuid4()))},
        {"price_snapshot_ids": "not-a-tuple"},
        {"user_focus": True},
    )
    for patch in invalid_patches:
        with pytest.raises(PydanticValidationError):
            WorkspaceDraftPatch.model_validate(patch)


def test_patch_canonicalizes_reference_tuples_and_trims_focus() -> None:
    first = UUID("00000000-0000-0000-0000-000000000002")
    second = UUID("00000000-0000-0000-0000-000000000001")

    patch = WorkspaceDraftPatch.model_validate(
        {
            "price_snapshot_ids": (first, second, first),
            "security_rights_ids": (first, second),
            "user_focus": "  overseas growth  ",
        }
    )

    assert patch.price_snapshot_ids == (second, first)
    assert patch.security_rights_ids == (second, first)
    assert patch.user_focus == "overseas growth"
    with pytest.raises(PydanticValidationError, match="must not be empty"):
        WorkspaceDraftPatch.model_validate({"user_focus": "   "})


def test_save_distinguishes_missing_from_explicit_clear(session, service) -> None:
    project = _project(session)
    service.create(project.id)
    mandate_id = uuid4()
    price_id = uuid4()

    first = service.save(
        project.id,
        expected_lock_version=1,
        patch={
            "mandate_id": mandate_id,
            "price_snapshot_ids": (price_id,),
            "user_focus": "  overseas  ",
        },
    )
    cleared = service.save(
        project.id,
        expected_lock_version=2,
        patch={"mandate_id": None, "price_snapshot_ids": None},
    )

    assert first.content.mandate_id == mandate_id
    assert cleared.content.mandate_id is None
    assert cleared.content.price_snapshot_ids == ()
    assert cleared.content.user_focus == "overseas"
    assert cleared.lock_version == 3


def test_save_is_one_statement_compare_and_swap(session, service, engine) -> None:
    project = _project(session)
    service.create(project.id)
    updates: list[str] = []

    def record_update(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith("UPDATE UW_WORKSPACE_DRAFTS"):
            updates.append(statement)

    event.listen(engine, "before_cursor_execute", record_update)
    try:
        saved = service.save(
            project.id,
            expected_lock_version=1,
            patch={"user_focus": "storage"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_update)

    assert saved.lock_version == 2
    assert len(updates) == 1
    normalized = updates[0].lower()
    assert "project_id" in normalized
    assert "lock_version" in normalized


def test_stale_draft_save_is_rejected_without_losing_winner(session, service) -> None:
    project = _project(session)
    service.create(project.id)
    saved = service.save(
        project.id,
        expected_lock_version=1,
        patch={"user_focus": "overseas"},
    )

    with pytest.raises(ConflictError, match="draft changed"):
        service.save(
            project.id,
            expected_lock_version=1,
            patch={"user_focus": "storage"},
        )

    current = service.read(project.id)
    assert current is not None
    assert current.lock_version == 2
    assert current.content.user_focus == "overseas"
    assert saved == current


def test_read_and_save_are_exactly_project_scoped(session, service) -> None:
    first = _project(session, "company:first")
    second = _project(session, "company:second")
    first_draft = service.create(first.id)
    second_draft = service.create(second.id)

    service.save(
        first.id,
        expected_lock_version=1,
        patch={"user_focus": "first only"},
    )

    untouched = service.read(second.id)
    assert service.read(uuid4()) is None
    assert untouched is not None
    assert untouched.id == second_draft.id
    assert untouched.content.user_focus is None
    assert first_draft.id != second_draft.id
    with pytest.raises(ValidationError, match="workspace draft does not exist"):
        service.save(uuid4(), expected_lock_version=1, patch={"user_focus": "unknown"})


@pytest.mark.parametrize("invalid", [True, False, 0, -1, -9, "1", 1.0, None])
def test_save_rejects_invalid_lock_versions(session, service, invalid) -> None:
    project = _project(session)
    service.create(project.id)

    with pytest.raises(ValidationError, match="lock_version"):
        service.save(
            project.id,
            expected_lock_version=invalid,
            patch={"user_focus": "valid"},
        )


def test_save_rejects_malformed_existing_content_without_writing(
    session, service
) -> None:
    project = _project(session)
    service.create(project.id)
    session.execute(
        update(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == project.id)
        .values(content={**_empty_content(), "direction": "provisional_bullish"})
    )
    session.flush()

    with pytest.raises(ValidationError, match="workspace draft content is invalid"):
        service.save(
            project.id,
            expected_lock_version=1,
            patch={"user_focus": "should not persist"},
        )

    row = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == project.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    assert row.lock_version == 1


def test_normal_save_cannot_change_base_revision(session, service) -> None:
    project = _project(session)
    created = service.create(project.id)
    basis = UnderwritingHistoricalBasis(
        cutoff=NOW,
        price_as_of=None,
        source_manifest_hash=A64,
        definition_bundle_hash=A64,
        parser_bundle_hash=A64,
        boundary_schema_version="product.historical-basis.v1",
        content_hash=A64,
        created_at=NOW,
    )
    session.add(basis)
    session.flush()
    revision = UnderwritingResearchVersion(
        object_id=project.primary_company_id,
        basis_id=basis.id,
        version_kind="research_revision",
        sequence=1,
        content_hash=A64,
        parent_ids=[],
        project_id=project.id,
        publication_status="user_frozen",
        created_at=NOW,
    )
    session.add(revision)
    session.flush()
    repository = ProductRepository(session)
    reset = repository.compare_and_swap_workspace_draft(
        project_id=project.id,
        expected_lock_version=created.lock_version,
        content=created.content.model_dump(mode="json"),
        updated_at=LATER,
        base_revision_id=revision.id,
    )

    saved = WorkspaceDraftService(session, now=lambda: LATER).save(
        project.id,
        expected_lock_version=reset.lock_version,
        patch={"user_focus": "preserve base"},
    )

    assert saved.base_revision_id == revision.id


def test_internal_base_reset_accepts_only_an_owned_revision_or_none(
    session, service
) -> None:
    project = _project(session, "company:owner")
    other_project = _project(session, "company:other")
    created = service.create(project.id)

    def revision_for(
        target: UnderwritingResearchProject | None,
        *,
        kind: str,
    ) -> UnderwritingResearchVersion:
        basis = UnderwritingHistoricalBasis(
            cutoff=NOW,
            price_as_of=None,
            source_manifest_hash=A64,
            definition_bundle_hash=A64,
            parser_bundle_hash=A64,
            boundary_schema_version="product.historical-basis.v1",
            content_hash=A64,
            created_at=NOW,
        )
        session.add(basis)
        session.flush()
        row = UnderwritingResearchVersion(
            object_id=(target or project).primary_company_id,
            basis_id=basis.id,
            version_kind=kind,
            sequence=1,
            content_hash=A64,
            parent_ids=[],
            project_id=target.id if target is not None else None,
            publication_status="user_frozen" if target is not None else None,
            created_at=NOW,
        )
        session.add(row)
        session.flush()
        return row

    owned = revision_for(project, kind="owned-revision")
    foreign = revision_for(other_project, kind="foreign-revision")
    legacy = revision_for(None, kind="legacy-revision")
    repository = ProductRepository(session)
    content = created.content.model_dump(mode="json")

    for invalid in (
        str(owned.id),
        True,
        1,
        object(),
        product_repository_module._UnchangedBaseRevision(),
        uuid4(),
        foreign.id,
        legacy.id,
    ):
        with pytest.raises(ValidationError, match="base_revision_id"):
            repository.compare_and_swap_workspace_draft(
                project_id=project.id,
                expected_lock_version=1,
                content=content,
                updated_at=NOW,
                base_revision_id=invalid,
            )
        assert session.scalar(select(UnderwritingResearchProject.id)) is not None

    reset = repository.compare_and_swap_workspace_draft(
        project_id=project.id,
        expected_lock_version=1,
        content=content,
        updated_at=NOW,
        base_revision_id=owned.id,
    )
    assert reset.base_revision_id == owned.id
    cleared = repository.compare_and_swap_workspace_draft(
        project_id=project.id,
        expected_lock_version=2,
        content=content,
        updated_at=NOW,
        base_revision_id=None,
    )
    assert cleared.base_revision_id is None


def test_service_rejects_timestamp_regression_and_allows_equal_time(
    session, service
) -> None:
    project = _project(session)
    service.create(project.id)

    regressing = WorkspaceDraftService(
        session, now=lambda: NOW - timedelta(microseconds=1)
    )
    with pytest.raises(ValidationError, match="updated_at.*earlier"):
        regressing.save(
            project.id,
            expected_lock_version=1,
            patch={"user_focus": "regression"},
        )

    current = service.read(project.id)
    assert current is not None
    assert current.lock_version == 1
    equal = service.save(
        project.id,
        expected_lock_version=1,
        patch={"user_focus": "equal is valid"},
    )
    assert equal.lock_version == 2
    assert equal.updated_at == NOW


def test_timestamp_monotonicity_compares_dst_instants_not_wall_clock(session) -> None:
    zone = ZoneInfo("America/New_York")
    first_instant = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    later_instant_with_earlier_wall_time = datetime(
        2026, 11, 1, 1, 15, tzinfo=zone, fold=1
    )
    project = _project(session)
    WorkspaceDraftService(session, now=lambda: first_instant).create(project.id)

    saved = WorkspaceDraftService(
        session, now=lambda: later_instant_with_earlier_wall_time
    ).save(
        project.id,
        expected_lock_version=1,
        patch={"user_focus": "later absolute instant"},
    )

    assert saved.updated_at == later_instant_with_earlier_wall_time.astimezone(UTC)


def test_repository_cas_rejects_an_older_timestamp_even_with_current_lock(
    session, service
) -> None:
    project = _project(session)
    created = service.create(project.id)

    with pytest.raises(ConflictError, match="draft changed"):
        ProductRepository(session).compare_and_swap_workspace_draft(
            project_id=project.id,
            expected_lock_version=created.lock_version,
            content=created.content.model_dump(mode="json"),
            updated_at=NOW - timedelta(seconds=1),
        )

    current = service.read(project.id)
    assert current is not None
    assert current.lock_version == 1
    assert current.updated_at == NOW


def test_repository_cas_normalizes_dst_fold_before_sqlite_comparison(session) -> None:
    zone = ZoneInfo("America/New_York")
    existing_instant = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    later_fold_instant = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    project = _project(session)
    created = WorkspaceDraftService(session, now=lambda: existing_instant).create(
        project.id
    )

    saved = ProductRepository(session).compare_and_swap_workspace_draft(
        project_id=project.id,
        expected_lock_version=created.lock_version,
        content=created.content.model_dump(mode="json"),
        updated_at=later_fold_instant,
    )

    assert saved.lock_version == 2
    assert WorkspaceDraftService._stored_utc(saved.updated_at) == datetime(
        2026, 11, 1, 6, 15, tzinfo=UTC
    )


def test_repository_cas_accepts_the_same_instant_with_another_offset(session) -> None:
    instant = datetime(2026, 11, 1, 6, 15, tzinfo=UTC)
    same_instant = datetime(2026, 11, 1, 8, 15, tzinfo=timezone(timedelta(hours=2)))
    project = _project(session)
    created = WorkspaceDraftService(session, now=lambda: instant).create(project.id)

    saved = ProductRepository(session).compare_and_swap_workspace_draft(
        project_id=project.id,
        expected_lock_version=created.lock_version,
        content=created.content.model_dump(mode="json"),
        updated_at=same_instant,
    )

    assert saved.lock_version == 2
    assert WorkspaceDraftService._stored_utc(saved.updated_at) == instant


@pytest.mark.parametrize(
    "invalid", [datetime(2026, 11, 1, 6, 15), "2026-11-01T06:15:00Z", True, None]
)
def test_repository_cas_rejects_non_aware_update_times(
    session, service, invalid
) -> None:
    project = _project(session)
    created = service.create(project.id)

    with pytest.raises(ValidationError, match="updated_at.*timezone-aware"):
        ProductRepository(session).compare_and_swap_workspace_draft(
            project_id=project.id,
            expected_lock_version=created.lock_version,
            content=created.content.model_dump(mode="json"),
            updated_at=invalid,
        )

    assert session.scalar(select(UnderwritingResearchProject.id)) is not None


def test_service_results_do_not_alias_input_or_expose_mutable_content(
    session, service
) -> None:
    project = _project(session)
    service.create(project.id)
    references = [uuid4(), uuid4()]
    saved = service.save(
        project.id,
        expected_lock_version=1,
        patch={"price_snapshot_ids": tuple(references)},
    )
    references.clear()

    assert len(saved.content.price_snapshot_ids) == 2
    with pytest.raises(PydanticValidationError, match="frozen"):
        saved.content.user_focus = "mutated"  # type: ignore[misc]


def test_repository_recovers_only_project_unique_create_races(session) -> None:
    first_project = _project(session, "company:first")
    second_project = _project(session, "company:second")
    repository = ProductRepository(session)
    first_id = uuid4()
    created = repository.create_workspace_draft(
        draft_id=first_id,
        project_id=first_project.id,
        content=_empty_content(),
        created_at=NOW,
    )

    recovered = repository.create_workspace_draft(
        draft_id=uuid4(),
        project_id=first_project.id,
        content=_empty_content(),
        created_at=NOW,
    )

    assert recovered.id == created.id
    session.expunge(recovered)
    with pytest.raises(IntegrityError):
        repository.create_workspace_draft(
            draft_id=first_id,
            project_id=second_project.id,
            content=_empty_content(),
            created_at=NOW,
        )
    assert session.get(UnderwritingResearchProject, second_project.id) is second_project


def test_repository_does_not_misclassify_another_project_id_constraint() -> None:
    error = IntegrityError(
        "INSERT",
        {},
        sqlite3.IntegrityError("UNIQUE constraint failed: other_table.project_id"),
    )

    assert not ProductRepository._is_workspace_draft_project_error(error)


def test_two_sessions_cannot_overwrite_the_same_lock_version(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'draft-cas.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    project = _project(seed)
    WorkspaceDraftService(seed, now=lambda: NOW).create(project.id)
    project_id = project.id
    seed.commit()
    seed.close()
    first = sessions()
    second = sessions()
    try:
        first_service = WorkspaceDraftService(first, now=lambda: LATER)
        second_service = WorkspaceDraftService(second, now=lambda: LATER)
        first_loaded = first_service.read(project_id)
        second_loaded = second_service.read(project_id)
        assert first_loaded is not None and second_loaded is not None
        assert first_loaded.lock_version == second_loaded.lock_version == 1

        first_service.save(
            project_id,
            expected_lock_version=first_loaded.lock_version,
            patch={"user_focus": "winner"},
        )
        first.commit()

        with pytest.raises(ConflictError, match="draft changed"):
            second_service.save(
                project_id,
                expected_lock_version=second_loaded.lock_version,
                patch={"user_focus": "loser"},
            )
        assert second.scalar(select(UnderwritingResearchProject.id)) is not None
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_two_sessions_that_observe_no_draft_converge_on_one_create(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'draft-create.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    seed = sessions()
    project_id = _project(seed).id
    seed.commit()
    seed.close()
    first = sessions()
    second = sessions()
    try:
        first_repository = ProductRepository(first)
        second_repository = ProductRepository(second)
        assert first_repository.workspace_draft(project_id) is None
        assert second_repository.workspace_draft(project_id) is None

        winner = first_repository.create_workspace_draft(
            draft_id=uuid4(),
            project_id=project_id,
            content=_empty_content(),
            created_at=NOW,
        )
        first.commit()
        recovered = second_repository.create_workspace_draft(
            draft_id=uuid4(),
            project_id=project_id,
            content=_empty_content(),
            created_at=NOW,
        )

        assert recovered.id == winner.id
        assert second.scalar(select(UnderwritingResearchProject.id)) == project_id
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_two_session_create_race_if_configured(session, engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("TEST_DATABASE_URL is not configured")
    project_id = _project(session).id
    session.commit()
    sessions = sessionmaker(bind=engine, future=True)
    first = sessions()
    second = sessions()
    try:
        first_repository = ProductRepository(first)
        second_repository = ProductRepository(second)
        assert first_repository.workspace_draft(project_id) is None
        assert second_repository.workspace_draft(project_id) is None
        winner = first_repository.create_workspace_draft(
            draft_id=uuid4(),
            project_id=project_id,
            content=_empty_content(),
            created_at=NOW,
        )
        first.commit()
        recovered = second_repository.create_workspace_draft(
            draft_id=uuid4(),
            project_id=project_id,
            content=_empty_content(),
            created_at=NOW,
        )
        assert recovered.id == winner.id
        assert second.scalar(select(UnderwritingResearchProject.id)) == project_id
    finally:
        first.close()
        second.close()


def test_repository_centralizes_sqlite_savepoints_and_unique_parsing() -> None:
    source = inspect.getsource(product_repository_module)

    assert source.count('exec_driver_sql("BEGIN")') == 1
    assert source.count('marker = "unique constraint failed:"') == 1


def test_create_race_savepoint_does_not_commit_the_outer_transaction(
    session, engine
) -> None:
    project = _project(session)
    repository = ProductRepository(session)
    repository.create_workspace_draft(
        draft_id=uuid4(),
        project_id=project.id,
        content=_empty_content(),
        created_at=NOW,
    )
    repository.create_workspace_draft(
        draft_id=uuid4(),
        project_id=project.id,
        content=_empty_content(),
        created_at=NOW,
    )

    session.rollback()

    with engine.connect() as connection:
        assert connection.scalar(select(UnderwritingWorkspaceDraft.id)) is None


def test_successful_save_remains_in_the_callers_transaction(session, engine) -> None:
    project = _project(session)
    service = WorkspaceDraftService(session, now=lambda: NOW)
    service.create(project.id)
    service.save(
        project.id,
        expected_lock_version=1,
        patch={"user_focus": "uncommitted"},
    )

    session.rollback()

    with engine.connect() as connection:
        assert connection.scalar(select(UnderwritingWorkspaceDraft.id)) is None


def test_workspace_drafts_have_no_delete_operation(session, service) -> None:
    assert not hasattr(service, "delete")
    assert not hasattr(ProductRepository(session), "delete_workspace_draft")
