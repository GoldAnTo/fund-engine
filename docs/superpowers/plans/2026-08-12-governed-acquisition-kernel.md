# Governed Acquisition Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, durable acquisition module that turns one frozen evidence objective into searched, downloaded, immutable, deduplicated, parsed, and automatically admitted evidence from Gildata plus SSE/SZSE official announcements, without modifying `AutoResearchService`.

**Architecture:** Add a deep `AcquisitionModule` seam backed by PostgreSQL jobs, leases, append-only events, source references, raw retrieval artifacts, and automatic-admission decisions. Provider adapters only search and fetch; the module owns policy, cutoff enforcement, persistence, parsing, three-level deduplication, source governance, and automatic evidence admission. The module ships with its own worker and protected HTTP commands so it can be verified before automatic-research integration.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, httpx, existing Gildata client, pypdf/Docling parser adapters, existing OpenAI-compatible extraction client, pytest.

---

## Preconditions and integration baseline

Do not implement this plan from commit `22377db` directly. First create a fresh implementation worktree from the main branch after these branches have been reviewed and integrated:

- `codex/auto-research-correctness`;
- `codex/source-category-successor`.

Record the resulting base commit in the implementation task commentary. Preserve every uncommitted file in the existing worktrees. The implementation branch must be named `codex/governed-acquisition-kernel` and must start clean.

The integrated source-category change uses Alembic revision `0052`; this plan uses `0053` for the acquisition schema and `0054` for automatic-admission uniqueness. If integration adds one of those revisions first, rename the affected migration to the next free revision everywhere before writing its test—never create two Alembic heads accidentally.

Create the implementation worktree's local Python environment before Task 1:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## File map

- Create `backend/app/domain/acquisition.py`: frozen public request/result values and objective enum.
- Create `backend/app/acquisition/policy.py`: versioned B-scope source policy and deterministic query planner.
- Create `backend/app/acquisition/sources.py`: internal adapter interface and normalized search/fetch values.
- Create `backend/app/models/acquisition.py`: operational job plus append-only acquisition ledger records.
- Create `backend/app/repositories/acquisition.py`: job creation, lease fencing, events, references, artifacts, and result queries.
- Create `backend/app/services/acquisition.py`: the three-method external module interface.
- Create `backend/app/services/retrieved_documents.py`: raw-artifact freezing, parsing, and three-level deduplication.
- Create `backend/app/services/automatic_admission.py`: structured gates and publication of automatically admitted evidence.
- Create `backend/app/datasources/gildata/research_source.py`: Gildata search/fetch adapter.
- Create `backend/app/datasources/exchanges/sse.py`: SSE announcement adapter.
- Create `backend/app/datasources/exchanges/szse.py`: SZSE announcement adapter.
- Create `backend/app/datasources/exchanges/http.py`: bounded official-source HTTP transport.
- Create `backend/app/scripts/run_acquisition_worker.py`: lease-aware worker entry point.
- Create `backend/app/api/v1/acquisition.py`: protected create/read/events/results endpoints.
- Create `backend/app/schemas/v1/acquisition.py`: request and response DTOs.
- Modify `backend/app/models/__init__.py`: import acquisition models.
- Modify `backend/app/models/ledger.py`: immutable-table registration and automatic-admission provenance.
- Modify `backend/app/services/atomic_claims.py`: publish a candidate through an automatic decision without fabricating a human review.
- Modify `backend/app/repositories/research.py`: persist automatic provenance on statement/link rows.
- Modify `backend/app/api/v1/router.py`: mount acquisition routes.
- Modify `backend/app/ai/extraction.py`: correct wording so extraction supports either human review or automatic admission.
- Create one Alembic migration for the acquisition schema and provenance columns.

## Non-negotiable invariants

- `AutoResearchService` and `run_research_worker.py` are not modified in this phase.
- Search results are never evidence. A `SourceReference` must be fetched into a frozen `RetrievalArtifact` first.
- Every successful HTTP/provider fetch creates exactly one immutable artifact, even when its bytes are duplicate or parsing fails.
- Secrets, cookies, authorization headers, full request headers, and provider tokens are never persisted.
- A source after the request cutoff, with an unapproved host, or without a publication time is quarantined.
- `automatically_admitted` is not `reviewed`; no `AtomicClaimReview` is fabricated.
- Lease token fencing and uniqueness constraints, not elapsed time alone, prevent stale workers from publishing.
- Unit tests may use fake adapters. The live smoke test may not silently fall back to a fake provider.

### Task 1: Freeze the public acquisition vocabulary and source policy

**Files:**
- Create: `backend/app/domain/acquisition.py`
- Create: `backend/app/acquisition/__init__.py`
- Create: `backend/app/acquisition/policy.py`
- Test: `backend/tests/test_acquisition_contract.py`
- Test: `backend/tests/test_acquisition_policy.py`

- [ ] **Step 1: Write failing contract and policy tests**

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.acquisition.policy import B_SCOPE_POLICY, AcquisitionQueryPlanner
from app.domain.acquisition import AcquisitionRequest, EvidenceObjective


def request() -> AcquisitionRequest:
    return AcquisitionRequest(
        tenant_id="team-a",
        case_id=uuid4(),
        thesis_id=uuid4(),
        research_run_id=None,
        round=1,
        objective=EvidenceObjective.CONTRADICT,
        target_link_role="contradicts",
        thesis_statement="公司收入将在 2026 年增长",
        entity_names=("示例公司",),
        security_codes=("600000",),
        metric_terms=("营业收入",),
        period_start="2026-01-01",
        period_end="2026-12-31",
        cutoff=datetime(2026, 8, 12, tzinfo=UTC),
        allowed_source_roles=frozenset({"company_disclosure", "licensed_provider"}),
        source_policy_version=B_SCOPE_POLICY.version,
        idempotency_key="run:none:thesis:contradict:1",
    )


def test_request_is_frozen_and_timezone_aware():
    value = request()
    with pytest.raises(AttributeError):
        value.round = 2
    assert value.cutoff.utcoffset().total_seconds() == 0


def test_b_scope_policy_never_allows_arbitrary_web_hosts():
    assert B_SCOPE_POLICY.allows_host("www.sse.com.cn")
    assert B_SCOPE_POLICY.allows_host("disc.static.szse.cn")
    assert not B_SCOPE_POLICY.allows_host("example-news.invalid")


def test_contradiction_query_keeps_objective_and_entity():
    planned = AcquisitionQueryPlanner().plan(request(), B_SCOPE_POLICY)
    assert planned
    assert all(item.objective is EvidenceObjective.CONTRADICT for item in planned)
    assert all("示例公司" in item.query or "600000" in item.query for item in planned)
    assert {item.adapter_key for item in planned} == {"gildata", "sse", "szse"}
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_contract.py tests/test_acquisition_policy.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.acquisition'`.

- [ ] **Step 3: Implement frozen contracts and deterministic policy**

Define `EvidenceObjective(str, Enum)` with exactly `support`, `contradict`, `alternative_explanation`, and `verify_rule`. Define frozen/slots dataclasses `AcquisitionPrincipal`, `AcquisitionRequest`, `PlannedQuery`, `AcquisitionJobRef`, `AcquisitionJobView`, and `AdmittedEvidenceRef`. `AcquisitionPrincipal` contains non-empty `tenant_id` and server-resolved `actor`. `AcquisitionRequest.__post_init__` must reject blank identifiers, a naive cutoff, an empty objective scope, unknown source roles, a policy-version mismatch, and an incompatible `target_link_role`: support requires `supports`, contradiction requires `contradicts`, alternative explanation requires `contextualizes`, while verification may freeze either `supports` or `contradicts` from its verification rule.

Define `SourcePolicy` as a frozen dataclass containing `version`, enabled adapter keys, allowed roles, exact hosts, suffix hosts, maximum response bytes, per-adapter page limit, and permission declarations. Instantiate `B_SCOPE_POLICY` with only Gildata, SSE, and SZSE; exact/suffix hosts must cover `query.sse.com.cn`, `www.sse.com.cn`, `www.szse.cn`, and `disc.static.szse.cn` without accepting sibling domains. Suffix matching must use a DNS-label boundary (`host == suffix or host.endswith("." + suffix)`), never a raw string suffix.

Implement `AcquisitionQueryPlanner.plan()` as deterministic string composition from entity, security code, metric, period, and objective-specific terms. It must return at most three queries per adapter and must never accept a host or URL from the request.

- [ ] **Step 4: Run the tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_contract.py tests/test_acquisition_policy.py -v`

Expected: PASS.

```bash
git add backend/app/domain/acquisition.py backend/app/acquisition backend/tests/test_acquisition_contract.py backend/tests/test_acquisition_policy.py
git commit -m "feat: define governed acquisition contract"
```

### Task 2: Add durable acquisition and automatic-provenance records

**Files:**
- Create: `backend/app/models/acquisition.py`
- Create: `backend/alembic/versions/0053_governed_acquisition.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Test: `backend/tests/test_acquisition_schema.py`
- Test: `backend/tests/test_acquisition_postgres.py`

- [ ] **Step 1: Write schema tests**

Assert that `Base.metadata` contains these tables:

```python
EXPECTED = {
    "acquisition_jobs",
    "acquisition_job_events",
    "acquisition_attempts",
    "source_references",
    "retrieval_artifacts",
    "retrieval_artifact_documents",
    "automatic_admission_decisions",
    "acquisition_exceptions",
}
assert EXPECTED <= set(Base.metadata.tables)
```

The PostgreSQL-marked test must migrate from the previous revision to the new revision, inspect all unique/check/foreign-key constraints, and assert UPDATE/DELETE fails for every append-only table while `acquisition_jobs` remains mutable.

- [ ] **Step 2: Run and verify missing-table failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_schema.py -v`

Expected: FAIL because the acquisition tables do not exist.

- [ ] **Step 3: Implement models with explicit constraints**

`AcquisitionJob` is operational and mutable. Required fields: request snapshot JSON, policy snapshot JSON, status, stage, attempt, counters, lease owner/token/expiry, retry time, error code/detail, created/started/finished/updated times. Status is constrained to `queued`, `running`, `retry_wait`, `succeeded`, `partial`, `failed`, or `cancelled`; stage is constrained to `queued`, `searching`, `fetching`, `freezing`, `extracting`, `admitting`, or a terminal status. Add a unique constraint on `(tenant_id, idempotency_key)` and checks for statuses and non-negative counters.

All other records are append-only:

- `AcquisitionJobEvent(job_id, seq, status, stage, message, payload_json, created_at)`, unique `(job_id, seq)`;
- `AcquisitionAttempt(job_id, adapter_key, operation, attempt_no, started_at, finished_at, outcome, error_code, retryable, safe_metadata)`, unique `(job_id, adapter_key, operation, attempt_no)`;
- `SourceReference(job_id, adapter_key, external_record_id, external_version, canonical_url, title, published_at, source_role, metadata_json, created_at)`, unique on the job plus stable provider identity;
- `RetrievalArtifact(source_reference_id, attempt_id, content_sha256, raw_bytes, mime_type, byte_size, final_url, etag, last_modified, provider_request_id, retrieved_at)`, unique `(source_reference_id, attempt_id)` so every actual successful fetch has one artifact while an already-fetched reference avoids a second fetch entirely;
- `RetrievalArtifactDocument(retrieval_artifact_id, document_version_id, relation, publication_key, created_at)`, unique `retrieval_artifact_id`;
- `AutomaticAdmissionDecision(job_id, candidate_id, retrieval_artifact_id, outcome, gate_version, policy_version, gate_results, created_at)`, unique `(job_id, candidate_id, gate_version, policy_version)`;
- `AcquisitionException(job_id, source_reference_id, retrieval_artifact_id, candidate_id, reason_code, detail_json, created_at)`.

Add nullable, unique `automatic_admission_decision_id` to `source_statements` and nullable `automatic_admission_decision_id` to `evidence_links`. Extend the Python `ReviewState` literal with `automatically_admitted`. Register all append-only acquisition tables in `IMMUTABLE_TABLES`.

- [ ] **Step 4: Implement the migration**

Use concrete revision/down-revision values discovered from the integrated baseline. Create all indexes and constraints represented by the models. Extend PostgreSQL immutable triggers using the repository's current migration helper/pattern. The downgrade must remove provenance columns before dropping acquisition tables.

- [ ] **Step 5: Run SQLite and PostgreSQL checks, then commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_schema.py -v`

Run when `TEST_DATABASE_URL` is configured: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_postgres.py -v`

Expected: PASS; PostgreSQL test may skip only when the environment variable is absent.

```bash
git add backend/app/models backend/alembic/versions backend/tests/test_acquisition_schema.py backend/tests/test_acquisition_postgres.py
git commit -m "feat: persist acquisition jobs and artifacts"
```

### Task 3: Implement lease-fenced repository and module request/read interface

**Files:**
- Create: `backend/app/repositories/acquisition.py`
- Create: `backend/app/services/acquisition.py`
- Test: `backend/tests/test_acquisition_repository.py`
- Test: `backend/tests/test_acquisition_service.py`

- [ ] **Step 1: Write concurrency and idempotency tests**

Cover:

```python
principal = AcquisitionPrincipal(tenant_id="team-a", actor="system:test")
first = module.request(request, principal=principal)
second = module.request(request, principal=principal)
assert second.id == first.id

claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
assert claim.lease_token
assert repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30)) is None

with pytest.raises(StaleLeaseError):
    repo.advance(claim.job_id, lease_token="old-token", stage="fetching")
```

Also test event sequence monotonicity, expired-lease reclaim, cancellation, terminal immutability, and `admitted_evidence()` returning only decisions with outcome `admitted`.

- [ ] **Step 2: Run and verify repository import failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_repository.py tests/test_acquisition_service.py -v`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement one transaction per state transition**

Use `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL and deterministic single-worker semantics on SQLite. `claim_next()` creates a random fencing token, increments attempt, and appends an event in the same transaction. Every mutating repository method requires the current token and includes `WHERE lease_token = :token`; zero affected rows raises `StaleLeaseError`.

`AcquisitionModule.request()` requires `principal.tenant_id == request.tenant_id`, checks Case tenant admission, validates the frozen policy version, inserts or returns the idempotent job, and commits no external call. `get()` and `admitted_evidence()` authorize the Case through the same principal before reading.

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_repository.py tests/test_acquisition_service.py -v`

Expected: PASS.

```bash
git add backend/app/repositories/acquisition.py backend/app/services/acquisition.py backend/tests/test_acquisition_repository.py backend/tests/test_acquisition_service.py
git commit -m "feat: add lease-fenced acquisition module"
```

### Task 4: Define adapter behavior and implement Gildata

**Files:**
- Create: `backend/app/acquisition/sources.py`
- Create: `backend/app/datasources/gildata/research_source.py`
- Test: `backend/tests/test_research_source_adapter.py`
- Test: `backend/tests/test_gildata_research_source.py`

- [ ] **Step 1: Write adapter contract tests**

Define test values around:

```python
reference = SourceReferenceValue(
    adapter_key="gildata",
    external_record_id="report:600000:2026-08-01:abc",
    external_version="published:2026-08-01",
    canonical_url="gildata://research-report/report:600000:2026-08-01:abc",
    title="示例公司收入跟踪",
    published_at=datetime(2026, 8, 1, tzinfo=UTC),
    source_role="licensed_provider",
    fetch_locator={"record_id": "report:600000:2026-08-01:abc"},
    metadata={"security_code": "600000", "publisher": "示例机构"},
)
```

Assert frozen values reject missing provider identity, naive publication dates, raw credentials in metadata, disallowed roles, and canonical URLs outside the adapter's declared scheme/hosts.

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_source_adapter.py tests/test_gildata_research_source.py -v`

Expected: FAIL due to missing source adapter modules.

- [ ] **Step 3: Implement the internal source interface**

`SourceAdapter` exposes exactly `descriptor`, `search(query, cutoff)`, `fetch(reference)`, and `close()`. `RetrievedEnvelope` contains bytes, MIME type, final URL, ETag/Last-Modified, provider request id, and safe metadata. It rejects empty bytes and forbidden metadata keys case-insensitively.

- [ ] **Step 4: Implement Gildata mapping**

Reuse `fetch_announcement` and `fetch_research_report`. Build stable external ids from source type, security code, normalized title, publication date, and publisher; never use list index. Missing publication date or body produces a rejected search item recorded by the worker, not a fabricated timestamp. `fetch()` returns the provider text bytes and the same stable identity; it does not call the provider again when search already returned the complete licensed payload. Convert `GildataMCPError` to `SourceUnavailable(retryable=True)` without exposing token-bearing URLs.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_source_adapter.py tests/test_gildata_research_source.py tests/test_gildata_client.py -v`

Expected: PASS.

```bash
git add backend/app/acquisition/sources.py backend/app/datasources/gildata/research_source.py backend/tests/test_research_source_adapter.py backend/tests/test_gildata_research_source.py
git commit -m "feat: add governed Gildata acquisition adapter"
```

### Task 5: Implement official SSE and SZSE announcement adapters

**Files:**
- Create: `backend/app/datasources/exchanges/__init__.py`
- Create: `backend/app/datasources/exchanges/http.py`
- Create: `backend/app/datasources/exchanges/sse.py`
- Create: `backend/app/datasources/exchanges/szse.py`
- Test: `backend/tests/test_exchange_http.py`
- Test: `backend/tests/test_sse_source.py`
- Test: `backend/tests/test_szse_source.py`
- Fixture: `backend/tests/fixtures/acquisition/sse-announcements.json`
- Fixture: `backend/tests/fixtures/acquisition/szse-announcements.json`
- Create: `backend/tests/fixtures/acquisition/README.md`

- [ ] **Step 1: Capture redacted official response fixtures**

Use the official SSE listed-company announcement search and SZSE listed-company disclosure search. Store the smallest response containing two records, including a PDF URL, publication timestamp, security code, title, and provider id. Remove cookies, volatile callback names, headers, and unrelated records. Add a fixture README containing retrieval date, official page URL, request method, and SHA-256 of the unredacted response; do not store credentials.

- [ ] **Step 2: Write transport and mapping tests**

Test exact allowlisted hosts, maximum byte count, redirect rejection outside policy, timeout mapping, 429 `Retry-After`, non-PDF/non-text rejection, and MIME sniff mismatch. Adapter tests must assert stable ids, timezone-aware publication values, bounded pagination, cutoff filtering, and correct download host.

- [ ] **Step 3: Run and verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_exchange_http.py tests/test_sse_source.py tests/test_szse_source.py -v`

Expected: FAIL due to missing exchange modules.

- [ ] **Step 4: Implement bounded HTTP transport**

Use one injected `httpx.Client` with connect/read/write/pool timeouts, no environment proxy in tests, explicit user agent, streaming byte limit, and manual redirect validation. Persist only final URL, MIME type, ETag, Last-Modified, status, and source request id.

- [ ] **Step 5: Implement official adapters**

SSE performs GET requests to `https://query.sse.com.cn/security/stock/queryCompanyBulletin.do`, sends an official-site Referer, and maps `SECURITY_CODE`, `TITLE`, `SSEDATE`, and `URL`; PDF URLs must resolve under `https://www.sse.com.cn/`. SZSE performs JSON POST requests to `https://www.szse.cn/api/disc/announcement/annList`, maps `id`/`annId`, `title`, `publishTime`, `secCode`, and `attachPath`, and resolves PDFs only under `https://disc.static.szse.cn/`. Request parameters come only from `PlannedQuery`; cap each adapter at the policy page/result limit. Any response-schema drift raises `SourceProtocolError` and fails closed—never fall back to generic web scraping.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_exchange_http.py tests/test_sse_source.py tests/test_szse_source.py -v`

Expected: PASS.

```bash
git add backend/app/datasources/exchanges backend/tests/test_exchange_http.py backend/tests/test_sse_source.py backend/tests/test_szse_source.py backend/tests/fixtures/acquisition
git commit -m "feat: acquire official exchange announcements"
```

### Task 6: Freeze artifacts, parse documents, and enforce three-level deduplication

**Files:**
- Create: `backend/app/services/retrieved_documents.py`
- Modify: `backend/app/services/ingest.py`
- Test: `backend/tests/test_retrieved_documents.py`

- [ ] **Step 1: Write artifact and dedup tests**

Cover exact outcomes:

```python
first = freezer.freeze(reference, envelope, request_context)
assert first.relation == "created"
assert first.artifact.content_sha256 == first.document.content_sha256

same_bytes = freezer.freeze(other_reference, envelope, request_context)
assert same_bytes.relation == "content_duplicate"
assert same_bytes.document.id == first.document.id

variant = freezer.freeze(same_publication_reference, changed_envelope, request_context)
assert variant.relation == "variant_conflict"
assert variant.document is None
```

Also assert an artifact survives parser failure, redirects/final URLs remain audited, duplicate provider identity skips a second fetch, same URL with changed bytes creates a superseding DocumentVersion, and contract incompatibility quarantines rather than borrowing the prior contract.

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_retrieved_documents.py -v`

Expected: FAIL with missing service.

- [ ] **Step 3: Expose created/duplicate status from DocumentService**

Add a public `freeze_with_status(...) -> tuple[DocumentVersion, bool]` wrapper around existing `_freeze`; do not change existing `freeze()` callers.

- [ ] **Step 4: Implement the freezer pipeline**

Insert and commit RetrievalArtifact in a short transaction before parsing. Start a new transaction for parsing and document binding, so a parser exception cannot roll back the original bytes. Parse PDFs with the existing parser adapter and UTF-8 text with deterministic line locators. Compute publication key from adapter key, publisher/security code, normalized title, publication date, and source role. Apply source-identity, byte, and publication-key dedup in that order. Record `RetrievalArtifactDocument` for created/content-duplicate/supersedes results; create `AcquisitionException(reason_code="variant_conflict")` without a document binding for ambiguous variants.

When a DocumentVersion is usable, record its SourceContract/ProviderRecord using the integrated source-governance interface and attach it to the Case. Contract mismatch becomes `incompatible_source_contract`; never widen an existing document contract.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_retrieved_documents.py tests/test_event_source_governance.py tests/test_event_research_uploads.py tests/test_document_upload_artifacts.py -v`

Expected: PASS.

```bash
git add backend/app/services/retrieved_documents.py backend/app/services/ingest.py backend/tests/test_retrieved_documents.py
git commit -m "feat: freeze and deduplicate retrieved artifacts"
```

### Task 7: Add structured automatic-admission gates without fake human reviews

**Files:**
- Create: `backend/app/services/automatic_admission.py`
- Modify: `backend/app/services/atomic_claims.py`
- Modify: `backend/app/repositories/research.py`
- Modify: `backend/app/ai/extraction.py`
- Test: `backend/tests/test_automatic_admission.py`

- [ ] **Step 1: Write gate and publication tests**

Use one fully structured numeric claim and one claim for each failure:

```python
decision = gate.evaluate(candidate, context)
assert decision.outcome == "admitted"
assert decision.gate_results.keys() == {"source", "temporal", "locator", "semantic"}
assert decision.source_statement.automatic_admission_decision_id == decision.id
assert decision.evidence_link.review_state == "automatically_admitted"
assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0
```

Failure cases: non-whitelisted source, inactive contract, post-cutoff publication, locator hash mismatch, missing subject, missing period, missing unit for numeric value, metric mismatch, and objective/link-role mismatch. Each must create a quarantined decision plus exception and no SourceStatement/EvidenceLink.

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_automatic_admission.py -v`

Expected: FAIL with missing service.

- [ ] **Step 3: Implement structured gate results**

Define frozen `GateResult(name, passed, reason_code, facts)` and `AdmissionContext`. `evaluate()` checks every gate even after one fails so the audit record is complete. It writes one decision idempotently. Confidence may be recorded in facts but cannot make a failed gate pass.

- [ ] **Step 4: Add automatic publication path**

Add `AtomicClaimService.publish_automatically(candidate_id, decision_id)` that requires an admitted decision for the same candidate and creates or reuses a SourceStatement carrying automatic provenance. It must not call `review()` or create AtomicClaimReview. Add repository support for an EvidenceLink carrying the decision id and `review_state="automatically_admitted"`. Use the request's already-validated `target_link_role`; the gate may never infer or change the role from model prose.

Update extraction documentation from “human review is the sole path” to “formal publication requires either a human review or an immutable automatic-admission decision.” Do not change extraction behavior itself.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_automatic_admission.py tests/test_atomic_claims.py tests/test_ai_engine.py -v`

Expected: PASS.

```bash
git add backend/app/services/automatic_admission.py backend/app/services/atomic_claims.py backend/app/repositories/research.py backend/app/ai/extraction.py backend/tests/test_automatic_admission.py
git commit -m "feat: admit machine evidence through governed gates"
```

### Task 8: Orchestrate the acquisition worker with restart recovery

**Files:**
- Create: `backend/app/services/acquisition_runner.py`
- Create: `backend/app/scripts/run_acquisition_worker.py`
- Test: `backend/tests/test_acquisition_runner.py`
- Test: `backend/tests/test_acquisition_worker_recovery.py`

- [ ] **Step 1: Write a full fake-adapter worker test**

Create a Case/Thesis through existing services, request a job through `AcquisitionModule`, run the worker, and assert stages:

```python
assert [event.stage for event in events] == [
    "queued", "searching", "fetching", "freezing", "extracting", "admitting", "succeeded"
]
assert view.status == "succeeded"
assert view.frozen_count == 1
assert len(module.admitted_evidence(job.id, principal=principal)) == 1
```

- [ ] **Step 2: Write crash/restart fencing tests**

Inject a crash after the first artifact commit. Advance the clock beyond lease expiry, claim with worker B, and finish. Assert one DocumentVersion, one SourceStatement, one EvidenceLink, monotonic events, two attempt records, and stale worker A rejected when it tries to publish. Run the same request again and assert the same job id is returned.

- [ ] **Step 3: Run and verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_runner.py tests/test_acquisition_worker_recovery.py -v`

Expected: FAIL with missing runner.

- [ ] **Step 4: Implement checkpointed orchestration**

`AcquisitionRunner.run_claim(claim)` reads the frozen request and policy, plans queries, searches enabled adapters, persists references, fetches each unbound reference, freezes/parses, extracts candidates through `StatementExtractor`, and applies automatic admission. Commit after every external call and every artifact/document/decision unit. Re-read the lease token before and after external work. A single-reference error appends an attempt and exception; retryable provider failure moves the job to `retry_wait`, while mixed success completes `partial`.

The script supports `--once` and `--loop`, builds enabled adapters explicitly from environment, requires a real LLM in production, and fails closed when a configured provider cannot initialize. It must not import `AutoResearchService`.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_runner.py tests/test_acquisition_worker_recovery.py -v`

Expected: PASS.

```bash
git add backend/app/services/acquisition_runner.py backend/app/scripts/run_acquisition_worker.py backend/tests/test_acquisition_runner.py backend/tests/test_acquisition_worker_recovery.py
git commit -m "feat: run recoverable acquisition jobs"
```

### Task 9: Expose protected standalone commands and transparent reads

**Files:**
- Create: `backend/app/schemas/v1/acquisition.py`
- Create: `backend/app/api/v1/acquisition.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_acquisition_api.py`

- [ ] **Step 1: Write API tests**

Cover:

- `POST /research-cases/{case_id}/theses/{thesis_id}/acquisition-jobs` returns 202 and contains no `actor`/`reviewer` field;
- repeated `Idempotency-Key` returns the same job;
- GET detail/events/evidence are tenant-scoped;
- another tenant receives non-disclosing 404;
- request cannot choose an adapter, URL, host, policy snapshot, or source permission;
- response exposes stage, counters, retry time, exceptions by reason, source title/URL, frozen document id, gate results, and automatic-review label;
- response never includes raw bytes, credentials, full request headers, or exception stack traces.

- [ ] **Step 2: Run and verify 404/import failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_api.py -v`

Expected: FAIL because routes are absent.

- [ ] **Step 3: Implement DTOs and routes**

Construct `AcquisitionRequest` server-side from Case/Thesis/protocol records plus the authenticated tenant. The client supplies only objective and idempotency header in this phase. For `verify_rule`, the route also resolves the selected verification rule and freezes its resulting target role server-side. Actor uses `tenant:` followed by the authenticated tenant ID until the identity phase replaces the current tenant credential with UserPrincipal; client body/header actor values are not accepted.

Add endpoints:

```text
POST /research-cases/{case_id}/theses/{thesis_id}/acquisition-jobs
GET  /acquisition-jobs/{job_id}
GET  /acquisition-jobs/{job_id}/events?after_seq=
GET  /acquisition-jobs/{job_id}/evidence
GET  /acquisition-jobs/{job_id}/exceptions
```

- [ ] **Step 4: Regenerate OpenAPI contract and run tests**

Run from the repository root: `bash scripts/sync-contract.sh --update`.

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_api.py tests/test_event_research_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/v1/acquisition.py backend/app/api/v1/acquisition.py backend/app/api/v1/router.py backend/tests/test_acquisition_api.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: expose acquisition job status"
```

### Task 10: Verify module quality and perform explicit live-source smoke checks

**Files:**
- Create: `backend/app/scripts/smoke_acquisition_sources.py`
- Create: `docs/operations/acquisition-sources.md`
- Test: `backend/tests/test_acquisition_source_smoke_script.py`

- [ ] **Step 1: Add a fail-closed smoke script test**

Assert `--source gildata` exits non-zero without `GILDATA_TOKEN`; `--source sse` and `--source szse` never substitute fixtures; `--dry-run` only validates configuration and does not claim a live success.

- [ ] **Step 2: Implement the smoke command and runbook**

The command accepts one source, one security code/name, date range, and output report path. It executes real adapter search/fetch, prints only counts and safe ids, and writes a JSON report containing timestamp, commit, adapter version, query hash, returned stable ids, MIME types, byte hashes, and errors. It does not insert business rows unless `--persist-case-id` is explicitly provided.

The runbook documents provider credentials, official-source rate limits, source-policy changes, schema-drift response, exception reasons, and safe log fields. Link the verified official announcement pages for SSE and SZSE.

- [ ] **Step 3: Run the complete acquisition slice**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_acquisition_contract.py \
  tests/test_acquisition_policy.py \
  tests/test_acquisition_schema.py \
  tests/test_acquisition_repository.py \
  tests/test_acquisition_service.py \
  tests/test_research_source_adapter.py \
  tests/test_gildata_research_source.py \
  tests/test_exchange_http.py \
  tests/test_sse_source.py \
  tests/test_szse_source.py \
  tests/test_retrieved_documents.py \
  tests/test_automatic_admission.py \
  tests/test_acquisition_runner.py \
  tests/test_acquisition_worker_recovery.py \
  tests/test_acquisition_api.py \
  tests/test_acquisition_source_smoke_script.py -v
```

Expected: PASS.

- [ ] **Step 4: Run adjacent regression suites**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_auto_research_api.py \
  tests/test_atomic_claims.py \
  tests/test_atomic_claims_api.py \
  tests/test_event_source_governance.py \
  tests/test_event_research_uploads.py \
  tests/test_document_upload_artifacts.py \
  tests/test_gildata_client.py -v
```

Expected: PASS and no `AutoResearchService` source diff.

- [ ] **Step 5: Run real official-source smoke checks**

Run SSE and SZSE against one known security code and a two-day window. If `GILDATA_TOKEN` is available, run Gildata too. A provider failure is reported as a failed smoke result; do not replace it with fixtures.

```bash
cd backend
.venv/bin/python -m app.scripts.smoke_acquisition_sources --source sse --security-code 600000 --days 2 --output ../docs/evaluation/reports/acquisition-sse.json
.venv/bin/python -m app.scripts.smoke_acquisition_sources --source szse --security-code 000001 --days 2 --output ../docs/evaluation/reports/acquisition-szse.json
```

- [ ] **Step 6: Confirm phase boundary and commit**

Run: `git diff --name-only "$(git merge-base main HEAD)"..HEAD | rg 'auto_research.py|run_research_worker.py'`

Expected: no output.

```bash
git add backend/app/scripts/smoke_acquisition_sources.py backend/tests/test_acquisition_source_smoke_script.py docs/operations/acquisition-sources.md
git commit -m "test: verify live acquisition source contracts"
```

## Phase handoff

This plan ends when the acquisition module can be requested and observed independently, survives a worker crash, produces automatically admitted evidence with immutable provenance, and passes live SSE/SZSE source smoke checks. It does not alter automatic-research orchestration.

After this phase is merged, write three new plans against the then-current paths and contracts:

1. `automatic-research-acquisition-integration`: submit AcquisitionJobs and resume Research Runs without human claim-review blocking;
2. `keycloak-case-grants-and-runtime-topology`: Keycloak, server actors, CaseGrant, API/worker/scheduler/PostgreSQL Compose, health and metrics;
3. `live-browser-restart-acceptance`: real OIDC browser flow, real adapters, and API/worker restart fault injection with no SQL writes.
