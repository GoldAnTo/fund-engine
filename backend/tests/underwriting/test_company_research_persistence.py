from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import bindparam, create_engine, event, select, text, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from app.models.ledger import Base, ConflictError, ImmutableLedgerError, ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchPersistedBundle,
    CompanyResearchRepository,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchProject,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.domain.product_contracts import ProductHistoricalBasisInput
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.services.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelBuilder,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftService,
)
from tests.underwriting.test_company_research_model_builder import _build_input

NOW = datetime(2026, 8, 25, tzinfo=UTC)
MANDATE_EFFECTIVE_AT = NOW + timedelta(days=1)
EVIDENCE_SOURCE_MANIFEST_HASH = "4" * 64
DEFINITION_BUNDLE_HASH = "5" * 64
PARSER_BUNDLE_HASH = "6" * 64


def _valid_model_payloads(*, with_valuation: bool) -> dict[str, dict[str, object]]:
    build_input = _build_input()
    if not with_valuation:
        build_input = replace(build_input, market_context=None)
    result = CompanyResearchModelBuilder().build(build_input)
    values = {
        "business_map": result.business_map,
        "driver_map": result.driver_map,
        "financial_bridge": result.financial_bridge,
        "scenario_set": result.scenario_set,
        "judgment_context": result.judgment_context,
        "research_gaps": result.gaps,
        "memo": result.memo,
    }
    if result.valuation_set is not None:
        values["valuation_set"] = result.valuation_set
    return {
        kind: CompanyResearchArtifactCodec.encode(kind, artifact)
        for kind, artifact in values.items()
    }


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
    projects = ResearchProjectService(session, now=lambda: NOW)
    mandate = projects.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            mandate_key="company-research-default",
            horizon_years=5,
            base_currency="CNY",
            required_return=Decimal("0.12"),
            permanent_loss_limit=Decimal("0.25"),
            comparison_set=("absolute_intrinsic_value",),
        ),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=MANDATE_EFFECTIVE_AT,
        expires_at=None,
        expected_parent_id=None,
    )
    basis = projects.create_historical_basis(
        ProductHistoricalBasisInput(
            cutoff_at=NOW,
            source_manifest_hash=EVIDENCE_SOURCE_MANIFEST_HASH,
            definition_bundle_hash=DEFINITION_BUNDLE_HASH,
            parser_bundle_hash=PARSER_BUNDLE_HASH,
        )
    )
    WorkspaceDraftService(session, now=lambda: NOW).create(
        project.id,
        initial_content=WorkspaceDraftContent(
            mandate_id=mandate.id,
            historical_basis_id=basis.id,
        ),
    )
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
        payload={
            "cutoff": NOW.isoformat(),
            "fixture_content_hash": EVIDENCE_SOURCE_MANIFEST_HASH,
            "facts": [
                {
                    "fact_key": "revenue",
                    "review_decision": "confirmed",
                    **source_refs[0],
                }
            ],
        },
        source_refs=source_refs,
        expected_parent_id=None,
        created_at=NOW,
    )
    gaps = repository.append_artifact(
        project_id=project.id,
        kind="research_gaps",
        input_hash="3" * 64,
        payload={
            "fixture_content_hash": EVIDENCE_SOURCE_MANIFEST_HASH,
            "company_external_key": "US:TEST:COMPANY",
            "gaps": [],
        },
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
    payloads = _valid_model_payloads(with_valuation=False)
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(project.id)
    assert draft is not None
    assert draft.content.historical_basis_id is not None
    basis = ProductRepository(session).product_basis(draft.content.historical_basis_id)
    assert basis is not None
    bundle = CompanyResearchPersistedBundle(
        evidence_artifact_id=evidence.id,
        evidence_content_hash=evidence.content_hash,
        research_gaps_artifact_id=gaps.id,
        research_gaps_content_hash=gaps.content_hash,
        workspace_draft_id=draft.id,
        workspace_draft_lock_version=draft.lock_version,
        historical_basis_id=basis.id,
        historical_basis_content_hash=basis.content_hash,
        business_map=payloads["business_map"],
        driver_map=payloads["driver_map"],
        financial_bridge=payloads["financial_bridge"],
        scenario_set=payloads["scenario_set"],
        valuation_set=None,
        judgment_context=payloads["judgment_context"],
        research_gaps=payloads["research_gaps"],
        memo=payloads["memo"],
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


def _basis_for_workspace(session, project):
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(project.id)
    assert draft is not None
    assert draft.content.historical_basis_id is not None
    basis = ProductRepository(session).product_basis(draft.content.historical_basis_id)
    assert basis is not None
    return basis


def _substitute_basis(session, project):
    original = _basis_for_workspace(session, project)
    return ResearchProjectService(session, now=lambda: NOW).create_historical_basis(
        ProductHistoricalBasisInput(
            cutoff_at=CompanyResearchRepository._persisted_utc(original.cutoff),
            source_manifest_hash=original.source_manifest_hash,
            definition_bundle_hash="d" * 64,
            parser_bundle_hash="e" * 64,
        )
    )


def _rebind_historical_basis_without_updating_draft_lock(
    session, project, basis_id
) -> None:
    draft = ProductRepository(session).workspace_draft(project.id)
    assert draft is not None
    content = {**draft.content, "historical_basis_id": str(basis_id)}
    _tamper_row(
        session,
        UnderwritingWorkspaceDraft,
        draft.id,
        content=content,
    )


def _save_workspace_draft(session, project, bundle, patch):
    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    draft = drafts.read(project.id)
    assert draft is not None
    updated = drafts.save(
        project.id,
        expected_lock_version=draft.lock_version,
        patch=patch,
    )
    return replace(
        bundle,
        workspace_draft_id=updated.id,
        workspace_draft_lock_version=updated.lock_version,
    )


def _tamper_row(session, model, row_id, *, expire: bool = True, **values) -> None:
    """Bypass ORM immutability with dialect-typed named SQL parameters."""
    table = model.__table__
    if not values or any(key not in table.c for key in values):
        raise AssertionError("tamper helper received an unknown column")
    statement = text(
        f"UPDATE {table.name} SET "
        + ", ".join(f"{key} = :{key}" for key in values)
        + " WHERE id = :tamper_row_id"
    ).bindparams(
        *(bindparam(key, type_=table.c[key].type) for key in values),
        bindparam("tamper_row_id", type_=table.c.id.type),
    )
    connection = session.connection()
    disable_trigger = text(f"ALTER TABLE {table.name} DISABLE TRIGGER USER")
    enable_trigger = text(f"ALTER TABLE {table.name} ENABLE TRIGGER USER")
    if connection.dialect.name == "postgresql":
        connection.execute(disable_trigger)
    try:
        connection.execute(statement, {**values, "tamper_row_id": row_id})
    finally:
        if connection.dialect.name == "postgresql":
            connection.execute(enable_trigger)
    if expire:
        session.expire_all()


def _mutated_bundle_source_refs(bundle, mutation: str):
    refs = [dict(value) for value in bundle.source_refs]
    fabricated = {
        "raw_hash": "9" * 64,
        "source_locator": "fabricated:source",
        "source_role": "third_party",
        "source_url": "https://attacker.invalid/source",
    }
    if mutation == "extra":
        refs.append(fabricated)
    elif mutation == "omitted":
        refs.pop()
    elif mutation == "fabricated":
        refs[0] = fabricated
    else:
        refs[0][mutation] = {
            "source_role": "third_party",
            "source_locator": "fabricated:locator",
            "raw_hash": "8" * 64,
        }[mutation]
    return tuple(refs)


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


def test_complete_model_bundle_succeeds_when_mandate_effective_at_differs_from_evidence(
    session,
) -> None:
    repository, project, preparation, job, evidence, old_gaps, bundle = (
        _repository_with_model_job(session)
    )
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(project.id)
    assert draft is not None and draft.content.mandate_id is not None
    mandate = session.get(UnderwritingMandateVersion, draft.content.mandate_id)
    assert mandate is not None
    assert mandate.effective_at is not None
    assert CompanyResearchRepository._persisted_utc(mandate.effective_at) != (
        CompanyResearchRepository.evidence_cutoff(evidence)
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
    assert all(tuple(row.source_refs) == bundle.source_refs for row in rows)

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
                "historical_basis_id": str(bundle.historical_basis_id),
                "historical_basis_content_hash": bundle.historical_basis_content_hash,
                "market_snapshot_ids": lineage["market_snapshot_ids"],
                "market_snapshot_bindings": lineage["market_snapshot_bindings"],
            }
        )


def test_complete_model_bundle_rejects_a_substitute_historical_basis_captured_for_bundle(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    substitute = _substitute_basis(session, project)
    _rebind_historical_basis_without_updating_draft_lock(
        session,
        project,
        substitute.id,
    )

    with pytest.raises(ValidationError, match="historical basis is stale"):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_durably_tampered_cached_basis_source_as_integrity_error(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    basis = _basis_for_workspace(session, project)
    original_source_manifest_hash = basis.source_manifest_hash
    _tamper_row(
        session,
        UnderwritingHistoricalBasis,
        basis.id,
        expire=False,
        source_manifest_hash="7" * 64,
    )
    assert basis.source_manifest_hash == original_source_manifest_hash

    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_durably_tampered_cached_basis_cutoff_as_integrity_error(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    basis = _basis_for_workspace(session, project)
    original_cutoff = basis.cutoff
    _tamper_row(
        session,
        UnderwritingHistoricalBasis,
        basis.id,
        expire=False,
        cutoff=NOW + timedelta(days=2),
    )
    assert basis.cutoff == original_cutoff

    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_requires_a_historical_basis(session) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    changed = _save_workspace_draft(
        session,
        project,
        bundle,
        {"historical_basis_id": None},
    )

    with pytest.raises(ValidationError, match="historical basis is missing"):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


@pytest.mark.parametrize("basis_state", ("unknown", "non_product"))
def test_complete_model_bundle_rejects_an_unknown_or_non_product_historical_basis(
    session, basis_state: str
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    if basis_state == "unknown":
        basis_id = uuid.uuid4()
    else:
        basis = _basis_for_workspace(session, project)
        _tamper_row(
            session,
            UnderwritingHistoricalBasis,
            basis.id,
            price_as_of=NOW,
        )
        basis_id = basis.id
    changed = _save_workspace_draft(
        session,
        project,
        bundle,
        {"historical_basis_id": basis_id},
    )

    with pytest.raises(ValidationError, match="historical basis is invalid"):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_historical_basis_cutoff_that_differs_from_evidence(
    session,
) -> None:
    repository, project, preparation, _job, evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    reviewed = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="4" * 64,
        payload={
            **evidence.payload,
            "cutoff": (NOW + timedelta(days=2)).isoformat(),
        },
        source_refs=evidence.source_refs,
        expected_parent_id=evidence.id,
        created_at=NOW,
    )
    changed = replace(
        bundle,
        evidence_artifact_id=reviewed.id,
        evidence_content_hash=reviewed.content_hash,
    )

    with pytest.raises(
        ValidationError,
        match="historical basis cutoff does not match reviewed evidence",
    ):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_historical_basis_source_that_differs_from_evidence(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    basis = _basis_for_workspace(session, project)
    _tamper_row(
        session,
        UnderwritingHistoricalBasis,
        basis.id,
        source_manifest_hash="7" * 64,
    )

    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("definition_bundle_hash", None),
        ("definition_bundle_hash", "not-a-hash"),
        ("parser_bundle_hash", None),
        ("parser_bundle_hash", "not-a-hash"),
    ),
)
def test_complete_model_bundle_rejects_a_historical_basis_with_invalid_bundle_hashes(
    session, field: str, value: str | None
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    basis = _basis_for_workspace(session, project)
    _tamper_row(session, UnderwritingHistoricalBasis, basis.id, **{field: value})

    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_a_historical_basis_with_a_corrupted_content_hash(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    basis = _basis_for_workspace(session, project)
    _tamper_row(
        session,
        UnderwritingHistoricalBasis,
        basis.id,
        content_hash="0" * 64,
    )

    with pytest.raises(
        CompanyResearchIntegrityError,
        match="historical basis is invalid",
    ):
        _complete_model_bundle(repository, preparation, bundle)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_does_not_substitute_a_newer_mandate_for_a_missing_basis(
    session,
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(project.id)
    assert draft is not None
    assert draft.content.mandate_id is not None
    newer_mandate = ResearchProjectService(
        session, now=lambda: NOW
    ).append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            mandate_key="company-research-default",
            horizon_years=5,
            base_currency="CNY",
            required_return=Decimal("0.12"),
            permanent_loss_limit=Decimal("0.25"),
            comparison_set=("absolute_intrinsic_value",),
        ),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=MANDATE_EFFECTIVE_AT + timedelta(days=1),
        expires_at=None,
        expected_parent_id=draft.content.mandate_id,
    )
    changed = _save_workspace_draft(
        session,
        project,
        bundle,
        {
            "mandate_id": newer_mandate.id,
            "historical_basis_id": None,
        },
    )

    with pytest.raises(ValidationError, match="historical basis is missing"):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


def test_complete_model_bundle_rejects_evidence_without_a_valid_source_manifest_hash(
    session,
) -> None:
    repository, project, preparation, _job, evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    reviewed = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="4" * 64,
        payload={
            key: value
            for key, value in evidence.payload.items()
            if key != "fixture_content_hash"
        },
        source_refs=evidence.source_refs,
        expected_parent_id=evidence.id,
        created_at=NOW,
    )
    changed = replace(
        bundle,
        evidence_artifact_id=reviewed.id,
        evidence_content_hash=reviewed.content_hash,
    )

    with pytest.raises(
        ValidationError,
        match="reviewed evidence source manifest is invalid",
    ):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


@pytest.mark.parametrize(
    "mutation",
    ("extra", "omitted", "fabricated", "source_role", "source_locator", "raw_hash"),
)
def test_complete_model_bundle_rejects_source_refs_not_derived_from_governed_heads(
    session, mutation: str
) -> None:
    repository, project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    changed = replace(
        bundle,
        source_refs=_mutated_bundle_source_refs(bundle, mutation),
    )

    with pytest.raises(ValidationError, match="source refs"):
        _complete_model_bundle(repository, preparation, changed)

    assert repository.current_artifact(project.id, "business_map") is None


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


@pytest.mark.parametrize("kind", ("driver_map", "scenario_set", "memo"))
def test_append_artifact_rejects_untyped_model_payload_without_lineage(
    session, kind: str
) -> None:
    repository, project, _preparation = _repository_with_preparation(session)

    with pytest.raises(ValidationError, match=rf"{kind} payload is invalid"):
        repository.append_artifact(
            project_id=project.id,
            kind=kind,
            input_hash="d" * 64,
            payload={"fabricated": True},
            source_refs=(),
            expected_parent_id=None,
            created_at=NOW,
        )

    assert repository.current_artifact(project.id, kind) is None


def test_artifact_read_rejects_an_untyped_model_payload_without_lineage(
    session,
) -> None:
    repository, project, _preparation = _repository_with_preparation(session)
    payload = _valid_model_payloads(with_valuation=False)["driver_map"]
    artifact = repository.append_artifact(
        project_id=project.id,
        kind="driver_map",
        input_hash="d" * 64,
        payload=payload,
        source_refs=(),
        expected_parent_id=None,
        created_at=NOW,
    )
    malformed = {"fabricated": True}
    set_committed_value(artifact, "payload", malformed)
    set_committed_value(
        artifact,
        "content_hash",
        CompanyResearchRepository.artifact_content_hash(
            project_id=artifact.project_id,
            kind=artifact.kind,
            version=artifact.version,
            supersedes_id=artifact.supersedes_id,
            parent_content_hash=artifact.parent_content_hash,
            input_hash=artifact.input_hash,
            payload=malformed,
            source_refs=artifact.source_refs,
        ),
    )

    with pytest.raises(ValidationError, match="driver_map payload is invalid"):
        repository.artifact(artifact.id)


def test_model_bundle_rejects_a_memo_ref_that_does_not_match_its_payload(
    session,
) -> None:
    repository, project, _preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    memo = {**bundle.memo}
    memo["business_map_ref"] = {
        **memo["business_map_ref"],
        "content_hash": "a" * 64,
    }

    with pytest.raises(ValidationError, match="memo artifact references"):
        replace(bundle, memo=memo)

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
        payload=bundle.research_gaps,
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
        workspace_draft_id=bundle.workspace_draft_id,
        workspace_draft_lock_version=bundle.workspace_draft_lock_version,
        historical_basis_id=bundle.historical_basis_id,
        historical_basis_content_hash=bundle.historical_basis_content_hash,
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
        kind="evidence_index",
        input_hash="1" * 64,
        payload={"segments": ["search"]},
        source_refs=[{"source_id": "annual-report"}],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
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
    assert repository.current_artifact(project.id, "evidence_index").id == second.id
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
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        first.id,
        supersedes_id=second.id,
        parent_content_hash=second.content_hash,
        content_hash=cycle_hash,
    )
    with pytest.raises(CompanyResearchIntegrityError, match="parent content hash"):
        repository.artifact_chain(second.id)


def test_artifact_chain_rejects_an_admin_rewrite_of_parent_content(session) -> None:
    """A successor commits its parent's content hash, not only its UUID."""
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="1" * 64,
        payload={"segments": ["search"]},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
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
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        first.id,
        payload=rewritten_payload,
        content_hash=rewritten_hash,
    )

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
    _tamper_row(
        session,
        CompanyResearchEvent,
        first.id,
        payload=rewritten_payload,
        content_hash=rewritten_hash,
    )

    with pytest.raises(CompanyResearchIntegrityError, match="predecessor"):
        repository.events(preparation.id)


def test_artifact_reads_fail_closed_on_hash_tamper_and_duplicate_heads(session) -> None:
    repository, project, _ = _repository_with_preparation(session)
    artifact = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="3" * 64,
        payload={"driver": "query volume"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    original_hash = artifact.content_hash

    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        artifact.id,
        content_hash="0" * 64,
    )
    with pytest.raises(CompanyResearchIntegrityError, match="content hash"):
        repository.artifact(artifact.id)

    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        artifact.id,
        content_hash=original_hash,
    )
    duplicate = CompanyResearchArtifactVersion(
        project_id=project.id,
        kind="evidence_index",
        version=2,
        supersedes_id=None,
        input_hash="4" * 64,
        payload={"driver": "cloud"},
        source_refs=[],
        content_hash=CompanyResearchRepository.artifact_content_hash(
            project_id=project.id,
            kind="evidence_index",
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
        repository.current_artifact(project.id, "evidence_index")


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
    _tamper_row(session, Job, job.id, kind="prepare_research")

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
            kind="evidence_index",
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
                    kind="evidence_index",
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
            kind="evidence_index",
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
        kind="evidence_index",
        input_hash="5" * 64,
        payload={"segment": "search"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    foreign_project = _project(session) if foreign_scope == "project" else project
    foreign_kind = "evidence_index" if foreign_scope == "project" else "research_gaps"
    foreign = repository.append_artifact(
        project_id=foreign_project.id,
        kind=foreign_kind,
        input_hash="6" * 64,
        payload=(
            {"segment": "cloud"}
            if foreign_kind == "evidence_index"
            else {
                "fixture_content_hash": "6" * 64,
                "company_external_key": "US:FOREIGN:COMPANY",
                "gaps": [],
            }
        ),
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
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        foreign.id,
        supersedes_id=first.id,
        parent_content_hash=first.content_hash,
        content_hash=foreign_hash,
    )

    with pytest.raises(ValidationError, match="successor|payload is invalid"):
        repository.current_artifact(project.id, "evidence_index")


def test_current_artifact_rejects_a_cycle_instead_of_returning_no_current(
    session,
) -> None:
    repository, project, _ = _repository_with_preparation(session)
    first = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
        input_hash="7" * 64,
        payload={"segment": "search"},
        source_refs=[],
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_artifact(
        project_id=project.id,
        kind="evidence_index",
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
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        first.id,
        supersedes_id=second.id,
        parent_content_hash=second.content_hash,
        content_hash=cycle_hash,
    )

    with pytest.raises(CompanyResearchIntegrityError, match="cycle"):
        repository.current_artifact(project.id, "evidence_index")


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
