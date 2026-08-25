from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.ledger import ConflictError, ImmutableLedgerError, ValidationError
from app.models.operational import Job
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import UnderwritingResearchProject

NOW = datetime(2026, 8, 25, tzinfo=UTC)


def _project(session) -> UnderwritingResearchProject:
    company = UnderwritingResearchObject(
        kind="company",
        external_key=f"US:TEST:{uuid.uuid4().hex}:COMPANY",
        canonical_name="Test Company",
        created_at=NOW,
    )
    session.add(company)
    session.flush()
    project = UnderwritingResearchProject(
        primary_company_id=company.id,
        content_hash="a" * 64,
        created_at=NOW,
    )
    session.add(project)
    session.flush()
    return project


def _repository_with_preparation(session):
    project = _project(session)
    repository = CompanyResearchRepository(session)
    preparation = repository.add_preparation(
        project_id=project.id,
        idempotency_key=f"prepare:{uuid.uuid4().hex}",
        request_hash="b" * 64,
        strategy_version="company-research-default.v1",
        status="queued",
        current_step="evidence_index",
        progress=0,
        attempt=1,
        next_attempt_at=None,
        last_error_code=None,
        job_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    return repository, project, preparation


def test_preparation_is_mutable_but_idempotency_and_project_are_unique(session) -> None:
    repository, project, preparation = _repository_with_preparation(session)

    preparation.status = "preparing_sources"
    preparation.progress = 20
    session.flush()
    assert repository.preparation(preparation.id).status == "preparing_sources"

    with pytest.raises(ConflictError):
        repository.add_preparation(
            project_id=project.id,
            idempotency_key=f"prepare:{uuid.uuid4().hex}",
            request_hash="c" * 64,
            strategy_version="company-research-default.v1",
            status="queued",
            current_step="evidence_index",
            progress=0,
            attempt=1,
            next_attempt_at=None,
            last_error_code=None,
            job_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
    second_project = _project(session)
    with pytest.raises(ConflictError):
        repository.add_preparation(
            project_id=second_project.id,
            idempotency_key=preparation.idempotency_key,
            request_hash="c" * 64,
            strategy_version="company-research-default.v1",
            status="queued",
            current_step="evidence_index",
            progress=0,
            attempt=1,
            next_attempt_at=None,
            last_error_code=None,
            job_id=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_artifacts_are_immutable_and_replayed_from_a_strict_parent_chain(
    session,
) -> None:
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="1" * 64,
        payload={"segments": ["search"]},
        source_refs=[{"source_id": "annual-report"}],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="2" * 64,
        payload={"segments": ["search", "cloud"]},
        source_refs=[{"source_id": "annual-report"}],
        expected_parent_id=first.id,
        created_at=NOW,
    )

    assert tuple(row.id for row in repository.artifact_chain(second.id)) == (
        first.id,
        second.id,
    )
    assert repository.current_artifact(project.id, "business_map").id == second.id
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            CompanyResearchArtifactVersion.__table__.update().values(version=9)
        )
    cycle_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=first.project_id,
        kind=first.kind,
        version=first.version,
        supersedes_id=second.id,
        input_hash=first.input_hash,
        payload=first.payload,
        source_refs=first.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET supersedes_id = ?, content_hash = ? WHERE id = ?",
        (second.id.hex, cycle_hash, first.id.hex),
    )
    session.expire_all()
    with pytest.raises(CompanyResearchIntegrityError, match="cycle"):
        repository.artifact_chain(second.id)


def test_artifact_reads_fail_closed_on_hash_tamper_and_duplicate_heads(session) -> None:
    repository, project, _ = _repository_with_preparation(session)
    artifact = repository.append_artifact(
        project_id=project.id,
        kind="driver_map",
        input_hash="3" * 64,
        payload={"driver": "query volume"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )

    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET content_hash = ? WHERE id = ?",
        ("0" * 64, artifact.id.hex),
    )
    session.expire_all()
    with pytest.raises(CompanyResearchIntegrityError, match="content hash"):
        repository.artifact(artifact.id)

    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET content_hash = ? WHERE id = ?",
        (artifact.content_hash, artifact.id.hex),
    )
    duplicate = CompanyResearchArtifactVersion(
        project_id=project.id,
        kind="driver_map",
        version=2,
        supersedes_id=None,
        input_hash="4" * 64,
        payload={"driver": "cloud"},
        source_refs=[],
        content_hash=CompanyResearchRepository.artifact_content_hash(
            project_id=project.id,
            kind="driver_map",
            version=2,
            supersedes_id=None,
            input_hash="4" * 64,
            payload={"driver": "cloud"},
            source_refs=[],
        ),
        created_at=NOW,
    )
    session.add(duplicate)
    session.flush()
    with pytest.raises(ConflictError, match="multiple current artifact heads"):
        repository.current_artifact(project.id, "driver_map")


def test_job_lookup_requires_exact_company_research_ownership(session) -> None:
    repository, _, preparation = _repository_with_preparation(session)
    legacy = Job(
        kind="prepare_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    owned = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add_all((legacy, owned))
    session.flush()

    assert repository.prepare_job(preparation.id).id == owned.id
    assert repository.attach_prepare_job(preparation.id, owned.id).job_id == owned.id
    with pytest.raises(ValidationError, match="job ownership"):
        repository.attach_prepare_job(preparation.id, legacy.id)


def test_repository_writes_remain_owned_by_the_callers_transaction(session) -> None:
    repository, project, _ = _repository_with_preparation(session)
    repository.append_event(
        preparation_id=session.scalar(select(CompanyResearchPreparation.id)),
        event_type="initialized",
        payload={"project_id": str(project.id)},
        created_at=NOW,
    )
    session.rollback()

    assert session.scalar(select(CompanyResearchPreparation.id)) is None
