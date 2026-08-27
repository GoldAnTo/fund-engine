from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ImmutableLedgerError, ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchPersistedBundle,
    CompanyResearchRepository,
)
from app.underwriting.persistence.repository import StaleParentError
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


def _repository_with_evidence_job(session):
    repository, project, preparation = _repository_with_preparation(session)
    job = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        step="evidence_index",
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add(job)
    session.flush()
    repository.attach_prepare_job(preparation.id, job.id)
    return repository, project, preparation, job


def _repository_with_model_job(session):
    repository, project, preparation, job = _repository_with_evidence_job(session)
    source_refs = (
        {
            "raw_hash": "1" * 64,
            "source_locator": "fixture:1",
            "source_role": "filing",
            "source_url": "https://example.test/filing",
        },
    )
    evidence = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="2" * 64,
        payload={"facts": [{"fact_key": "revenue", "review_decision": "confirmed"}]},
        source_refs=source_refs,
        expected_parent_id=None,
        created_at=NOW,
    )
    gaps = repository.append_artifact(
        project_id=project.id,
        kind="research_gaps",
        input_hash="3" * 64,
        payload={"gaps": []},
        source_refs=source_refs,
        expected_parent_id=None,
        created_at=NOW,
    )
    preparation.status = "building_model"
    preparation.current_step = "model_bundle"
    preparation.progress = 25
    job.status = "running"
    job.step = "model_bundle"
    job.progress = 25
    job.claim_token = "model-claim"
    session.flush([preparation, job])
    bundle = CompanyResearchPersistedBundle(
        evidence_artifact_id=evidence.id,
        evidence_content_hash=evidence.content_hash,
        research_gaps_artifact_id=gaps.id,
        research_gaps_content_hash=gaps.content_hash,
        business_map={"modules": [{"key": "search"}]},
        driver_map={"drivers": [{"driver_key": "queries"}]},
        financial_bridge={"scenario_id": "base", "rows": []},
        scenario_set={"scenarios": [{"scenario_id": "base"}]},
        valuation_set=None,
        judgment_context={"assessment_status": "answerable"},
        research_gaps={"gaps": []},
        memo={"candidate_status": "machine_draft"},
        source_refs=source_refs,
        market_snapshot_bindings=(),
    )
    return repository, project, preparation, job, evidence, gaps, bundle


def _complete_model_bundle(repository, preparation, bundle):
    return repository.complete_model_bundle(
        preparation.id,
        bundle=bundle,
        expected_claim_token="model-claim",
        expected_request_hash=preparation.request_hash,
        expected_strategy_version=preparation.strategy_version,
        created_at=NOW,
    )


@pytest.mark.parametrize(
    "fail_after",
    (
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "judgment_context",
        "research_gaps",
        "memo",
    ),
)
def test_model_bundle_rolls_back_every_artifact_on_failure(
    session, fail_after: str, monkeypatch
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    original = repository.append_artifact

    def fail_on_kind(*args, kind, **kwargs):
        if kind == fail_after:
            raise RuntimeError("injected bundle failure")
        return original(*args, kind=kind, **kwargs)

    monkeypatch.setattr(repository, "append_artifact", fail_on_kind)
    with pytest.raises(RuntimeError, match="injected bundle failure"):
        _complete_model_bundle(repository, preparation, bundle)

    observer = CompanyResearchRepository(session)
    existing = {
        kind
        for kind in (
            "evidence_index",
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "research_gaps",
            "memo",
        )
        if observer.current_artifact(project.id, kind) is not None
    }
    assert existing == {"evidence_index", "research_gaps"}


def test_complete_model_bundle_persists_exact_dependency_refs_and_input_hashes(
    session,
) -> None:
    repository, _project, preparation, job, evidence, old_gaps, bundle = (
        _repository_with_model_job(session)
    )

    updated, rows = _complete_model_bundle(repository, preparation, bundle)
    by_kind = {row.kind: row for row in rows}

    assert tuple(by_kind) == (
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "judgment_context",
        "research_gaps",
        "memo",
    )
    assert updated.status == "awaiting_judgment_review"
    assert updated.current_step == "judgment_context"
    assert updated.progress == 85
    assert job.status == "waiting_for_review"
    assert job.step == "judgment_context"
    assert job.progress == 85
    assert job.claim_token is None
    assert by_kind["research_gaps"].supersedes_id == old_gaps.id

    def refs(kind: str) -> dict[str, tuple[str, str]]:
        return {
            item["artifact_kind"]: (item["artifact_id"], item["content_hash"])
            for item in by_kind[kind].payload["_lineage"]["artifact_refs"]
        }

    assert refs("business_map") == {
        "evidence_index": (str(evidence.id), evidence.content_hash)
    }
    assert refs("driver_map") == {
        "business_map": (
            str(by_kind["business_map"].id),
            by_kind["business_map"].content_hash,
        )
    }
    assert set(refs("judgment_context")) == {
        "evidence_index",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "research_gaps",
    }
    assert refs("memo") == {
        "judgment_context": (
            str(by_kind["judgment_context"].id),
            by_kind["judgment_context"].content_hash,
        )
    }
    expected_market_ids: list[str] = []
    for row in rows:
        lineage = row.payload["_lineage"]
        assert lineage["market_snapshot_ids"] == expected_market_ids
        assert row.input_hash == canonical_hash(
            {
                "request_hash": preparation.request_hash,
                "artifact_refs": lineage["artifact_refs"],
                "market_snapshot_ids": lineage["market_snapshot_ids"],
                "market_snapshot_bindings": lineage["market_snapshot_bindings"],
            }
        )


def test_complete_model_bundle_rejects_a_stale_claim_without_writing(session) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )

    with pytest.raises(ValidationError, match="claim is stale"):
        repository.complete_model_bundle(
            preparation.id,
            bundle=bundle,
            expected_claim_token="stale-claim",
            expected_request_hash=preparation.request_hash,
            expected_strategy_version=preparation.strategy_version,
            created_at=NOW,
        )

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_stale_evidence_head_without_writing(
    session,
) -> None:
    repository, project, preparation, _job, evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    successor_payload = dict(evidence.payload)
    successor_payload["review_generation"] = 2
    repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="4" * 64,
        payload=successor_payload,
        source_refs=evidence.source_refs,
        expected_parent_id=evidence.id,
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="model inputs are stale"):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_stale_gap_head_without_writing(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, gaps, bundle = (
        _repository_with_model_job(session)
    )
    repository.append_artifact(
        project_id=project.id,
        kind="research_gaps",
        input_hash="4" * 64,
        payload={"gaps": [{"gap_key": "new"}]},
        source_refs=gaps.source_refs,
        expected_parent_id=gaps.id,
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="model inputs are stale"):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_omits_optional_valuation_and_keeps_lineage_closed(
    session,
) -> None:
    repository, _project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    without_valuation = CompanyResearchPersistedBundle(
        evidence_artifact_id=bundle.evidence_artifact_id,
        evidence_content_hash=bundle.evidence_content_hash,
        research_gaps_artifact_id=bundle.research_gaps_artifact_id,
        research_gaps_content_hash=bundle.research_gaps_content_hash,
        business_map=bundle.business_map,
        driver_map=bundle.driver_map,
        financial_bridge=bundle.financial_bridge,
        scenario_set=bundle.scenario_set,
        valuation_set=None,
        judgment_context=bundle.judgment_context,
        research_gaps=bundle.research_gaps,
        memo=bundle.memo,
        source_refs=bundle.source_refs,
        market_snapshot_bindings=(),
    )

    _updated, rows = _complete_model_bundle(repository, preparation, without_valuation)
    by_kind = {row.kind: row for row in rows}

    assert "valuation_set" not in by_kind
    judgment_kinds = {
        item["artifact_kind"]
        for item in by_kind["judgment_context"].payload["_lineage"]["artifact_refs"]
    }
    assert "valuation_set" not in judgment_kinds


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
        parent_content_hash=second.content_hash,
        input_hash=first.input_hash,
        payload=first.payload,
        source_refs=first.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET supersedes_id = ?, parent_content_hash = ?, content_hash = ? WHERE id = ?",
        (second.id.hex, second.content_hash, cycle_hash, first.id.hex),
    )
    session.expire_all()
    with pytest.raises(CompanyResearchIntegrityError, match="parent content hash"):
        repository.artifact_chain(second.id)


def test_artifact_chain_rejects_an_admin_rewrite_of_parent_content(session) -> None:
    """A successor commits its parent's content hash, not only its UUID."""
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="1" * 64,
        payload={"segments": ["search"]},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="2" * 64,
        payload={"segments": ["search", "cloud"]},
        source_refs=[],
        expected_parent_id=first.id,
        created_at=NOW,
    )
    assert second.parent_content_hash == first.content_hash

    rewritten_payload = {"segments": ["rewritten"]}
    rewritten_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=first.project_id,
        kind=first.kind,
        version=first.version,
        supersedes_id=None,
        parent_content_hash=None,
        input_hash=first.input_hash,
        payload=rewritten_payload,
        source_refs=first.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET payload = ?, content_hash = ? WHERE id = ?",
        ('{"segments":["rewritten"]}', rewritten_hash, first.id.hex),
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="parent content hash"):
        repository.artifact_chain(second.id)


def test_event_reads_reject_a_rewritten_predecessor(session) -> None:
    repository, _, preparation = _repository_with_preparation(session)
    first = repository.append_event(
        preparation_id=preparation.id,
        event_type="initialized",
        payload={"step": "evidence_index"},
        created_at=NOW,
    )
    second = repository.append_event(
        preparation_id=preparation.id,
        event_type="advanced",
        payload={"step": "business_map"},
        created_at=NOW,
    )
    assert (first.sequence, second.sequence) == (1, 2)
    assert second.previous_event_hash == first.content_hash

    rewritten_payload = {"step": "rewritten"}
    rewritten_hash = CompanyResearchRepository.event_content_hash(
        preparation_id=first.preparation_id,
        sequence=first.sequence,
        previous_event_hash=None,
        event_type=first.event_type,
        payload=rewritten_payload,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_events SET payload = ?, content_hash = ? WHERE id = ?",
        ('{"step":"rewritten"}', rewritten_hash, first.id.hex),
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="predecessor"):
        repository.events(preparation.id)


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
    original_hash = artifact.content_hash

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
        (original_hash, artifact.id.hex),
    )
    session.expire_all()
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
        step="evidence_index",
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add_all((legacy, owned))
    session.flush()

    assert repository.prepare_job(preparation.id) is None
    assert repository.attach_prepare_job(preparation.id, owned.id).job_id == owned.id
    assert repository.prepare_job(preparation.id).id == owned.id
    with pytest.raises(ConflictError, match="already bound"):
        repository.attach_prepare_job(preparation.id, legacy.id)


def test_preparation_can_only_attach_its_first_valid_job(session) -> None:
    repository, _, preparation = _repository_with_preparation(session)
    first = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    second = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add_all((first, second))
    session.flush()

    assert repository.attach_prepare_job(preparation.id, first.id).job_id == first.id
    assert repository.attach_prepare_job(preparation.id, first.id).job_id == first.id
    with pytest.raises(ConflictError, match="already bound"):
        repository.attach_prepare_job(preparation.id, second.id)


def test_prepare_job_rejects_an_attached_job_whose_discriminator_was_rewritten(
    session,
) -> None:
    repository, _, preparation = _repository_with_preparation(session)
    job = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=preparation.id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add(job)
    session.flush()
    repository.attach_prepare_job(preparation.id, job.id)
    session.connection().exec_driver_sql(
        "UPDATE jobs SET kind = 'prepare_research' WHERE id = ?", (job.id.hex,)
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="job ownership"):
        repository.prepare_job(preparation.id)


@pytest.mark.parametrize("operation", ("complete", "fail"))
def test_evidence_lifecycle_locks_its_owned_job_before_transition(
    session, operation: str
) -> None:
    """A PostgreSQL rival cannot rewrite the Job after preparation is locked."""
    repository, _project, preparation, _job = _repository_with_evidence_job(session)
    session.expire_all()
    statements = []

    def _capture_job_select(execute_state) -> None:
        statement = execute_state.statement
        if execute_state.is_select and "FROM jobs" in str(statement):
            statements.append(statement)

    event.listen(session, "do_orm_execute", _capture_job_select)
    try:
        if operation == "complete":
            repository.complete_evidence_preparation(
                preparation.id,
                input_hash="c" * 64,
                evidence_index_payload={"facts": []},
                research_gaps_payload={"gaps": []},
                source_refs=[],
                created_at=NOW,
            )
        else:
            repository.fail_evidence_preparation(
                preparation.id,
                error_code="fixture_unavailable",
                created_at=NOW,
            )
    finally:
        event.remove(session, "do_orm_execute", _capture_job_select)

    assert len(statements) == 1
    assert "FOR UPDATE" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statements[0].compile(dialect=sqlite.dialect()))


def test_add_preparation_rejects_a_job_that_is_not_its_exact_owner(session) -> None:
    project = _project(session)
    repository = CompanyResearchRepository(session)
    legacy = Job(
        kind="prepare_research",
        status="queued",
        progress=0,
        attempt=1,
        target_type="company_research_preparation",
        target_id=uuid.uuid4(),
        research_case_id=None,
        created_at=NOW,
    )
    session.add(legacy)
    session.flush()

    with pytest.raises(ValidationError, match="job ownership"):
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
            job_id=legacy.id,
            created_at=NOW,
            updated_at=NOW,
        )

    assert session.scalar(select(CompanyResearchPreparation.id)) is None


def test_add_preparation_binds_a_job_to_its_new_exact_target(session) -> None:
    project = _project(session)
    repository = CompanyResearchRepository(session)
    preparation_id = uuid.uuid4()
    owned = Job(
        kind="prepare_company_research",
        status="queued",
        progress=0,
        attempt=1,
        step="evidence_index",
        target_type="company_research_preparation",
        target_id=preparation_id,
        research_case_id=None,
        created_at=NOW,
    )
    session.add(owned)
    session.flush()

    preparation = repository.add_preparation(
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
        job_id=owned.id,
        created_at=NOW,
        updated_at=NOW,
    )

    assert preparation.id == preparation_id
    assert preparation.job_id == owned.id


@pytest.mark.parametrize("operation", ("add", "attach"))
def test_job_ownership_binding_locks_the_job_before_validation(
    session, operation: str
) -> None:
    """The Job discriminator must remain stable through the binding decision."""
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
    session.add(legacy)
    session.flush()
    statements = []

    def _capture_job_select(execute_state) -> None:
        statement = execute_state.statement
        if execute_state.is_select and "FROM jobs" in str(statement):
            statements.append(statement)

    event.listen(session, "do_orm_execute", _capture_job_select)
    try:
        with pytest.raises(ValidationError, match="job ownership"):
            if operation == "add":
                repository.add_preparation(
                    project_id=uuid.uuid4(),
                    idempotency_key=f"prepare:{uuid.uuid4().hex}",
                    request_hash="d" * 64,
                    strategy_version="company-research-default.v1",
                    status="queued",
                    current_step="evidence_index",
                    progress=0,
                    attempt=1,
                    next_attempt_at=None,
                    last_error_code=None,
                    job_id=legacy.id,
                    created_at=NOW,
                    updated_at=NOW,
                )
            else:
                repository.attach_prepare_job(preparation.id, legacy.id)
    finally:
        event.remove(session, "do_orm_execute", _capture_job_select)

    assert len(statements) == 1
    assert "FOR UPDATE" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statements[0].compile(dialect=sqlite.dialect()))


def test_attach_prepare_job_locks_the_preparation_it_mutates(session) -> None:
    repository, _, preparation = _repository_with_preparation(session)
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
    session.add(owned)
    session.flush()
    statements = []

    def _capture_preparation_select(execute_state) -> None:
        statement = execute_state.statement
        if execute_state.is_select and "FROM uw_company_research_preparations" in str(
            statement
        ):
            statements.append(statement)

    event.listen(session, "do_orm_execute", _capture_preparation_select)
    try:
        assert (
            repository.attach_prepare_job(preparation.id, owned.id).job_id == owned.id
        )
    finally:
        event.remove(session, "do_orm_execute", _capture_preparation_select)

    assert len(statements) == 1
    assert "FOR UPDATE" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statements[0].compile(dialect=sqlite.dialect()))


@pytest.mark.parametrize("operation", ("add", "attach"))
def test_sqlite_job_ownership_binding_reserves_the_writer_before_its_read(
    tmp_path, operation: str
) -> None:
    """A competing ownership rewrite cannot slip between the read and bind."""
    engine = create_engine(
        f"sqlite:///{tmp_path / f'company-research-{operation}.sqlite'}",
        future=True,
        connect_args={"timeout": 0.1},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    bootstrap = sessions()
    try:
        project = _project(bootstrap)
        preparation_id = uuid.uuid4()
        if operation == "attach":
            preparation = CompanyResearchRepository(bootstrap).add_preparation(
                project_id=project.id,
                idempotency_key=f"prepare:{uuid.uuid4().hex}",
                request_hash="d" * 64,
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
            preparation_id = preparation.id
        job = Job(
            kind="prepare_company_research",
            status="queued",
            progress=0,
            attempt=1,
            step="evidence_index",
            target_type="company_research_preparation",
            target_id=preparation_id,
            research_case_id=None,
            created_at=NOW,
        )
        bootstrap.add(job)
        bootstrap.commit()
        project_id = project.id
        job_id = job.id
    finally:
        bootstrap.close()

    worker = sessions()
    rival = sessions()
    primary_connection = worker.connection()
    rival_errors: list[OperationalError] = []
    job_read_seen = False

    def rewrite_after_primary_job_read(connection, _cursor, statement, *_args) -> None:
        nonlocal job_read_seen
        if connection is not primary_connection or job_read_seen:
            return
        if statement.lstrip().upper().startswith("SELECT") and "FROM jobs" in statement:
            job_read_seen = True
            try:
                rival.execute(
                    update(Job).where(Job.id == job_id).values(kind="prepare_research")
                )
                rival.commit()
            except OperationalError as exc:
                rival.rollback()
                rival_errors.append(exc)

    event.listen(engine, "after_cursor_execute", rewrite_after_primary_job_read)
    try:
        repository = CompanyResearchRepository(worker)
        if operation == "add":
            result = repository.add_preparation(
                project_id=project_id,
                idempotency_key=f"prepare:{uuid.uuid4().hex}",
                request_hash="e" * 64,
                strategy_version="company-research-default.v1",
                status="queued",
                current_step="evidence_index",
                progress=0,
                attempt=1,
                next_attempt_at=None,
                last_error_code=None,
                job_id=job_id,
                created_at=NOW,
                updated_at=NOW,
            )
        else:
            result = repository.attach_prepare_job(preparation_id, job_id)
        result_id = result.id
        worker.commit()
    finally:
        event.remove(engine, "after_cursor_execute", rewrite_after_primary_job_read)
        worker.close()
        rival.close()

    try:
        assert job_read_seen
        assert len(rival_errors) == 1
        observer = sessions()
        try:
            persisted_job = observer.get(Job, job_id)
            persisted_preparation = observer.get(CompanyResearchPreparation, result_id)
            assert persisted_job is not None
            assert persisted_job.kind == "prepare_company_research"
            assert persisted_preparation is not None
            assert persisted_preparation.job_id == job_id
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_sqlite_job_ownership_reservation_rolls_back_with_the_caller(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-rollback.sqlite'}", future=True
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    bootstrap = sessions()
    try:
        _, project, preparation = _repository_with_preparation(bootstrap)
        job = Job(
            kind="prepare_company_research",
            status="queued",
            progress=0,
            attempt=1,
            target_type="company_research_preparation",
            target_id=preparation.id,
            research_case_id=None,
            created_at=NOW,
        )
        bootstrap.add(job)
        bootstrap.commit()
        preparation_id = preparation.id
        job_id = job.id
        project_id = project.id
    finally:
        bootstrap.close()

    worker = sessions()
    try:
        result = CompanyResearchRepository(worker).attach_prepare_job(
            preparation_id, job_id
        )
        assert result.job_id == job_id
        worker.rollback()
    finally:
        worker.close()

    try:
        observer = sessions()
        try:
            preparation = observer.get(CompanyResearchPreparation, preparation_id)
            assert preparation is not None
            assert preparation.project_id == project_id
            assert preparation.job_id is None
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_sqlite_concurrent_artifact_append_returns_a_stale_parent_error(
    tmp_path,
) -> None:
    """The losing writer must get a domain stale-parent result, not sqlite I/O."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-research-artifact-race.sqlite'}",
        future=True,
        connect_args={"timeout": 0.1},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    bootstrap = sessions()
    try:
        repository, project, _ = _repository_with_preparation(bootstrap)
        root = repository.append_artifact(
            project_id=project.id,
            kind="business_map",
            input_hash="9" * 64,
            payload={"segments": ["search"]},
            source_refs=[],
            expected_parent_id=None,
            created_at=NOW,
        )
        bootstrap.commit()
        project_id, root_id = project.id, root.id
    finally:
        bootstrap.close()

    primary, rival = sessions(), sessions()
    primary_connection = primary.connection()
    attempted_rival = False
    rival_errors: list[Exception] = []

    def append_from_rival(connection, _cursor, statement, *_args) -> None:
        nonlocal attempted_rival
        if connection is not primary_connection or attempted_rival:
            return
        if statement.lstrip().upper().startswith("SELECT") and (
            "FROM uw_company_research_artifact_versions" in statement
        ):
            attempted_rival = True
            try:
                CompanyResearchRepository(rival).append_artifact(
                    project_id=project_id,
                    kind="business_map",
                    input_hash="a" * 64,
                    payload={"segments": ["cloud"]},
                    source_refs=[],
                    expected_parent_id=root_id,
                    created_at=NOW,
                )
            except Exception as exc:  # assertion below makes the type strict
                rival_errors.append(exc)

    event.listen(engine, "after_cursor_execute", append_from_rival)
    try:
        appended = CompanyResearchRepository(primary).append_artifact(
            project_id=project_id,
            kind="business_map",
            input_hash="b" * 64,
            payload={"segments": ["youtube"]},
            source_refs=[],
            expected_parent_id=root_id,
            created_at=NOW,
        )
        appended_id = appended.id
        primary.commit()
    finally:
        event.remove(engine, "after_cursor_execute", append_from_rival)
        primary.close()
        rival.close()

    try:
        assert attempted_rival
        assert len(rival_errors) == 1
        assert isinstance(rival_errors[0], StaleParentError)
        observer = sessions()
        try:
            assert CompanyResearchRepository(observer).artifact(appended_id) is not None
        finally:
            observer.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("foreign_scope", ("kind", "project"))
def test_current_artifact_rejects_a_foreign_scope_successor(
    session, foreign_scope: str
) -> None:
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="5" * 64,
        payload={"segment": "search"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    foreign_project = _project(session) if foreign_scope == "project" else project
    foreign_kind = "business_map" if foreign_scope == "project" else "driver_map"
    foreign = repository.append_artifact(
        project_id=foreign_project.id,
        kind=foreign_kind,
        input_hash="6" * 64,
        payload={"segment": "cloud"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    foreign_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=foreign.project_id,
        kind=foreign.kind,
        version=foreign.version,
        supersedes_id=first.id,
        parent_content_hash=first.content_hash,
        input_hash=foreign.input_hash,
        payload=foreign.payload,
        source_refs=foreign.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET supersedes_id = ?, parent_content_hash = ?, content_hash = ? WHERE id = ?",
        (first.id.hex, first.content_hash, foreign_hash, foreign.id.hex),
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="successor"):
        repository.current_artifact(project.id, "business_map")


def test_current_artifact_rejects_a_cycle_instead_of_returning_no_current(
    session,
) -> None:
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="7" * 64,
        payload={"segment": "search"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="business_map",
        input_hash="8" * 64,
        payload={"segment": "cloud"},
        source_refs=[],
        expected_parent_id=first.id,
        created_at=NOW,
    )
    cycle_hash = CompanyResearchRepository.artifact_content_hash(
        project_id=first.project_id,
        kind=first.kind,
        version=first.version,
        supersedes_id=second.id,
        parent_content_hash=second.content_hash,
        input_hash=first.input_hash,
        payload=first.payload,
        source_refs=first.source_refs,
    )
    session.connection().exec_driver_sql(
        "UPDATE uw_company_research_artifact_versions "
        "SET supersedes_id = ?, parent_content_hash = ?, content_hash = ? WHERE id = ?",
        (second.id.hex, second.content_hash, cycle_hash, first.id.hex),
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="cycle"):
        repository.current_artifact(project.id, "business_map")


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
