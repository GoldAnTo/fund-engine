# Company Research Historical Basis Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HistoricalBasis the sole company-research time boundary and recover the existing blocked Alphabet project in place without changing its seven evidence decisions.

**Architecture:** Add one pure Alphabet boundary descriptor that normalizes the supported cutoff and fingerprints source, model-definition, and parser contracts. New initialization creates and binds that basis atomically; model completion validates basis/evidence/market equality; a narrowly gated retry service locks and repairs only legacy drafts with no basis before requeueing the blocked model job.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Pydantic 2, pytest, React 19, TypeScript, Vitest, Docker Compose, PostgreSQL 16

---

## File map

- Create backend/app/underwriting/services/company_research_boundary.py: cutoff resolution and deterministic basis fingerprints.
- Create backend/tests/underwriting/test_company_research_boundary.py: unit tests for cutoff and fingerprint rules.
- Modify backend/app/underwriting/services/company_research_initializer.py: basis-bound initialization and retry orchestration.
- Modify backend/app/underwriting/persistence/product_repository.py: exact basis lookup for idempotent recovery.
- Modify backend/app/underwriting/persistence/company_research_repository.py: strict boundary validation, locked recovery state, and blocked-job requeue.
- Create backend/app/underwriting/services/company_research_basis_recovery.py: fail-closed legacy recovery.
- Modify backend/app/underwriting/services/company_research_preparation.py: bounded validation diagnostics.
- Modify the four existing company-research backend test files named in the tasks below.
- Do not modify frontend/openapi.json or any public response schema.

### Task 1: Define the Alphabet historical-boundary descriptor

**Files:**
- Create: backend/app/underwriting/services/company_research_boundary.py
- Create: backend/tests/underwriting/test_company_research_boundary.py

- [ ] **Step 1: Write the failing boundary tests**

~~~python
from datetime import UTC, datetime, timedelta
import re

import pytest

from app.models.ledger import ValidationError
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.services.company_research_boundary import (
    resolve_alphabet_company_research_boundary,
)


def test_boundary_normalizes_later_request_to_authenticated_fixture() -> None:
    fixture = load_alphabet_golden_case_fixture()
    later = resolve_alphabet_company_research_boundary(
        datetime(2026, 8, 28, tzinfo=UTC)
    )
    exact = resolve_alphabet_company_research_boundary(fixture.cutoff)

    assert later == exact
    assert later.cutoff_at == fixture.cutoff
    assert later.basis_input.source_manifest_hash == fixture.content_hash
    assert later.basis_content_hash
    for value in (
        later.basis_input.source_manifest_hash,
        later.basis_input.definition_bundle_hash,
        later.basis_input.parser_bundle_hash,
        later.basis_content_hash,
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_boundary_rejects_request_before_authenticated_fixture() -> None:
    fixture = load_alphabet_golden_case_fixture()

    with pytest.raises(
        ValidationError,
        match="requested cutoff precedes the authenticated Alphabet fixture",
    ):
        resolve_alphabet_company_research_boundary(
            fixture.cutoff - timedelta(seconds=1)
        )
~~~

- [ ] **Step 2: Run the new test and verify RED**

Run:

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_boundary.py
~~~

Expected: collection fails because company_research_boundary does not exist.

- [ ] **Step 3: Implement the pure descriptor**

Create the module with this complete public seam:

~~~python
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Mapping

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import (
    AlphabetCompanyResearchAdapter,
)
from app.underwriting.domain.product_contracts import ProductHistoricalBasisInput
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash

_BASIS_SCHEMA = "product.historical-basis.v1"
_DEFINITION_SCHEMA = "company-research.definition-bundle.v1"
_PARSER_SCHEMA = "company-research.parser-bundle.v1"
_PARSER_STRATEGY = "alphabet-golden-case-parser.v1"
_FIXTURE_SCHEMAS = (
    "alphabet.golden-case.manifest.v1",
    "alphabet.golden-case.business-map.v1",
    "alphabet.golden-case.source-facts.v1",
    "alphabet.golden-case.market-inputs.v1",
    "alphabet.golden-case.strategy-assumptions.v1",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("requested cutoff must be timezone-aware")
    return value.astimezone(UTC)


def _json_contract(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_contract(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_json_contract(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CompanyResearchHistoricalBoundary:
    cutoff_at: datetime
    basis_input: ProductHistoricalBasisInput
    basis_content_hash: str


def resolve_alphabet_company_research_boundary(
    requested_cutoff_at: datetime,
) -> CompanyResearchHistoricalBoundary:
    requested = _utc(requested_cutoff_at)
    fixture = load_alphabet_golden_case_fixture()
    if requested < fixture.cutoff:
        raise ValidationError(
            "requested cutoff precedes the authenticated Alphabet fixture"
        )
    template = AlphabetCompanyResearchAdapter().model_template()
    definition_hash = canonical_hash(
        {
            "schema_version": _DEFINITION_SCHEMA,
            "model_template": _json_contract(asdict(template)),
        }
    )
    parser_hash = canonical_hash(
        {
            "schema_version": _PARSER_SCHEMA,
            "parser_strategy": _PARSER_STRATEGY,
            "fixture_schema_versions": _FIXTURE_SCHEMAS,
        }
    )
    basis_input = ProductHistoricalBasisInput(
        cutoff_at=fixture.cutoff,
        source_manifest_hash=fixture.content_hash,
        definition_bundle_hash=definition_hash,
        parser_bundle_hash=parser_hash,
    )
    basis_content_hash = canonical_hash(
        {
            "schema_version": _BASIS_SCHEMA,
            "cutoff_at": fixture.cutoff.isoformat(),
            "source_manifest_hash": basis_input.source_manifest_hash,
            "definition_bundle_hash": definition_hash,
            "parser_bundle_hash": parser_hash,
        }
    )
    return CompanyResearchHistoricalBoundary(
        cutoff_at=fixture.cutoff,
        basis_input=basis_input,
        basis_content_hash=basis_content_hash,
    )
~~~

- [ ] **Step 4: Run the boundary tests and verify GREEN**

Run the command from Step 2.

Expected: all boundary tests pass.

- [ ] **Step 5: Commit**

~~~bash
git add backend/app/underwriting/services/company_research_boundary.py backend/tests/underwriting/test_company_research_boundary.py
git commit -m "feat: define company research historical boundary"
~~~

### Task 2: Bind HistoricalBasis during initialization

**Files:**
- Modify: backend/app/underwriting/services/company_research_initializer.py
- Modify: backend/tests/underwriting/test_company_research_initializer.py

- [ ] **Step 1: Write RED initialization tests**

Set the initializer test clock to datetime(2026, 8, 28, tzinfo=UTC) and retain GOVERNED_CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC). Add:

~~~python
from app.underwriting.persistence.models import UnderwritingHistoricalBasis


def test_preview_normalizes_current_request_to_governed_cutoff(session) -> None:
    initializer, alphabet = _initializer(session)

    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)

    assert preview.cutoff_at == GOVERNED_CUTOFF


def test_preview_rejects_a_cutoff_before_the_fixture(session) -> None:
    initializer, alphabet = _initializer(session)

    with pytest.raises(ValidationError, match="requested cutoff precedes"):
        initializer.preview(
            company_id=alphabet.id,
            cutoff_at=GOVERNED_CUTOFF - timedelta(seconds=1),
        )
~~~

Extend the complete-foundation test:

~~~python
assert result.basis.id == result.draft.content.historical_basis_id
assert result.basis.cutoff.replace(tzinfo=UTC) == GOVERNED_CUTOFF
assert result.mandate.effective_at.replace(tzinfo=UTC) == NOW
assert result.mandate.effective_at.replace(tzinfo=UTC) != (
    result.basis.cutoff.replace(tzinfo=UTC)
)
~~~

Add UnderwritingHistoricalBasis to the rollback count helper. Extend same-key and fresh-session replay tests to assert the original basis ID is returned and only one basis row exists.

- [ ] **Step 2: Run initializer tests and verify RED**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_initializer.py
~~~

Expected: preview still returns the request time and CompanyResearchInitialization has no basis.

- [ ] **Step 3: Implement normalized preview and atomic basis creation**

Add basis: UnderwritingHistoricalBasis to CompanyResearchInitialization. In _preview, load the company, resolve its supported adapter, resolve the boundary, and use boundary.cutoff_at for identity/security queries and build_company_research_preview.

In _create_initialization, use this order:

~~~python
boundary = resolve_alphabet_company_research_boundary(preview.cutoff_at)
project = self._products.create_project(
    primary_company_id=preview.company.object_id,
    target_security_ids=tuple(item.object_id for item in preview.securities),
)
basis = self._products.create_historical_basis(boundary.basis_input)
mandate = self._products.append_product_mandate(
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
    effective_at=self._created_at(),
    expires_at=None,
    expected_parent_id=None,
)
~~~

Keep scope and agenda creation unchanged. Create the draft as:

~~~python
draft = self._drafts.create(
    project.id,
    initial_content=WorkspaceDraftContent(
        mandate_id=mandate.id,
        scope_id=scope.id,
        agenda_id=agenda.id,
        historical_basis_id=basis.id,
    ),
)
~~~

Return basis in CompanyResearchInitialization. In _existing_result, require historical_basis_id, load it with self._products.historical_basis, reject a missing/invalid basis as an incomplete foundation, and return it. Keep everything inside the existing nested transaction.

- [ ] **Step 4: Run boundary and initializer tests**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_boundary.py tests/underwriting/test_company_research_initializer.py
~~~

Expected: both files pass, including rollback and concurrency tests.

- [ ] **Step 5: Commit**

~~~bash
git add backend/app/underwriting/services/company_research_initializer.py backend/tests/underwriting/test_company_research_initializer.py
git commit -m "fix: bind basis during company research initialization"
~~~

### Task 3: Enforce the basis-owned model boundary

**Files:**
- Modify: backend/app/underwriting/persistence/company_research_repository.py
- Modify: backend/tests/underwriting/test_company_research_persistence.py

- [ ] **Step 1: Convert persistence fixtures and write RED cases**

In _repository_with_preparation, create ProductHistoricalBasisInput(NOW, "2" * 64, "3" * 64, "4" * 64) through ResearchProjectService.create_historical_basis and bind it in WorkspaceDraftContent. Include fixture_content_hash: "2" * 64 in the evidence payload.

Add:

~~~python
def test_model_bundle_uses_basis_when_mandate_time_differs(session) -> None:
    repository, _project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    draft = ProductRepository(session).workspace_draft(preparation.project_id)
    content = WorkspaceDraftService._content(draft.content)
    mandate = session.get(UnderwritingMandateVersion, content.mandate_id)
    mandate.effective_at = NOW + timedelta(days=30)

    updated, _rows = _complete_model_bundle(repository, preparation, bundle)

    assert updated.status == "awaiting_judgment_review"


def test_model_bundle_rejects_missing_historical_basis(session) -> None:
    repository, _project, preparation, _job, _evidence, _gaps, bundle = (
        _repository_with_model_job(session)
    )
    draft = ProductRepository(session).workspace_draft(preparation.project_id)
    WorkspaceDraftService(session, now=lambda: NOW).patch(
        preparation.project_id,
        expected_lock_version=draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=None),
    )
    stale_fixed = replace(
        bundle,
        workspace_draft_lock_version=draft.lock_version + 1,
    )

    with pytest.raises(ValidationError, match="historical basis is missing"):
        _complete_model_bundle(repository, preparation, stale_fixed)
~~~

Add cases for unknown basis ID, cutoff mismatch, source-manifest mismatch, corrupted basis content hash, and a newer mandate that cannot substitute for a missing basis.

- [ ] **Step 2: Run persistence tests and verify RED**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_persistence.py
~~~

Expected: the differing-mandate case fails and missing basis still reads the mandate.

- [ ] **Step 3: Replace mandate fallback with basis validation**

Add:

~~~python
@classmethod
def evidence_source_manifest_hash(
    cls, artifact: CompanyResearchArtifactVersion
) -> str:
    value = (
        artifact.payload.get("fixture_content_hash")
        if isinstance(artifact.payload, dict)
        else None
    )
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValidationError("reviewed evidence source manifest is invalid")
    return value
~~~

Extend validate_workspace_market_boundary with expected_source_manifest_hash. Replace mandate loading with:

~~~python
if content.historical_basis_id is None:
    raise ValidationError("company research historical basis is missing")
basis = ProductRepository(self._session).product_basis(
    content.historical_basis_id
)
if basis is None:
    raise ValidationError("company research historical basis is invalid")
cutoff = self._persisted_utc(basis.cutoff)
expected_cutoff = self._stored_datetime(
    expected_cutoff_at, "expected_cutoff_at"
)
expected_manifest = self._require_hash(
    expected_source_manifest_hash,
    "expected_source_manifest_hash",
)
if cutoff != expected_cutoff:
    raise ValidationError(
        "company research historical basis cutoff does not match reviewed evidence"
    )
if basis.source_manifest_hash != expected_manifest:
    raise ValidationError(
        "company research historical basis source does not match reviewed evidence"
    )
if basis.definition_bundle_hash is None or basis.parser_bundle_hash is None:
    raise ValidationError("company research historical basis is invalid")
expected_content_hash = canonical_hash(
    {
        "schema_version": "product.historical-basis.v1",
        "cutoff_at": cutoff.isoformat(),
        "source_manifest_hash": basis.source_manifest_hash,
        "definition_bundle_hash": basis.definition_bundle_hash,
        "parser_bundle_hash": basis.parser_bundle_hash,
    }
)
if basis.content_hash != expected_content_hash:
    raise ValidationError("company research historical basis is invalid")
~~~

Keep all existing draft-reference and snapshot-binding checks, validate availability against cutoff, and return cutoff. In complete_model_bundle derive evidence cutoff and evidence manifest and pass both.

- [ ] **Step 4: Run persistence plus model success tests**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_persistence.py
.venv/bin/pytest -q tests/underwriting/test_company_research_worker.py::test_worker_builds_all_model_artifacts_after_last_evidence_review
~~~

Expected: both commands pass and mandate time is irrelevant.

- [ ] **Step 5: Commit**

~~~bash
git add backend/app/underwriting/persistence/company_research_repository.py backend/tests/underwriting/test_company_research_persistence.py
git commit -m "fix: validate company model against historical basis"
~~~

### Task 4: Add fail-closed legacy recovery

**Files:**
- Modify: backend/app/underwriting/persistence/product_repository.py
- Modify: backend/app/underwriting/persistence/company_research_repository.py
- Create: backend/app/underwriting/services/company_research_basis_recovery.py
- Modify: backend/app/underwriting/services/company_research_initializer.py
- Modify: backend/tests/underwriting/test_company_research_worker.py
- Modify: backend/tests/underwriting/test_company_research_api.py

- [ ] **Step 1: Write RED recovery tests**

Build a helper that initializes Alphabet, prepares evidence, records six confirmed decisions and one rejected decision, clears only historical_basis_id, and runs the model claim to blocked model_bundle with validation_failed.

Snapshot and assert:

~~~python
before_evidence = repository.current_artifact(project_id, "evidence_index")
before_gaps = repository.current_artifact(project_id, "research_gaps")
before_decisions = tuple(
    (fact["fact_key"], fact["review_decision"])
    for fact in before_evidence.payload["facts"]
)
before_draft = WorkspaceDraftService(session, now=lambda: NOW).read(project_id)

retried = CompanyResearchPreparationService(
    session, now=lambda: NOW
).retry(project_id=project_id)

after_draft = WorkspaceDraftService(session, now=lambda: NOW).read(project_id)
after_evidence = repository.current_artifact(project_id, "evidence_index")
after_gaps = repository.current_artifact(project_id, "research_gaps")
assert retried.project.id == project_id
assert retried.preparation.status == "building_model"
assert retried.preparation.current_step == "model_bundle"
assert after_draft.content.historical_basis_id is not None
assert after_draft.lock_version == before_draft.lock_version + 1
assert after_draft.content.model_copy(
    update={"historical_basis_id": None}
) == before_draft.content
assert (after_evidence.id, after_evidence.content_hash) == (
    before_evidence.id,
    before_evidence.content_hash,
)
assert (after_gaps.id, after_gaps.content_hash) == (
    before_gaps.id,
    before_gaps.content_hash,
)
assert tuple(
    (fact["fact_key"], fact["review_decision"])
    for fact in after_evidence.payload["facts"]
) == before_decisions
~~~

Add rejection cases for an unreviewed fact, wrong cutoff, wrong fixture hash, wrong company/security identities, missing mandate/scope/agenda, incomplete market refs, stale draft, and an already-bound mismatched basis. Add an exact-basis idempotence case with no duplicate basis/recovery event.

Add a file-backed SQLite concurrency test using two sessions and a Barrier. Both sessions call retry for the same blocked project. Assert exactly one call returns building_model, the other receives the existing stale/not-recoverable validation result, one basis is bound, at most one historical_basis_recovered event exists, and evidence/gap rows and decisions are unchanged.

In test_company_research_api.py, write test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions before implementation. Drive initialization, source work, six confirmed and one rejected public reviews, basis removal, the blocked model claim, POST retry, the second model claim, and the final workspace GET. Assert HTTP 202, in-place IDs/hashes/decisions, and final awaiting_judgment_review at 85%.

Set that file's NOW constant to datetime(2026, 8, 28, tzinfo=UTC), so existing default preview calls request a time after the authenticated fixture. Add these imports:

~~~python
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
~~~

Use this complete test structure, retaining the repository's existing response helpers and imports:

~~~python
def test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions(
    api_client, session
) -> None:
    company_id = _alphabet_id(session)
    preview = _preview(api_client, company_id, cutoff_at=NOW)
    initialized = _initialize(
        api_client,
        company_id,
        preview["preview_hash"],
        cutoff_at=datetime.fromisoformat(preview["cutoff_at"]),
    )
    assert initialized.status_code == 201, initialized.text
    project_id = initialized.json()["project_id"]
    preparation_id = initialized.json()["preparation"]["id"]

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    source_claim = worker.claim_next()
    assert source_claim is not None
    assert worker.run_claim(source_claim) == "awaiting_evidence_review"
    evidence = next(
        item
        for item in api_client.get(
            f"{BASE}/projects/{project_id}/workspace"
        ).json()["artifacts"]
        if item["kind"] == "evidence_index"
    )
    current = evidence
    for index, fact in enumerate(evidence["payload"]["facts"]):
        reviewed = api_client.post(
            f"{BASE}/projects/{project_id}/evidence-reviews",
            json={
                "evidence_artifact_id": current["id"],
                "fact_key": fact["fact_key"],
                "decision": "rejected" if index == 0 else "confirmed",
                "expected_head_id": current["id"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()["evidence_artifact"]

    drafts = WorkspaceDraftService(session, now=lambda: NOW)
    initialized_draft = drafts.read(UUID(project_id))
    assert initialized_draft is not None
    drafts.patch(
        UUID(project_id),
        expected_lock_version=initialized_draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=None),
    )
    session.commit()

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    model_claim = worker.claim_next()
    assert model_claim is not None and model_claim.step == "model_bundle"
    assert worker.run_claim(model_claim) == "discarded"
    blocked = session.get(CompanyResearchPreparation, UUID(preparation_id))
    blocked_job = session.get(Job, model_claim.job_id)
    assert blocked is not None and (
        blocked.status,
        blocked.current_step,
        blocked.progress,
        blocked.last_error_code,
    ) == ("blocked", "model_bundle", 30, "validation_failed")
    assert blocked_job is not None
    assert "historical basis is missing" in blocked_job.error

    repository = CompanyResearchRepository(session)
    before_evidence = repository.current_artifact(
        UUID(project_id), "evidence_index"
    )
    before_gaps = repository.current_artifact(
        UUID(project_id), "research_gaps"
    )
    before_draft = drafts.read(UUID(project_id))
    assert before_evidence is not None
    assert before_gaps is not None
    assert before_draft is not None
    decisions = tuple(
        (fact["fact_key"], fact["review_decision"])
        for fact in before_evidence.payload["facts"]
    )
    before_without_basis = before_draft.content.model_copy(
        update={"historical_basis_id": None}
    )

    retried = api_client.post(f"{BASE}/projects/{project_id}/retry")
    assert retried.status_code == 202, retried.text
    assert retried.json()["preparation"]["status"] == "building_model"
    assert retried.json()["preparation"]["current_step"] == "model_bundle"
    assert retried.json()["preparation"]["progress"] == 25

    after_draft = drafts.read(UUID(project_id))
    after_evidence = repository.current_artifact(
        UUID(project_id), "evidence_index"
    )
    after_gaps = repository.current_artifact(
        UUID(project_id), "research_gaps"
    )
    assert after_draft is not None
    assert after_draft.content.historical_basis_id is not None
    assert after_draft.lock_version == before_draft.lock_version + 1
    assert after_draft.content.model_copy(
        update={"historical_basis_id": None}
    ) == before_without_basis
    assert (after_evidence.id, after_evidence.content_hash) == (
        before_evidence.id,
        before_evidence.content_hash,
    )
    assert (after_gaps.id, after_gaps.content_hash) == (
        before_gaps.id,
        before_gaps.content_hash,
    )

    retry_worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    retry_claim = retry_worker.claim_next()
    assert retry_claim is not None and retry_claim.step == "model_bundle"
    assert retry_worker.run_claim(retry_claim) == "awaiting_judgment_review"
    final_workspace = api_client.get(
        f"{BASE}/projects/{project_id}/workspace"
    ).json()
    final_evidence = next(
        item
        for item in final_workspace["artifacts"]
        if item["kind"] == "evidence_index"
    )
    final_decisions = tuple(
        (fact["fact_key"], fact["review_decision"])
        for fact in final_evidence["payload"]["facts"]
    )
    assert len(decisions) == 7
    assert tuple(value for _, value in decisions).count("confirmed") == 6
    assert tuple(value for _, value in decisions).count("rejected") == 1
    assert final_workspace["preparation"]["status"] == (
        "awaiting_judgment_review"
    )
    assert final_workspace["preparation"]["current_step"] == (
        "judgment_context"
    )
    assert final_workspace["preparation"]["progress"] == 85
    assert final_workspace["preparation"]["error"] is None
    assert final_decisions == decisions
~~~

- [ ] **Step 2: Run recovery tests and verify RED**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_worker.py -k historical_basis_recovery
.venv/bin/pytest -q tests/underwriting/test_company_research_api.py::test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions
~~~

Expected: retry rejects blocked status because only recoverable_failure is accepted.

- [ ] **Step 3: Add exact basis lookup and locked recovery state**

In ProductRepository add:

~~~python
def product_basis_by_content_hash(
    self, content_hash: str
) -> UnderwritingHistoricalBasis | None:
    return self._session.scalar(
        select(UnderwritingHistoricalBasis)
        .where(
            UnderwritingHistoricalBasis.content_hash == content_hash,
            UnderwritingHistoricalBasis.boundary_schema_version
            == "product.historical-basis.v1",
            UnderwritingHistoricalBasis.price_as_of.is_(None),
        )
        .order_by(
            UnderwritingHistoricalBasis.created_at,
            UnderwritingHistoricalBasis.id,
        )
        .limit(1)
    )
~~~

In CompanyResearchRepository define frozen CompanyResearchBasisRecoveryState with preparation, job, draft, evidence, and research_gaps. lock_basis_recovery_state must reserve the SQLite writer, lock in that order, lock both current artifact heads, and reject a missing member.

~~~python
@dataclass(frozen=True, slots=True)
class CompanyResearchBasisRecoveryState:
    preparation: CompanyResearchPreparation
    job: Job
    draft: UnderwritingWorkspaceDraft
    evidence: CompanyResearchArtifactVersion
    research_gaps: CompanyResearchArtifactVersion


def lock_basis_recovery_state(
    self, preparation_id: UUID
) -> CompanyResearchBasisRecoveryState:
    self._reserve_sqlite_writer_before_ownership_read()
    preparation = self._preparation_for_update(preparation_id)
    if preparation is None:
        raise ValidationError("company research preparation not found")
    job = self._locked_prepare_job(preparation)
    draft = self._workspace_draft_for_update(preparation.project_id)
    evidence = self.current_artifact(
        preparation.project_id, "evidence_index", lock=True
    )
    gaps = self.current_artifact(
        preparation.project_id, "research_gaps", lock=True
    )
    if draft is None or evidence is None or gaps is None:
        raise ValidationError(
            "company research historical basis recovery inputs are incomplete"
        )
    return CompanyResearchBasisRecoveryState(
        preparation, job, draft, evidence, gaps
    )
~~~

Extend requeue_recoverable_preparation with expected_recovered_basis_id: UUID | None = None. Preserve ordinary retry when absent. When present require exactly:

~~~python
preparation.status == "blocked"
preparation.current_step == "model_bundle"
preparation.last_error_code == "validation_failed"
job.status == "failed"
job.step == "model_bundle"
WorkspaceDraftService._content(draft.content).historical_basis_id
    == expected_recovered_basis_id
~~~

Then apply the existing model retry transition: building_model, progress 25, cleared error/retry time, queued job, and one reserved next attempt.

- [ ] **Step 4: Implement the recovery service**

Create CompanyResearchHistoricalBasisRecovery with:

~~~python
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_boundary import (
    CompanyResearchHistoricalBoundary,
    resolve_alphabet_company_research_boundary,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
~~~

Define the constructor and clock boundary exactly:

~~~python
class CompanyResearchHistoricalBasisRecovery:
    def __init__(
        self, session: Session, *, now: Callable[[], datetime]
    ) -> None:
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._product_repository = ProductRepository(session)
        self._products = ResearchProjectService(session, now=now)
        self._drafts = WorkspaceDraftService(session, now=now)

    def _now_utc(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)
~~~

~~~python
def recover(self, *, preparation_id: UUID) -> UUID:
    state = self._repository.lock_basis_recovery_state(preparation_id)
    self._require_blocked_model_failure(state.preparation, state.job)
    project = self._products.project(state.preparation.project_id)
    if project is None:
        raise ValidationError("company research recovery project is missing")
    company = self._product_repository.object(project.primary_company_id)
    securities = tuple(
        self._product_repository.object(item)
        for item in project.target_security_ids
    )
    if company is None or company.external_key != "US:ALPHABET:COMPANY":
        raise ValidationError("company research recovery identity is invalid")
    if any(item is None for item in securities):
        raise ValidationError("company research recovery identity is invalid")

    cutoff = self._repository.evidence_cutoff(state.evidence)
    boundary = resolve_alphabet_company_research_boundary(cutoff)
    self._validate_evidence_and_gap_contracts(
        project,
        company,
        securities,
        state.evidence.payload,
        state.research_gaps.payload,
        boundary,
    )
    content = WorkspaceDraftService._content(state.draft.content)
    self._validate_foundation_and_market_refs(content)

    if content.historical_basis_id is not None:
        basis = self._products.historical_basis(
            content.historical_basis_id
        )
        self._require_exact_basis(basis, boundary)
        return basis.id

    basis = self._product_repository.product_basis_by_content_hash(
        boundary.basis_content_hash
    )
    if basis is None:
        basis = self._products.create_historical_basis(boundary.basis_input)
    self._require_exact_basis(basis, boundary)
    updated = self._drafts.patch(
        project.id,
        expected_lock_version=state.draft.lock_version,
        patch=WorkspaceDraftPatch(historical_basis_id=basis.id),
    )
    self._repository.append_event(
        preparation_id=state.preparation.id,
        event_type="historical_basis_recovered",
        payload={
            "basis_id": str(basis.id),
            "cutoff_at": boundary.cutoff_at.isoformat(),
            "source_manifest_hash": boundary.basis_input.source_manifest_hash,
            "prior_draft_lock_version": state.draft.lock_version,
            "draft_lock_version": updated.lock_version,
        },
        created_at=self._now_utc(),
    )
    return basis.id
~~~

The called validators use exact equality: every fact is terminally reviewed; evidence/gaps carry boundary manifest/company; evidence and project security keys equal adapter keys; mandate/scope/agenda and price/FX/capital/rights references are present; existing basis matches cutoff, all three hashes, and content hash. Patch no field except historical_basis_id.

Define those validators in the new service rather than leaving implicit checks:

~~~python
@staticmethod
def _require_blocked_model_failure(preparation, job) -> None:
    if (
        preparation.status != "blocked"
        or preparation.current_step != "model_bundle"
        or preparation.last_error_code != "validation_failed"
        or job.status != "failed"
        or job.step != "model_bundle"
    ):
        raise ValidationError(
            "company research preparation is not eligible for basis recovery"
        )


@staticmethod
def _validate_evidence_and_gap_contracts(
    project,
    company,
    securities,
    evidence_payload: Mapping[str, object],
    gaps_payload: Mapping[str, object],
    boundary: CompanyResearchHistoricalBoundary,
) -> None:
    facts = evidence_payload.get("facts")
    fixture = load_alphabet_golden_case_fixture()
    security_keys = tuple(sorted(item.external_key for item in securities))
    if (
        evidence_payload.get("fixture_content_hash")
        != boundary.basis_input.source_manifest_hash
        or gaps_payload.get("fixture_content_hash")
        != boundary.basis_input.source_manifest_hash
        or evidence_payload.get("company_external_key")
        != company.external_key
        or gaps_payload.get("company_external_key") != company.external_key
        or tuple(evidence_payload.get("security_external_keys", ()))
        != fixture.security_external_keys
        or security_keys != fixture.security_external_keys
        or not isinstance(facts, list)
        or not facts
        or any(
            not isinstance(fact, Mapping)
            or fact.get("review_decision") not in {"confirmed", "rejected"}
            for fact in facts
        )
    ):
        raise ValidationError(
            "company research historical basis recovery evidence is invalid"
        )


@staticmethod
def _validate_foundation_and_market_refs(
    content: WorkspaceDraftContent,
) -> None:
    if (
        content.mandate_id is None
        or content.scope_id is None
        or content.agenda_id is None
        or not content.price_snapshot_ids
        or not content.fx_snapshot_ids
        or content.capital_structure_snapshot_id is None
        or not content.security_rights_ids
    ):
        raise ValidationError(
            "company research historical basis recovery draft is incomplete"
        )


@staticmethod
def _require_exact_basis(
    basis,
    boundary: CompanyResearchHistoricalBoundary,
) -> None:
    expected = boundary.basis_input
    if (
        basis is None
        or CompanyResearchHistoricalBasisRecovery._stored_utc(basis.cutoff)
        != boundary.cutoff_at
        or basis.source_manifest_hash != expected.source_manifest_hash
        or basis.definition_bundle_hash != expected.definition_bundle_hash
        or basis.parser_bundle_hash != expected.parser_bundle_hash
        or basis.content_hash != boundary.basis_content_hash
    ):
        raise ValidationError(
            "company research historical basis recovery found a conflicting basis"
        )


@staticmethod
def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
~~~

- [ ] **Step 5: Wire recovery into retry**

Construct the recovery service in CompanyResearchPreparationService. In retry:

~~~python
expected_basis_id = None
if current.preparation.status == "blocked":
    expected_basis_id = self._basis_recovery.recover(
        preparation_id=current.preparation.id
    )
preparation = self._company_repository.requeue_recoverable_preparation(
    current.preparation.id,
    updated_at=self._now_utc(),
    expected_recovered_basis_id=expected_basis_id,
)
~~~

Keep backoff validation for ordinary recoverable failures. Append retry_queued only after recovery and requeue succeed.

- [ ] **Step 6: Run recovery and ordinary retry tests**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_worker.py -k "retry or historical_basis_recovery"
.venv/bin/pytest -q tests/underwriting/test_company_research_api.py -k retry
~~~

Expected: new and existing retry tests pass.

- [ ] **Step 7: Commit**

~~~bash
git add backend/app/underwriting/persistence/product_repository.py backend/app/underwriting/persistence/company_research_repository.py backend/app/underwriting/services/company_research_basis_recovery.py backend/app/underwriting/services/company_research_initializer.py backend/tests/underwriting/test_company_research_worker.py backend/tests/underwriting/test_company_research_api.py
git commit -m "fix: recover missing company research basis"
~~~

### Task 5: Preserve actionable validation diagnostics

**Files:**
- Modify: backend/app/underwriting/services/company_research_preparation.py
- Modify: backend/tests/underwriting/test_company_research_worker.py

- [ ] **Step 1: Write RED diagnostic assertions**

For missing basis assert:

~~~python
assert preparation.last_error_code == "validation_failed"
assert job.error == (
    "validation_failed: company research historical basis is missing"
)
blocked = next(
    event
    for event in events
    if event.event_type == "model_preparation_blocked"
)
assert blocked.payload == {
    "code": "validation_failed",
    "message": "company research historical basis is missing",
}
~~~

Add a test with a validation message longer than 512 characters containing newlines; assert one-line truncation, a total job.error length no greater than 512, an event message no greater than 512, and unchanged last_error_code.

- [ ] **Step 2: Run diagnostic tests and verify RED**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_worker.py -k "validation_diagnostic or historical_basis_recovery"
~~~

Expected: job and event contain only validation_failed.

- [ ] **Step 3: Persist a bounded safe message**

Add:

~~~python
_VALIDATION_MESSAGE_LIMIT = 512


@staticmethod
def _safe_validation_message(message: str) -> str:
    normalized = " ".join(message.split())
    return normalized[:_VALIDATION_MESSAGE_LIMIT]
~~~

Change _block to accept error_message: str | None = None. Keep preparation.last_error_code = error_code. Store job.error = error_code without a message, otherwise truncate error_code plus ": " plus the safe message to _VALIDATION_MESSAGE_LIMIT. Put the safe message in JobEvent and the company event payload. In both validation exception branches capture the exception and pass str(exc). Provider exceptions retain their fixed sanitized code.

- [ ] **Step 4: Run the whole worker file**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_worker.py
~~~

Expected: all worker tests pass.

- [ ] **Step 5: Commit**

~~~bash
git add backend/app/underwriting/services/company_research_preparation.py backend/tests/underwriting/test_company_research_worker.py
git commit -m "fix: retain company preparation validation detail"
~~~

### Task 6: Re-run public recovery and invariant preservation

**Files:**
- No new changes expected; the public RED test was added in Task 4.

- [ ] **Step 1: Audit the end-to-end API regression**

Confirm test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions previews and initializes, runs source preparation, reviews seven facts through the public API with exactly one rejected, clears only historical_basis_id through WorkspaceDraftService.patch, snapshots both artifact IDs/versions/hashes and every other draft field, and runs the model claim to blocked 30% model_bundle.

Then POST retry and assert HTTP 202, building_model, model_bundle, progress 25. Assert only basis and draft lock changed; artifacts and ordered decisions remain exact. Run the next worker claim and assert awaiting_judgment_review, judgment_context, progress 85.

Use these final assertions:

~~~python
assert len(decisions) == 7
assert tuple(decisions.values()).count("confirmed") == 6
assert tuple(decisions.values()).count("rejected") == 1
assert final_workspace["preparation"]["status"] == (
    "awaiting_judgment_review"
)
assert final_workspace["preparation"]["current_step"] == "judgment_context"
assert final_workspace["preparation"]["progress"] == 85
assert final_workspace["preparation"]["error"] is None
assert final_decisions == decisions
~~~

- [ ] **Step 2: Run the public API recovery test**

~~~bash
cd backend
.venv/bin/pytest -q tests/underwriting/test_company_research_api.py::test_retry_recovers_legacy_basis_and_preserves_seven_review_decisions
~~~

Expected: PASS. If it fails, return to the responsible Task 1–5 test, reproduce RED there, make the specified minimal implementation, and keep every equality assertion.

- [ ] **Step 3: Run the focused backend suite**

~~~bash
cd backend
.venv/bin/pytest -q   tests/underwriting/test_company_research_boundary.py   tests/underwriting/test_company_research_initializer.py   tests/underwriting/test_company_research_persistence.py   tests/underwriting/test_company_research_worker.py   tests/underwriting/test_company_research_api.py
~~~

Expected: all selected tests pass; the existing PostgreSQL-only case may skip without a PostgreSQL test URL.

- [ ] **Step 4: Confirm the Task 4 commit contains the public regression**

~~~bash
git show --name-only --format= HEAD
~~~

Expected: test_company_research_api.py is present with the recovery implementation files and no unrelated file.

### Task 7: Verify, review, integrate, and restore the live project

**Files:**
- No further production files expected.
- Runtime data changes occur only after verified integration.

- [ ] **Step 1: Run complete backend verification**

~~~bash
cd backend
.venv/bin/pytest -q
~~~

Expected: all non-environment-gated tests pass.

- [ ] **Step 2: Run frontend compatibility checks**

~~~bash
cd frontend
npm test --   src/data/InvestmentResearchApi.test.ts   src/features/investment-research/NewResearchPage.test.tsx   src/features/investment-research/ResearchWorkbenchPage.test.tsx   src/features/investment-research/companyResearchView.test.ts
npm run typecheck
npm run build
~~~

Expected: four Vitest files pass, TypeScript has no errors, and Vite builds.

- [ ] **Step 3: Audit the branch**

~~~bash
git diff --check 7707036...HEAD
git status --short
git diff --name-only 7707036...HEAD
~~~

Confirm no company cutoff reads mandate effective time, recovery writes only the basis field and lifecycle events, blocked recovery accepts only the exact legacy state, frontend/openapi.json is unchanged, and the worktree is clean.

- [ ] **Step 4: Run required completion skills**

Invoke verification-before-completion and requesting-code-review. Resolve every Important finding with a new failing test, minimal correction, and rerun of Steps 1–3.

- [ ] **Step 5: Integrate using finishing-a-development-branch**

Use the integration option explicitly approved by the user. Preserve the unrelated dirty files in the main workspace and verify neither is staged nor overwritten.

- [ ] **Step 6: Snapshot the live project**

From the main workspace:

~~~bash
recovery_snapshot_dir=$(mktemp -d)
curl --fail --silent --show-error   http://127.0.0.1:8080/api/underwriting/v1/product/company-research/projects/19a046e8-2f48-4e1e-949d-cdd84a66bb5e/workspace   -o "$recovery_snapshot_dir/before.json"
jq -S '{project_id, preparation, draft, evidence: (.artifacts[] | select(.kind == "evidence_index") | {id, version, content_hash, facts: [.payload.facts[] | {fact_key, review_decision}]}), gaps: (.artifacts[] | select(.kind == "research_gaps") | {id, version, content_hash})}'   "$recovery_snapshot_dir/before.json" > "$recovery_snapshot_dir/before-invariants.json"
~~~

Expected: blocked model_bundle with seven decisions, six confirmed and one rejected.

- [ ] **Step 7: Rebuild and verify runtime**

~~~bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh
~~~

Expected: API, frontend, company worker, and PostgreSQL are healthy on the existing volume.

- [ ] **Step 8: Retry exactly once**

~~~bash
curl --fail --silent --show-error -X POST   http://127.0.0.1:8080/api/underwriting/v1/product/company-research/projects/19a046e8-2f48-4e1e-949d-cdd84a66bb5e/retry   -o "$recovery_snapshot_dir/retry.json"
jq '{status: .preparation.status, step: .preparation.current_step, progress: .preparation.progress}'   "$recovery_snapshot_dir/retry.json"
~~~

Expected immediately: building_model, model_bundle, progress 25.

- [ ] **Step 9: Verify basis and judgment-review completion**

Query the draft/basis join inside the one-click PostgreSQL container:

~~~bash
docker compose -f docker-compose.one-click.yml --env-file .env --env-file .env.one-click.local exec -T postgres sh -eu -c   'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select d.content->>''historical_basis_id'', b.cutoff, b.source_manifest_hash from uw_workspace_drafts d join uw_historical_bases b on b.id::text = d.content->>''historical_basis_id'' where d.project_id = ''19a046e8-2f48-4e1e-949d-cdd84a66bb5e'';"'
~~~

Expected: one non-null basis at 2026-08-25 23:59:59+00 with a 64-character manifest hash.

Poll the workspace every two seconds for at most 60 seconds. Stop at awaiting_judgment_review, judgment_context, progress 85. If blocked again, inspect its persisted diagnostic and do not issue a second retry.

- [ ] **Step 10: Compare invariants and verify UI**

Fetch after.json and generate the same evidence/gaps projection used before. Project ID, evidence/gap IDs, versions, hashes, fact order, and seven decisions must match before-invariants.json; only preparation state and draft lock/basis may change.

Open http://127.0.0.1:8080/research/projects/19a046e8-2f48-4e1e-949d-cdd84a66bb5e in the in-app browser. Confirm 等待判断审核, 85%, judgment_context, six confirmed and one rejected facts, and all nine modules without a decoder error.
