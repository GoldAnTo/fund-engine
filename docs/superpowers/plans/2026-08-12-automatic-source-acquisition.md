# Automatic Source Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an evidence objective into audited provider retrieval, frozen `DocumentVersion` records, source contracts, and reviewable atomic-claim candidates before evidence proposal begins.

**Architecture:** Add a deep `EvidenceReplenishmentService` seam. Provider adapters only return normalized retrieval documents; the service owns cutoff enforcement, deduplication, source governance, Case attachment, provider audit records, and run events. Begin with Gildata company announcements and licensed research reports, while keeping the interface provider-neutral.

**Tech Stack:** Python 3.12, SQLAlchemy 2, existing Gildata MCP client/adapters, immutable document ledger, pytest.

---

## File map

- Create `backend/app/services/research_source.py`: provider-neutral request/result contracts.
- Create `backend/app/datasources/gildata/research_source.py`: Gildata adapter.
- Create `backend/app/services/evidence_replenishment.py`: intake and governance orchestration.
- Modify `backend/app/services/ingest.py`: expose a public freeze-with-dedup-result method.
- Modify `backend/app/services/auto_research.py`: replenish before processing in-scope documents.
- Modify `backend/app/scripts/run_research_worker.py`: construct enabled source adapters for worker execution.
- Test with fake adapters; retain existing `SourceContract`, `ProviderRecord`, and `ResearchRunEvent` tables, so no migration is needed.

### Task 1: Define provider-neutral retrieval contracts

**Files:**
- Create: `backend/app/services/research_source.py`
- Create: `backend/tests/test_research_source_contract.py`

- [ ] **Step 1: Write contract tests**

```python
from datetime import UTC, datetime

import pytest

from app.services.evidence_objective import EvidenceObjective
from app.services.research_source import RetrievalDocument, RetrievalRequest


def test_retrieval_request_is_frozen_and_cutoff_aware():
    request = RetrievalRequest(
        case_id="case-1",
        thesis_id="thesis-1",
        thesis_statement="利润能否兑现",
        objective=EvidenceObjective.CONTRADICT,
        query="利润 未达预期",
        cutoff=datetime(2026, 8, 12, tzinfo=UTC),
        allowed_source_types=frozenset({"licensed_provider"}),
    )
    assert request.objective is EvidenceObjective.CONTRADICT
    with pytest.raises(AttributeError):
        request.query = "changed"


def test_retrieval_document_requires_provider_identity_and_bytes():
    with pytest.raises(ValueError, match="provider_record_id"):
        RetrievalDocument(
            provider_name="gildata",
            provider_record_id="",
            source_type="licensed_provider",
            source_url="gildata://research_report/empty",
            title="报告",
            raw=b"body",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
            retrieval_reference=None,
            contract_metadata={},
        )
```

- [ ] **Step 2: Run and verify module failure**

Run: `cd backend && uv run pytest tests/test_research_source_contract.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the contracts**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.services.evidence_objective import EvidenceObjective


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    case_id: str
    thesis_id: str
    thesis_statement: str
    objective: EvidenceObjective
    query: str
    cutoff: datetime
    allowed_source_types: frozenset[str]


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    provider_name: str
    provider_record_id: str
    source_type: str
    source_url: str
    title: str
    raw: bytes
    published_at: datetime
    retrieval_reference: str | None
    contract_metadata: dict[str, object]

    def __post_init__(self) -> None:
        for name in ("provider_name", "provider_record_id", "source_type", "source_url", "title"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.raw:
            raise ValueError("raw is required")
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must include a timezone")


class ResearchSourceAdapter(Protocol):
    source_types: frozenset[str]

    def search(self, request: RetrievalRequest) -> list[RetrievalDocument]: ...
```

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && uv run pytest tests/test_research_source_contract.py -v`

Expected: PASS.

```bash
git add backend/app/services/research_source.py backend/tests/test_research_source_contract.py
git commit -m "feat: define research source adapter contract"
```

### Task 2: Implement the Gildata research-source adapter

**Files:**
- Create: `backend/app/datasources/gildata/research_source.py`
- Create: `backend/tests/test_gildata_research_source.py`

- [ ] **Step 1: Write fake-client adapter tests**

Assert that `company_disclosure` calls `fetch_announcement`, `licensed_provider` calls `fetch_research_report`, documents after the request cutoff are dropped, and missing publication dates are dropped rather than assigned retrieval time.

```python
assert [item.provider_record_id for item in documents] == ["announcement:600000:20260801:半年报"]
assert documents[0].source_type == "company_disclosure"
assert documents[0].contract_metadata["permissions"] == {
    "ai_processing": True,
    "display": True,
    "export": False,
    "api": True,
}
```

- [ ] **Step 2: Run and verify module failure**

Run: `cd backend && uv run pytest tests/test_gildata_research_source.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement normalized mapping**

Create `GildataResearchSource` with `source_types = frozenset({"company_disclosure", "licensed_provider"})`. Its `search` method must:

```python
def search(self, request: RetrievalRequest) -> list[RetrievalDocument]:
    output: list[RetrievalDocument] = []
    if "company_disclosure" in request.allowed_source_types:
        output.extend(self._announcements(request))
    if "licensed_provider" in request.allowed_source_types:
        output.extend(self._reports(request))
    return [item for item in output if item.published_at <= request.cutoff]
```

Use `fetch_announcement(self._client, request.query)` and `fetch_research_report(self._client, request.query)`. Parse provider publication timestamps with one helper that returns `None` on absence/invalid input. Build stable provider ids from provider fields, not list positions. Encode text as UTF-8 bytes and never copy provider retrieval time into `published_at`.

- [ ] **Step 4: Run Gildata suites and commit**

Run: `cd backend && uv run pytest tests/test_gildata_research_source.py tests/test_gildata_client.py -v`

Expected: PASS.

```bash
git add backend/app/datasources/gildata/research_source.py backend/tests/test_gildata_research_source.py
git commit -m "feat: adapt Gildata sources for evidence research"
```

### Task 3: Build the governed replenishment service

**Files:**
- Create: `backend/app/services/evidence_replenishment.py`
- Modify: `backend/app/services/ingest.py`
- Create: `backend/tests/test_evidence_replenishment.py`

- [ ] **Step 1: Write failing service tests**

With a fake adapter returning one before-cutoff, one after-cutoff, one disallowed, and one duplicate document, assert:

```python
assert result.retrieved_count == 4
assert result.frozen_count == 1
assert result.duplicate_count == 1
assert result.rejected_count == 2
assert len(result.document_version_ids) == 1
assert session.scalar(select(func.count()).select_from(SourceContract)) == 1
assert session.scalar(select(func.count()).select_from(ProviderRecord)) == 1
assert session.scalar(select(func.count()).select_from(CaseDocumentVersion)) == 1
```

Also assert a contract with `allow_ai_processing=False` is persisted for audit but its document id is absent from `processable_document_version_ids`.

- [ ] **Step 2: Run and verify module failure**

Run: `cd backend && uv run pytest tests/test_evidence_replenishment.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement one deep service interface**

Define:

```python
@dataclass(frozen=True, slots=True)
class ReplenishmentResult:
    retrieved_count: int
    frozen_count: int
    duplicate_count: int
    rejected_count: int
    document_version_ids: tuple[uuid.UUID, ...]
    processable_document_version_ids: tuple[uuid.UUID, ...]


class EvidenceReplenishmentService:
    def __init__(self, session: Session, adapters: Sequence[ResearchSourceAdapter]):
        self._session = session
        self._adapters = tuple(adapters)

    def replenish(self, request: RetrievalRequest) -> ReplenishmentResult:
        ...
```

Inside `replenish`, for each adapter whose `source_types` intersects the frozen allowed types:

1. call `adapter.search(request)`;
2. reject documents after cutoff or outside allowed source types;
3. freeze bytes through a public `DocumentService.freeze_with_status(...)` method to retain the `created` flag;
4. call `SourceGovernanceService.record_event_intake(...)` with the provider's contract declaration;
5. attach the document to the Case;
6. insert `ProviderRecord` only when one does not already exist for the document;
7. place the document in `processable_document_version_ids` only when `apply_source_contract(...).can_accept` is true.

Add the public wrapper without changing existing callers:

```python
def freeze_with_status(
    self,
    raw: bytes,
    source_url: str,
    **metadata,
) -> tuple[DocumentVersion, bool]:
    return self._freeze(raw=raw, source_url=source_url, **metadata)
```

Use a savepoint around each returned document so one malformed provider item does not roll back earlier valid items. The final result must be deterministic and deduplicated by document id.

- [ ] **Step 4: Run source-governance and replenishment tests**

Run: `cd backend && uv run pytest tests/test_evidence_replenishment.py tests/test_event_source_governance.py tests/test_ingest_command_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/evidence_replenishment.py backend/app/services/ingest.py backend/tests/test_evidence_replenishment.py
git commit -m "feat: freeze governed evidence retrieval results"
```

### Task 4: Insert retrieval into the automatic research cycle

**Files:**
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/repositories/auto_research.py`
- Test: `backend/tests/test_auto_research_api.py`

- [ ] **Step 1: Write an end-to-end worker test with a fake source adapter**

Start a run whose Case has no relevant statements. Execute the worker with a fake adapter and assert the stage order:

```python
assert [event.stage for event in events] == [
    "scope",
    "retrieval",
    "extract",
    "claim_review",
]
assert events[1].payload_json["objective"] == "contradict"
assert events[1].payload_json["frozen_count"] == 1
assert run.status == "waiting_for_review"
assert run.stop_reason == "pending_atomic_claim_review"
```

- [ ] **Step 2: Run and verify the current no-source result**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py -k retrieves_before_extracting -v`

Expected: FAIL because the worker only processes already attached documents.

- [ ] **Step 3: Add retrieval before `_pending_versions_in_run_scope`**

Inject `source_adapters` into `AutoResearchService`. For each evidence task, build `RetrievalRequest` from the immutable scope event, task query, task objective, and run cutoff. Call the replenishment service once per `(thesis_id, objective, round)` and append:

```python
ResearchRunEventRepository(self.session).append(
    run.id,
    stage="retrieval",
    status="completed",
    message=f"已检索 {result.retrieved_count} 条，冻结 {result.frozen_count} 份材料",
    payload_json={
        "task_id": str(task.id),
        "thesis_id": str(task.thesis_id),
        "objective": objective.value,
        "retrieved_count": result.retrieved_count,
        "frozen_count": result.frozen_count,
        "duplicate_count": result.duplicate_count,
        "rejected_count": result.rejected_count,
        "document_version_ids": [str(item) for item in result.document_version_ids],
    },
)
```

Do not propose evidence directly from the retrieved payload. Let the existing document parsing, atomic-claim review, recall, and proposal gates continue.

- [ ] **Step 4: Run orchestration tests and commit**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py tests/test_atomic_claims.py tests/test_atomic_claims_api.py -v`

Expected: PASS.

```bash
git add backend/app/services/auto_research.py backend/app/repositories/auto_research.py backend/tests/test_auto_research_api.py
git commit -m "feat: retrieve sources during automatic research"
```

### Task 5: Wire configuration and fail closed

**Files:**
- Modify: `backend/app/scripts/run_research_worker.py`
- Test: `backend/tests/test_auto_research_api.py`

- [ ] **Step 1: Test disabled and unavailable provider behavior**

Assert an unconfigured provider produces a failed retrieval event with `provider_unavailable`, consumes no evidence budget, and does not silently fall back to unrestricted web content. Assert a run with no enabled adapter still proceeds against already admitted Case materials.

- [ ] **Step 2: Implement explicit adapter construction**

Add a worker factory:

```python
def _research_source_adapters() -> tuple[ResearchSourceAdapter, ...]:
    if not os.getenv("GILDATA_TOKEN", "").strip():
        return ()
    return (GildataResearchSource(GildataMCPClient.from_env()),)
```

Construct `AutoResearchService(session, source_adapters=_research_source_adapters())` only in the execution worker; HTTP routes that merely create runs remain provider-free. Convert `GildataMCPError` into the existing `UpstreamUnavailableError` at the adapter boundary and record the provider name without tokens or request credentials.

- [ ] **Step 3: Run failure-path tests**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py tests/test_gildata_research_source.py -k "unavailable or disabled or admitted" -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/scripts/run_research_worker.py backend/tests/test_auto_research_api.py
git commit -m "feat: configure governed research source adapters"
```

### Task 6: Phase verification

**Files:**
- No production file changes.

- [ ] **Step 1: Run the acquisition slice**

Run: `cd backend && uv run pytest tests/test_research_source_contract.py tests/test_gildata_research_source.py tests/test_evidence_replenishment.py tests/test_gildata_client.py tests/test_event_source_governance.py tests/test_auto_research_api.py tests/test_atomic_claims.py tests/test_atomic_claims_api.py -v`

Expected: PASS.

- [ ] **Step 2: Verify idempotency**

Run the same fake-provider worker scenario twice and assert the second run adds no new `DocumentVersion`, `SourceContract`, or `ProviderRecord`, while its retrieval event reports the duplicate count.
