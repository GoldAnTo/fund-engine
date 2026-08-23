"""Repository governance for append-only evidence candidate dossiers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread, get_ident
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import Base, ValidationError
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceDossier,
    CandidateEvidenceItem,
    CandidateEvidenceReview,
    CandidateEvidenceStatus,
)
from app.underwriting.domain.types import HistoricalBasisInput
from app.underwriting.persistence.repository import StaleParentError, UnderwritingRepository
from app.underwriting.persistence.research_models import (
    UnderwritingEvidenceCandidateDossierVersion,
    UnderwritingEvidenceCandidateReviewVersion,
    UnderwritingSourceManifestVersion,
    begin_candidate_sqlite_write,
    require_candidate_write_read_committed,
)
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_freeze import freeze_manifest


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def _manifest_payload(*, locator: str = "https://example.test/catl-2024-ar", available_at: datetime = NOW) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "sources": [{
            "source_id": "catl-2024-ar",
            "title": "CATL annual report",
            "locator": locator,
            "published_at": NOW.isoformat(),
            "first_available_at": available_at.isoformat(),
            "retrieved_at": NOW.isoformat(),
            "content_sha256": "a" * 64,
            "authority": "issuer_filing",
            "authorization": "authorized",
            "display_policy": "derived_only",
            "provider_capability": "public_http",
            "retention": "hash_locator_and_derived_observations",
        }],
    }


@pytest.fixture
def repository(session: Session) -> UnderwritingResearchRepository:
    return UnderwritingResearchRepository(session)


@pytest.fixture
def kernel(session: Session) -> UnderwritingRepository:
    return UnderwritingRepository(session)


@pytest.fixture
def company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)


@pytest.fixture
def other_company(kernel: UnderwritingRepository):
    return kernel.add_object("company", "US:GOOGL:COMPANY", "Alphabet", NOW)


@pytest.fixture
def basis(kernel: UnderwritingRepository):
    manifest = freeze_manifest(_manifest_payload(), NOW)
    return kernel.add_basis(HistoricalBasisInput(NOW, NOW, manifest.manifest_hash), NOW)


@pytest.fixture
def manifest(repository: UnderwritingResearchRepository, basis):
    payload = _manifest_payload()
    frozen = freeze_manifest(payload, NOW)
    return repository.add_source_manifest(
        manifest_key="catl-evidence", basis_id=basis.id, manifest=payload,
        manifest_hash=frozen.manifest_hash, content_hash=canonical_hash(payload),
        expected_parent_id=None, created_at=NOW,
    )


def _dossier_payload(*, object_id: UUID, basis_id: UUID, source_manifest_id: UUID, source_manifest_hash: str, version: int = 1, supersedes_id: UUID | None = None, source_id: str = "catl-2024-ar", source_locator: str = "https://example.test/catl-2024-ar", available_at: datetime = NOW) -> dict[str, object]:
    item = CandidateEvidenceItem(
        metric_key="industry.capacity", status=CandidateEvidenceStatus.SOURCE_REPORTED,
        value=Decimal("100"), unit="GWh", observed_start=NOW - timedelta(days=365),
        observed_end=NOW, available_at=available_at, source_id=source_id,
        source_locator=source_locator, scope_statement="Global battery capacity.",
        exclusions=("No investment conclusion.",), methodology="Direct transcription.",
        prohibited_splicing_declaration="No source splicing.",
    )
    return {
        "object_id": str(object_id), "basis_id": str(basis_id),
        "source_manifest_id": str(source_manifest_id), "dossier_key": "industry-capacity",
        "version": version, "scope_statement": "Candidate industry evidence only.",
        "status": "candidate", "purpose": "evidence_candidate",
        "items": [item.canonical_payload], "rejected_calculations": ["No valuation model."],
        "source_manifest_hash": source_manifest_hash, "created_at": NOW.isoformat(),
        "supersedes_id": None if supersedes_id is None else str(supersedes_id),
    }


def _append_dossier(repository: UnderwritingResearchRepository, company, basis, manifest, *, payload: dict[str, object] | None = None, expected_parent_id: UUID | None = None):
    dossier_payload = payload or _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    )
    return repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="industry-capacity", payload=dossier_payload, created_at=NOW,
        expected_parent_id=expected_parent_id,
    )


def _review_payload(*, dossier_id: UUID, dossier_content_hash: str, reviewer_identity: str, reviewer_role: str, decision: str = "approve") -> dict[str, object]:
    return {
        "dossier_id": str(dossier_id), "dossier_content_hash": dossier_content_hash,
        "reviewer_identity": reviewer_identity, "reviewer_role": reviewer_role,
        "decision": decision, "rationale": "Evidence is bounded and traceable.",
        "reviewed_at": NOW.isoformat(),
    }


def _append_review(repository: UnderwritingResearchRepository, dossier, *, reviewer_identity: str, reviewer_role: str, dossier_content_hash: str | None = None):
    return repository.append_candidate_review(
        dossier_id=dossier.id, dossier_content_hash=dossier_content_hash or dossier.content_hash,
        reviewer_identity=reviewer_identity, reviewer_role=reviewer_role, decision="approve",
        payload=_review_payload(
            dossier_id=dossier.id, dossier_content_hash=dossier_content_hash or dossier.content_hash,
            reviewer_identity=reviewer_identity, reviewer_role=reviewer_role,
        ), created_at=NOW,
    )


def _seed_file_candidate_database(tmp_path, filename: str):
    engine = create_engine(
        f"sqlite:///{tmp_path / filename}", future=True,
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as bootstrap:
        kernel = UnderwritingRepository(bootstrap)
        repository = UnderwritingResearchRepository(bootstrap)
        company = kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)
        frozen = freeze_manifest(_manifest_payload(), NOW)
        basis = kernel.add_basis(HistoricalBasisInput(NOW, NOW, frozen.manifest_hash), NOW)
        manifest_payload = _manifest_payload()
        manifest = repository.add_source_manifest(
            manifest_key=f"{filename}-manifest", basis_id=basis.id,
            manifest=manifest_payload, manifest_hash=frozen.manifest_hash,
            content_hash=canonical_hash(manifest_payload), expected_parent_id=None,
            created_at=NOW,
        )
        bootstrap.commit()
        return engine, sessions, (company.id, basis.id, manifest.id, manifest.manifest_hash)


def test_postgresql_candidate_dossier_family_lock_is_stable_and_parameterized() -> None:
    statement, lock_id = UnderwritingResearchRepository.postgresql_candidate_dossier_family_lock(
        object_id=UUID("00000000-0000-0000-0000-000000000001"),
        basis_id=UUID("00000000-0000-0000-0000-000000000002"),
        dossier_key="industry-capacity",
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    assert -(2**63) <= lock_id < 2**63
    assert "pg_advisory_xact_lock" in str(compiled)
    assert compiled.params == {"candidate_dossier_family_lock_id": lock_id}


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_candidate_writes_reject_postgres_snapshot_isolation_before_family_reads(isolation) -> None:
    class FakeBind:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    class FakeConnection:
        def get_isolation_level(self):
            return isolation

    class FakeSession:
        def get_bind(self):
            return FakeBind()

        def connection(self):
            return FakeConnection()

    with pytest.raises(ValidationError, match="candidate writes require READ COMMITTED"):
        require_candidate_write_read_committed(FakeSession())


def test_candidate_writes_allow_postgres_read_committed_before_family_lock() -> None:
    class FakeBind:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    class FakeConnection:
        def get_isolation_level(self):
            return "READ COMMITTED"

    class FakeSession:
        def get_bind(self):
            return FakeBind()

        def connection(self):
            return FakeConnection()

    require_candidate_write_read_committed(FakeSession())


def test_same_identity_cannot_fill_both_approval_roles(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")

    with pytest.raises(ValidationError, match="different reviewer identities"):
        _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="methodology")


def test_same_role_cannot_be_filled_twice(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")

    with pytest.raises(ValidationError, match="already has a review for this role"):
        _append_review(repository, dossier, reviewer_identity="reviewer:b", reviewer_role="provenance")


def test_successor_cannot_reuse_prior_dossier_review(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    successor = _append_dossier(
        repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id,
    )

    assert repository.effective_candidate_reviews(successor.id) == []


def test_prior_review_remains_readable_after_successor_in_fresh_session(
    session, company, basis, manifest
) -> None:
    repository = UnderwritingResearchRepository(session)
    dossier = _append_dossier(repository, company, basis, manifest)
    review = _append_review(
        repository, dossier, reviewer_identity="reviewer:historical", reviewer_role="provenance",
    )
    _append_dossier(
        repository, company, basis, manifest,
        payload=_dossier_payload(
            object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
            source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
        ),
        expected_parent_id=dossier.id,
    )
    review_id = review.id
    engine = session.get_bind()
    session.commit()

    with sessionmaker(bind=engine, future=True)() as fresh:
        replayed = fresh.get(UnderwritingEvidenceCandidateReviewVersion, review_id)
        assert replayed is not None
        assert replayed.dossier_id == dossier.id


def test_review_rejects_dossier_that_has_been_superseded(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    successor_payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    _append_dossier(
        repository, company, basis, manifest, payload=successor_payload,
        expected_parent_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="dossier is no longer current"):
        _append_review(repository, dossier, reviewer_identity="reviewer:a", reviewer_role="provenance")


def test_fresh_sqlite_session_can_append_review_at_persisted_utc_cutoff(
    session, company, basis, manifest
) -> None:
    repository = UnderwritingResearchRepository(session)
    dossier = _append_dossier(repository, company, basis, manifest)
    engine = session.get_bind()
    session.commit()

    with sessionmaker(bind=engine, future=True)() as fresh:
        review = UnderwritingResearchRepository(fresh).append_candidate_review(
            dossier_id=dossier.id, dossier_content_hash=dossier.content_hash,
            reviewer_identity="reviewer:fresh", reviewer_role="provenance", decision="approve",
            payload=_review_payload(
                dossier_id=dossier.id, dossier_content_hash=dossier.content_hash,
                reviewer_identity="reviewer:fresh", reviewer_role="provenance",
            ), created_at=NOW,
        )
        fresh.commit()
        assert review.dossier_id == dossier.id


def test_invalid_repository_candidate_releases_sqlite_write_reservation(tmp_path) -> None:
    engine, sessions, (company_id, basis_id, manifest_id, manifest_hash) = _seed_file_candidate_database(
        tmp_path, "candidate-invalid-release.sqlite",
    )
    try:
        with sessions() as invalid_writer:
            repository = UnderwritingResearchRepository(invalid_writer)
            with pytest.raises(ValidationError, match="source_locator does not match"):
                repository.append_candidate_dossier(
                    object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                    dossier_key="industry-capacity",
                    payload=_dossier_payload(
                        object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                        source_manifest_hash=manifest_hash,
                        source_locator="https://example.test/forged",
                    ),
                    created_at=NOW, expected_parent_id=None,
                )
        with sessions() as contender:
            begin_candidate_sqlite_write(contender)
            contender.rollback()
    finally:
        engine.dispose()


def test_repository_rejects_preopened_deferred_sqlite_transaction_before_reads(tmp_path) -> None:
    engine, sessions, (company_id, basis_id, manifest_id, manifest_hash) = _seed_file_candidate_database(
        tmp_path, "candidate-deferred-transaction.sqlite",
    )
    try:
        with sessions() as writer:
            writer.connection().exec_driver_sql("BEGIN")
            with pytest.raises(ValidationError, match="fresh SQLite transaction"):
                UnderwritingResearchRepository(writer).append_candidate_dossier(
                    object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                    dossier_key="industry-capacity",
                    payload=_dossier_payload(
                        object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                        source_manifest_hash=manifest_hash,
                    ),
                    created_at=NOW, expected_parent_id=None,
                )
            writer.rollback()
    finally:
        engine.dispose()


def test_fresh_session_reads_a_599_deep_valid_dossier_chain_without_recursion(tmp_path) -> None:
    engine, sessions, (company_id, basis_id, manifest_id, manifest_hash) = _seed_file_candidate_database(
        tmp_path, "candidate-deep-history.sqlite",
    )
    try:
        parent_id: UUID | None = None
        rows: list[dict[str, object]] = []
        for version in range(1, 600):
            contract = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
                object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                source_manifest_hash=manifest_hash, version=version, supersedes_id=parent_id,
            ))
            row_id = uuid4()
            rows.append({
                "id": row_id, "dossier_key": contract.dossier_key, "version": contract.version,
                "object_id": contract.object_id, "basis_id": contract.basis_id,
                "source_manifest_id": contract.source_manifest_id,
                "scope_statement": contract.scope_statement, "purpose": contract.purpose,
                "status": contract.status.value,
                "rejected_calculations": list(contract.rejected_calculations),
                "payload": contract.canonical_payload,
                "source_manifest_hash": contract.source_manifest_hash,
                "content_hash": contract.content_hash, "supersedes_id": contract.supersedes_id,
                "created_at": contract.created_at,
            })
            parent_id = row_id
        with engine.begin() as connection:
            connection.execute(UnderwritingEvidenceCandidateDossierVersion.__table__.insert(), rows)
        with sessions() as fresh:
            deepest = fresh.get(UnderwritingEvidenceCandidateDossierVersion, parent_id)
            assert deepest is not None
            assert deepest.version == 599
    finally:
        engine.dispose()


def test_direct_review_after_successor_is_rejected(session, company, basis, manifest) -> None:
    repository = UnderwritingResearchRepository(session)
    dossier = _append_dossier(repository, company, basis, manifest)
    successor = _append_dossier(
        repository, company, basis, manifest,
        payload=_dossier_payload(
            object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
            source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
        ), expected_parent_id=dossier.id,
    )
    assert successor.version == 2
    review_contract = CandidateEvidenceReview.from_canonical_payload(_review_payload(
        dossier_id=dossier.id, dossier_content_hash=dossier.content_hash,
        reviewer_identity="reviewer:direct", reviewer_role="provenance",
    ))
    session.add(UnderwritingEvidenceCandidateReviewVersion.from_contract(
        id=uuid4(), contract=review_contract,
    ))

    with pytest.raises(ValidationError, match="dossier is no longer current"):
        session.flush()


def test_direct_dossier_rejects_manifest_from_another_basis(session, kernel, company, basis, manifest) -> None:
    other_basis = kernel.add_basis(
        HistoricalBasisInput(NOW, NOW, manifest.manifest_hash), NOW,
    )
    contract = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
        object_id=company.id, basis_id=other_basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    ))
    session.add(UnderwritingEvidenceCandidateDossierVersion.from_contract(
        id=uuid4(), contract=contract,
    ))

    with pytest.raises(ValidationError, match="source manifest must belong"):
        session.flush()


def test_direct_dossier_rejects_source_locator_absent_from_manifest(session, company, basis, manifest) -> None:
    contract = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
        source_locator="https://example.test/catl-2024-ar#forged",
    ))
    session.add(UnderwritingEvidenceCandidateDossierVersion.from_contract(
        id=uuid4(), contract=contract,
    ))

    with pytest.raises(ValidationError, match="source_locator does not match"):
        session.flush()


def test_honest_direct_dossier_is_accepted(session, company, basis, manifest) -> None:
    contract = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    ))
    row = UnderwritingEvidenceCandidateDossierVersion.from_contract(id=uuid4(), contract=contract)
    session.add(row)
    session.flush()

    assert row.content_hash == contract.content_hash


def test_honest_direct_dossier_survives_file_backed_fresh_session(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'candidate-direct-fresh.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    try:
        with sessions() as bootstrap:
            kernel = UnderwritingRepository(bootstrap)
            repository = UnderwritingResearchRepository(bootstrap)
            company = kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)
            frozen = freeze_manifest(_manifest_payload(), NOW)
            basis = kernel.add_basis(HistoricalBasisInput(NOW, NOW, frozen.manifest_hash), NOW)
            manifest_payload = _manifest_payload()
            manifest = repository.add_source_manifest(
                manifest_key="candidate-direct-fresh", basis_id=basis.id,
                manifest=manifest_payload, manifest_hash=frozen.manifest_hash,
                content_hash=canonical_hash(manifest_payload), expected_parent_id=None,
                created_at=NOW,
            )
            bootstrap.commit()
            company_id, basis_id, manifest_id, manifest_hash = (
                company.id, basis.id, manifest.id, manifest.manifest_hash,
            )

        with sessions() as direct:
            contract = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
                object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                source_manifest_hash=manifest_hash,
            ))
            row = UnderwritingEvidenceCandidateDossierVersion.from_contract(
                id=uuid4(), contract=contract,
            )
            direct.add(row)
            direct.commit()
            row_id, content_hash = row.id, row.content_hash

        with sessions() as fresh_review:
            review = UnderwritingResearchRepository(fresh_review).append_candidate_review(
                dossier_id=row_id, dossier_content_hash=content_hash,
                reviewer_identity="reviewer:direct-fresh", reviewer_role="provenance",
                decision="approve",
                payload=_review_payload(
                    dossier_id=row_id, dossier_content_hash=content_hash,
                    reviewer_identity="reviewer:direct-fresh", reviewer_role="provenance",
                ),
                created_at=NOW,
            )
            fresh_review.commit()
            assert review.dossier_id == row_id

        with sessions() as fresh:
            restored = fresh.get(UnderwritingEvidenceCandidateDossierVersion, row_id)
            assert restored is not None
            assert restored.content_hash == content_hash
    finally:
        engine.dispose()


def test_raw_candidate_with_forged_manifest_locator_fails_closed_on_fresh_load(
    session, company, basis, manifest
) -> None:
    """Core/raw writes cannot bypass the source-locator closure on reload."""
    forged = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
        source_locator="https://example.test/catl-2024-ar#forged-by-core",
    ))
    row_id = uuid4()
    engine = session.get_bind()
    session.commit()

    with engine.begin() as connection:
        connection.execute(
            UnderwritingEvidenceCandidateDossierVersion.__table__.insert().values(
                id=row_id,
                dossier_key=forged.dossier_key,
                version=forged.version,
                object_id=forged.object_id,
                basis_id=forged.basis_id,
                source_manifest_id=forged.source_manifest_id,
                scope_statement=forged.scope_statement,
                purpose=forged.purpose,
                status=forged.status.value,
                rejected_calculations=list(forged.rejected_calculations),
                payload=forged.canonical_payload,
                source_manifest_hash=forged.source_manifest_hash,
                content_hash=forged.content_hash,
                supersedes_id=forged.supersedes_id,
                created_at=forged.created_at,
            )
        )

    with sessionmaker(bind=engine, future=True)() as fresh:
        with pytest.raises(ValidationError, match="source_locator does not match"):
            fresh.get(UnderwritingEvidenceCandidateDossierVersion, row_id)


def test_raw_dossier_with_noncanonical_predecessor_fails_closed_on_fresh_load(
    session, company, basis, manifest
) -> None:
    """A Core/raw successor must retain the same sealed family lineage."""
    malformed = CandidateEvidenceDossier.from_canonical_payload(_dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=uuid4(),
    ))
    row_id = uuid4()
    engine = session.get_bind()
    session.commit()

    with engine.begin() as connection:
        connection.execute(
            UnderwritingEvidenceCandidateDossierVersion.__table__.insert().values(
                id=row_id,
                dossier_key=malformed.dossier_key,
                version=malformed.version,
                object_id=malformed.object_id,
                basis_id=malformed.basis_id,
                source_manifest_id=malformed.source_manifest_id,
                scope_statement=malformed.scope_statement,
                purpose=malformed.purpose,
                status=malformed.status.value,
                rejected_calculations=list(malformed.rejected_calculations),
                payload=malformed.canonical_payload,
                source_manifest_hash=malformed.source_manifest_hash,
                content_hash=malformed.content_hash,
                supersedes_id=malformed.supersedes_id,
                created_at=malformed.created_at,
            )
        )

    with sessionmaker(bind=engine, future=True)() as fresh:
        with pytest.raises(ValidationError, match="dossier predecessor does not exist"):
            fresh.get(UnderwritingEvidenceCandidateDossierVersion, row_id)


def test_raw_prior_review_of_superseded_dossier_remains_readable_on_fresh_load(
    session, company, basis, manifest
) -> None:
    """Read-time integrity validates closure, not whether a review is still current."""
    repository = UnderwritingResearchRepository(session)
    dossier = _append_dossier(repository, company, basis, manifest)
    _append_dossier(
        repository, company, basis, manifest,
        payload=_dossier_payload(
            object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
            source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
        ),
        expected_parent_id=dossier.id,
    )
    review = CandidateEvidenceReview.from_canonical_payload(_review_payload(
        dossier_id=dossier.id, dossier_content_hash=dossier.content_hash,
        reviewer_identity="reviewer:raw-stale", reviewer_role="provenance",
    ))
    row_id = uuid4()
    engine = session.get_bind()
    session.commit()

    with engine.begin() as connection:
        connection.execute(
            UnderwritingEvidenceCandidateReviewVersion.__table__.insert().values(
                id=row_id,
                dossier_id=review.dossier_id,
                dossier_content_hash=review.dossier_content_hash,
                reviewer_identity=review.reviewer_identity,
                reviewer_role=review.reviewer_role,
                decision=review.decision,
                rationale=review.rationale,
                payload=review.canonical_payload,
                content_hash=review.content_hash,
                reviewed_at=review.reviewed_at,
                created_at=review.reviewed_at,
            )
        )

    with sessionmaker(bind=engine, future=True)() as fresh:
        replayed = fresh.get(UnderwritingEvidenceCandidateReviewVersion, row_id)
        assert replayed is not None
        assert replayed.dossier_id == dossier.id


def test_sqlite_file_backed_review_waits_for_successor_and_rejects_stale_dossier(
    tmp_path, monkeypatch
) -> None:
    """A review cannot read v1 before an in-flight v2 successor commits."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'candidate-review-race.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    successor_has_writer_lock, release_successor = Event(), Event()
    review_entered, review_finished = Event(), Event()
    errors: list[BaseException] = []
    successor_thread: list[int] = []
    original_latest = UnderwritingResearchRepository._latest

    def hold_successor_after_current_read(repository, statement):
        current = original_latest(repository, statement)
        if successor_thread and get_ident() == successor_thread[0]:
            successor_has_writer_lock.set()
            assert release_successor.wait(timeout=5)
        return current

    monkeypatch.setattr(
        UnderwritingResearchRepository, "_latest", hold_successor_after_current_read,
    )

    try:
        with sessions() as bootstrap:
            kernel = UnderwritingRepository(bootstrap)
            repository = UnderwritingResearchRepository(bootstrap)
            company = kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)
            frozen = freeze_manifest(_manifest_payload(), NOW)
            basis = kernel.add_basis(HistoricalBasisInput(NOW, NOW, frozen.manifest_hash), NOW)
            manifest_payload = _manifest_payload()
            manifest = repository.add_source_manifest(
                manifest_key="candidate-file-race", basis_id=basis.id,
                manifest=manifest_payload, manifest_hash=frozen.manifest_hash,
                content_hash=canonical_hash(manifest_payload), expected_parent_id=None,
                created_at=NOW,
            )
            dossier = _append_dossier(repository, company, basis, manifest)
            bootstrap.commit()
            company_id, basis_id, manifest_id = company.id, basis.id, manifest.id
            manifest_hash, dossier_id, dossier_hash = (
                manifest.manifest_hash, dossier.id, dossier.content_hash,
            )

        def append_successor() -> None:
            with sessions() as successor_session:
                try:
                    successor_thread.append(get_ident())
                    UnderwritingResearchRepository(successor_session).append_candidate_dossier(
                        object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                        dossier_key="industry-capacity",
                        payload=_dossier_payload(
                            object_id=company_id, basis_id=basis_id,
                            source_manifest_id=manifest_id,
                            source_manifest_hash=manifest_hash, version=2,
                            supersedes_id=dossier_id,
                        ),
                        created_at=NOW, expected_parent_id=dossier_id,
                    )
                    successor_session.commit()
                except BaseException as exc:
                    errors.append(exc)
                    successor_session.rollback()

        def append_review() -> None:
            with sessions() as review_session:
                try:
                    review_entered.set()
                    UnderwritingResearchRepository(review_session).append_candidate_review(
                        dossier_id=dossier_id, dossier_content_hash=dossier_hash,
                        reviewer_identity="reviewer:file-race", reviewer_role="provenance",
                        decision="approve",
                        payload=_review_payload(
                            dossier_id=dossier_id, dossier_content_hash=dossier_hash,
                            reviewer_identity="reviewer:file-race", reviewer_role="provenance",
                        ),
                        created_at=NOW,
                    )
                    review_session.commit()
                except BaseException as exc:
                    errors.append(exc)
                    review_session.rollback()
                finally:
                    review_finished.set()

        successor = Thread(target=append_successor)
        review = Thread(target=append_review)
        successor.start()
        assert successor_has_writer_lock.wait(timeout=5)
        review.start()
        assert review_entered.wait(timeout=5)
        assert not review_finished.wait(timeout=0.2)
        release_successor.set()
        successor.join(timeout=5)
        review.join(timeout=5)
        assert not successor.is_alive()
        assert not review.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], ValidationError)
        assert str(errors[0]) == "candidate dossier is no longer current"
    finally:
        release_successor.set()
        engine.dispose()


@pytest.mark.pg_only
def test_postgres_successor_serializes_candidate_review_family(engine, session, monkeypatch) -> None:
    """A v1 review cannot append after another session has committed v2."""
    kernel = UnderwritingRepository(session)
    repository = UnderwritingResearchRepository(session)
    company = kernel.add_object("company", "CN:300750:COMPANY", "CATL", NOW)
    frozen = freeze_manifest(_manifest_payload(), NOW)
    basis = kernel.add_basis(HistoricalBasisInput(NOW, NOW, frozen.manifest_hash), NOW)
    manifest_payload = _manifest_payload()
    manifest = repository.add_source_manifest(
        manifest_key="candidate-review-race", basis_id=basis.id, manifest=manifest_payload,
        manifest_hash=frozen.manifest_hash, content_hash=canonical_hash(manifest_payload),
        expected_parent_id=None, created_at=NOW,
    )
    dossier = _append_dossier(repository, company, basis, manifest)
    dossier_id, dossier_hash = dossier.id, dossier.content_hash
    company_id, basis_id, manifest_id, manifest_hash = (
        company.id, basis.id, manifest.id, manifest.manifest_hash,
    )
    session.commit()

    sessions = sessionmaker(bind=engine, future=True)
    review_read, allow_review_lock = Event(), Event()
    successor_locked, allow_successor_commit = Event(), Event()
    review_lock_attempted, successor_committed = Event(), Event()
    errors: list[BaseException] = []
    review_thread_id: list[int] = []
    successor_thread_id: list[int] = []
    original_lock = UnderwritingResearchRepository._lock_candidate_dossier_family
    original_latest = UnderwritingResearchRepository._latest

    def coordinate_lock(repository, *, object_id, basis_id, dossier_key):
        if review_thread_id and get_ident() == review_thread_id[0]:
            review_read.set()
            assert allow_review_lock.wait(timeout=5)
            review_lock_attempted.set()
        if successor_thread_id and get_ident() == successor_thread_id[0]:
            successor_locked.set()
        return original_lock(
            repository, object_id=object_id, basis_id=basis_id, dossier_key=dossier_key,
        )

    def pause_successor_after_lock(repository, statement):
        row = original_latest(repository, statement)
        if successor_thread_id and get_ident() == successor_thread_id[0]:
            assert allow_successor_commit.wait(timeout=5)
        return row

    monkeypatch.setattr(UnderwritingResearchRepository, "_lock_candidate_dossier_family", coordinate_lock)
    monkeypatch.setattr(UnderwritingResearchRepository, "_latest", pause_successor_after_lock)

    def append_review() -> None:
        review_session = sessions()
        try:
            review_thread_id.append(get_ident())
            UnderwritingResearchRepository(review_session).append_candidate_review(
                dossier_id=dossier_id, dossier_content_hash=dossier_hash,
                reviewer_identity="reviewer:race", reviewer_role="provenance", decision="approve",
                payload=_review_payload(
                    dossier_id=dossier_id, dossier_content_hash=dossier_hash,
                    reviewer_identity="reviewer:race", reviewer_role="provenance",
                ), created_at=NOW,
            )
            review_session.commit()
        except BaseException as exc:
            errors.append(exc)
            review_session.rollback()
        finally:
            review_session.close()

    def append_successor() -> None:
        successor_session = sessions()
        try:
            successor_thread_id.append(get_ident())
            payload = _dossier_payload(
                object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                source_manifest_hash=manifest_hash, version=2, supersedes_id=dossier_id,
            )
            UnderwritingResearchRepository(successor_session).append_candidate_dossier(
                object_id=company_id, basis_id=basis_id, source_manifest_id=manifest_id,
                dossier_key="industry-capacity", payload=payload, created_at=NOW,
                expected_parent_id=dossier_id,
            )
            successor_session.commit()
            successor_committed.set()
        except BaseException as exc:
            errors.append(exc)
            successor_session.rollback()
        finally:
            successor_session.close()

    review = Thread(target=append_review)
    successor = Thread(target=append_successor)
    try:
        review.start()
        assert review_read.wait(timeout=5)
        successor.start()
        assert successor_locked.wait(timeout=5)
        allow_review_lock.set()
        assert review_lock_attempted.wait(timeout=5)
        assert not successor_committed.is_set()
        allow_successor_commit.set()
        review.join(timeout=5)
        successor.join(timeout=5)
        assert not review.is_alive()
        assert not successor.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], ValidationError)
        assert str(errors[0]) == "candidate dossier is no longer current"
    finally:
        allow_review_lock.set()
        allow_successor_commit.set()
        review.join(timeout=5)
        successor.join(timeout=5)

    with sessions() as verifier:
        assert verifier.scalar(
            select(UnderwritingEvidenceCandidateReviewVersion).where(
                UnderwritingEvidenceCandidateReviewVersion.dossier_id == dossier_id
            )
        ) is None


def test_dossier_rejects_stale_parent(repository, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )
    _append_dossier(repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id)

    with pytest.raises(StaleParentError, match="expected parent"):
        _append_dossier(repository, company, basis, manifest, payload=payload, expected_parent_id=dossier.id)


def test_dossier_successor_rejects_cross_object(repository, company, other_company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    payload = _dossier_payload(
        object_id=other_company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="successor must share object and basis"):
        repository.append_candidate_dossier(
            object_id=other_company.id, basis_id=basis.id, source_manifest_id=manifest.id,
            dossier_key="industry-capacity", payload=payload, created_at=NOW,
            expected_parent_id=dossier.id,
        )


def test_dossier_successor_rejects_cross_basis(repository, kernel, company, basis, manifest) -> None:
    dossier = _append_dossier(repository, company, basis, manifest)
    later_cutoff = NOW + timedelta(days=1)
    later_payload = _manifest_payload()
    later_frozen = freeze_manifest(later_payload, later_cutoff)
    later_basis = kernel.add_basis(
        HistoricalBasisInput(later_cutoff, later_cutoff, later_frozen.manifest_hash), later_cutoff,
    )
    later_manifest = repository.add_source_manifest(
        manifest_key="catl-evidence", basis_id=later_basis.id, manifest=later_payload,
        manifest_hash=later_frozen.manifest_hash, content_hash=canonical_hash(later_payload),
        expected_parent_id=manifest.id, created_at=later_cutoff,
    )
    payload = _dossier_payload(
        object_id=company.id, basis_id=later_basis.id, source_manifest_id=later_manifest.id,
        source_manifest_hash=later_manifest.manifest_hash, version=2, supersedes_id=dossier.id,
    )

    with pytest.raises(ValidationError, match="successor must share object and basis"):
        repository.append_candidate_dossier(
            object_id=company.id, basis_id=later_basis.id, source_manifest_id=later_manifest.id,
            dossier_key="industry-capacity", payload=payload, created_at=NOW,
            expected_parent_id=dossier.id,
        )


def test_dossier_rejects_foreign_manifest_source_locator(repository, company, basis, manifest) -> None:
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, source_locator="https://example.test/catl-2024-ar#p18",
    )

    with pytest.raises(ValidationError, match="source_locator does not match"):
        _append_dossier(repository, company, basis, manifest, payload=payload)


def test_dossier_rejects_future_manifest_source(repository, session, company, basis) -> None:
    future_manifest = UnderwritingSourceManifestVersion(
        manifest_key="future-evidence", version=1, basis_id=basis.id,
        manifest=_manifest_payload(available_at=NOW + timedelta(seconds=1)),
        manifest_hash="b" * 64, content_hash="c" * 64, supersedes_id=None,
        created_at=NOW,
    )
    session.add(future_manifest)
    session.flush()
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=future_manifest.id,
        source_manifest_hash=future_manifest.manifest_hash,
    )

    with pytest.raises(ValidationError, match="source is unavailable at basis cutoff"):
        _append_dossier(repository, company, basis, future_manifest, payload=payload)


def test_dossier_rejects_future_candidate_item(repository, company, basis, manifest) -> None:
    payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash, available_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValidationError, match="item available_at must not exceed"):
        _append_dossier(repository, company, basis, manifest, payload=payload)


def test_review_rejects_hash_from_another_dossier(repository, company, basis, manifest) -> None:
    first = _append_dossier(repository, company, basis, manifest)
    second_payload = _dossier_payload(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        source_manifest_hash=manifest.manifest_hash,
    ) | {"dossier_key": "other-industry-capacity"}
    second = repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="other-industry-capacity", payload=second_payload, created_at=NOW,
        expected_parent_id=None,
    )

    with pytest.raises(ValidationError, match="dossier_content_hash does not match"):
        _append_review(repository, second, reviewer_identity="reviewer:a", reviewer_role="provenance", dossier_content_hash=first.content_hash)
