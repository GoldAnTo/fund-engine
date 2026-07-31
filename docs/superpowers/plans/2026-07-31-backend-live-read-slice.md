# Backend Live Read Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prototype's mock-only read path with a versioned `/api/v1` HTTP contract for overview, case dossier, connected graph, document library, and grouped search, while keeping frontend and backend models separated by `HttpResearchAdapter`.

**Architecture:** Keep the existing FastAPI/PostgreSQL modular monolith. Add Pydantic wire DTOs and page-oriented query modules behind `/api/v1`; centralize point-in-time semantics in `HistoricalBasis`; keep the current `/workbench` endpoint temporarily for compatibility. The frontend continues to depend on `ResearchClient`, with a single HTTP adapter translating v1 DTOs into frontend domain objects.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, PostgreSQL/SQLite tests, React 18, TypeScript, Vitest, Playwright.

---

## Scope and follow-on plans

This is delivery 1 of 3 and must remain independently deployable.

Included:

- `/api/v1` router, versioned schemas, error envelope and request IDs.
- Shared `HistoricalBasis` for every read endpoint in this slice.
- Research-case list and dossier queries.
- A genuinely connected case graph with case→thesis and company→stock edges.
- Document list/detail/span reads.
- PostgreSQL/SQLite-compatible grouped search.
- Overview derived honestly from current ledger data.
- Frontend `HttpResearchAdapter` and adapter contract tests.

Not included:

- Proposal/ReviewDecision write workflow, review queue mutations and review assignments.
- Blob upload, parser jobs, retries and locator round-trip.
- Domain events, task/activity operational projections and SSE.
- OpenSearch, Temporal, Debezium, Kafka or microservices.

Those belong in delivery 2, `backend-ingestion-review-jobs`, and delivery 3, `backend-events-history-provenance`. Until delivery 2, the review page remains explicitly unavailable in HTTP mode; it must not silently fall back to mock data.

## File map

```text
backend/app/
  api/v1/
    __init__.py              # v1 package marker
    router.py                # combines v1 routes
    cases.py                 # case list, dossier and graph routes
    documents.py             # document list/detail routes
    overview.py              # overview route
    search.py                # grouped search route
  schemas/v1/
    __init__.py
    common.py                # ErrorEnvelope, CursorPage, HistoricalBasisDTO
    cases.py                 # case/dossier wire DTOs
    graph.py                 # graph wire DTOs
    documents.py             # document wire DTOs
    overview.py              # overview wire DTOs
    search.py                # search wire DTOs
  queries/
    basis.py                 # one cutoff interpretation
    cases.py                 # case list and dossier assembly
    graph.py                 # connected graph assembly
    documents.py             # document list/detail assembly
    overview.py              # current ledger overview
    search.py                # grouped SQL search
  main.py                    # request ID/error handling + v1 router

backend/tests/
  test_api_v1_common.py
  test_case_read_api_v1.py
  test_graph_read_api_v1.py
  test_document_read_api_v1.py
  test_overview_read_api_v1.py
  test_search_read_api_v1.py

frontend/src/data/
  dto/v1.ts                  # HTTP-only types
  httpResearchAdapter.ts     # ResearchClient implementation
  researchClient.ts          # environment-controlled adapter bootstrap

frontend/src/tests/
  HttpResearchAdapter.test.ts
  LiveReadContract.test.ts
```

## Task 1: Establish the versioned HTTP seam

**Files:**

- Create: `backend/app/api/v1/__init__.py`
- Create: `backend/app/api/v1/router.py`
- Create: `backend/app/schemas/v1/__init__.py`
- Create: `backend/app/schemas/v1/common.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_v1_common.py`

- [ ] **Step 1: Write failing tests for version and error envelopes**

```python
from fastapi import APIRouter


def test_v1_health_exposes_schema_version(api_client):
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
    assert response.headers["x-request-id"]


def test_v1_not_found_uses_stable_error_envelope(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["request_id"] == response.headers["x-request-id"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_api_v1_common.py -q
```

Expected: FAIL because `/api/v1/health` and the common error handler do not exist.

- [ ] **Step 3: Define common wire schemas**

```python
# backend/app/schemas/v1/common.py
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoricalBasisDTO(V1Model):
    cutoff: datetime
    is_historical: bool
    ledger_high_watermark: str | None = None
    projection_built_at: datetime | None = None
    projection_schema_version: str | None = None


class CursorPage(V1Model):
    next_cursor: str | None = None
    has_more: bool = False


class ErrorBody(V1Model):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(V1Model):
    error: ErrorBody
```

- [ ] **Step 4: Add the v1 router and request/error middleware**

```python
# backend/app/api/v1/router.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health_v1() -> dict[str, str]:
    return {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
```

Add to `backend/app/main.py` without removing the legacy router:

```python
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router

app.include_router(v1_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError):
    request_id = request.state.request_id
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "not_found",
                "message": str(exc.args[0]),
                "request_id": request_id,
                "details": {},
            }
        },
        headers={"x-request-id": request_id},
    )
```

The dossier route added in Task 3 must raise `KeyError("research case not found")` for the second test. Do not convert every `KeyError` in the process into HTTP 404; Task 3 will replace this temporary handler with the explicit `NotFoundError` below:

```python
class NotFoundError(Exception):
    pass
```

- [ ] **Step 5: Run the focused test**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_api_v1_common.py -q
```

Expected: the health test passes; the dossier test remains failing until Task 3. Mark it `xfail(strict=True, reason="Task 3")` only for the Task 1 commit, then remove the marker in Task 3.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1 backend/app/schemas/v1 backend/app/main.py backend/tests/test_api_v1_common.py
git commit -m "feat: establish versioned backend API seam"
```

## Task 2: Centralize historical basis

**Files:**

- Create: `backend/app/queries/__init__.py`
- Create: `backend/app/queries/basis.py`
- Test: `backend/tests/test_api_v1_common.py`

- [ ] **Step 1: Add failing basis tests**

```python
from datetime import UTC, datetime

from app.queries.basis import HistoricalBasis


def test_basis_normalizes_naive_cutoff_to_utc():
    basis = HistoricalBasis.from_cutoff(datetime(2024, 5, 31, 12, 0))
    assert basis.cutoff == datetime(2024, 5, 31, 12, 0, tzinfo=UTC)
    assert basis.is_historical is True


def test_basis_current_uses_injected_clock():
    now = datetime(2026, 7, 31, 4, 0, tzinfo=UTC)
    basis = HistoricalBasis.from_cutoff(None, now=lambda: now)
    assert basis.cutoff == now
    assert basis.is_historical is False
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_api_v1_common.py -q
```

Expected: FAIL with `ModuleNotFoundError: app.queries.basis`.

- [ ] **Step 3: Implement the value object and DTO conversion**

```python
# backend/app/queries/basis.py
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from app.schemas.v1.common import HistoricalBasisDTO


@dataclass(frozen=True)
class HistoricalBasis:
    cutoff: datetime
    is_historical: bool
    ledger_high_watermark: str | None = None
    projection_built_at: datetime | None = None
    projection_schema_version: str | None = None

    @classmethod
    def from_cutoff(
        cls,
        cutoff: datetime | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> "HistoricalBasis":
        if cutoff is None:
            return cls(cutoff=now(), is_historical=False)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        return cls(cutoff=cutoff, is_historical=True)

    def to_dto(self) -> HistoricalBasisDTO:
        return HistoricalBasisDTO(**self.__dict__)
```

- [ ] **Step 4: Verify pass and run time-travel regressions**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_api_v1_common.py tests/test_time_travel.py tests/test_exposure.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/queries backend/tests/test_api_v1_common.py
git commit -m "feat: centralize historical read basis"
```

## Task 3: Add case list and dossier APIs

**Files:**

- Create: `backend/app/schemas/v1/cases.py`
- Create: `backend/app/queries/cases.py`
- Create: `backend/app/api/v1/cases.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/repositories/research.py`
- Test: `backend/tests/test_case_read_api_v1.py`
- Modify test: `backend/tests/test_api_v1_common.py`

- [ ] **Step 1: Write failing endpoint tests**

```python
from datetime import UTC, datetime


def test_case_list_returns_navigation_rows(api_client, workbench_case):
    response = api_client.get("/api/v1/research-cases")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["items"][0]["id"] == str(workbench_case.case.id)
    assert payload["items"][0]["title"] == workbench_case.case.title


def test_dossier_selects_requested_thesis_and_respects_cutoff(
    api_client, workbench_case
):
    cutoff = datetime(2026, 4, 30, tzinfo=UTC).isoformat()
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier",
        params={"thesis_id": str(workbench_case.thesis.id), "cutoff": cutoff},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_thesis_id"] == str(workbench_case.thesis.id)
    assert payload["basis"]["cutoff"] == cutoff
    assert payload["assessment"]["provisional"] is True
    assert payload["evidence"]["supports"][0]["verbatim_text"]
    assert "confidence" not in payload["assessment"]
    assert "ready_for_review" not in payload


def test_missing_case_returns_v1_error(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_case_read_api_v1.py -q
```

Expected: FAIL because v1 case routes do not exist.

- [ ] **Step 3: Add repository reads without changing write semantics**

Add to `ResearchRepository`:

```python
def cases_page(self, *, limit: int, after_created_at=None, after_id=None):
    query = select(ResearchCase).order_by(
        ResearchCase.created_at.desc(), ResearchCase.id.desc()
    )
    if after_created_at is not None and after_id is not None:
        query = query.where(
            tuple_(ResearchCase.created_at, ResearchCase.id)
            < tuple_(after_created_at, after_id)
        )
    return list(self._session.scalars(query.limit(limit + 1)))


def thesis_by_id_for_case(self, case_id: uuid.UUID, thesis_id: uuid.UUID):
    return self._session.scalar(
        select(Thesis)
        .where(Thesis.id == thesis_id)
        .where(Thesis.research_case_id == case_id)
    )
```

Import `tuple_` from SQLAlchemy. Cursor encoding belongs in the query module, not the repository.

- [ ] **Step 4: Define explicit DTOs**

`backend/app/schemas/v1/cases.py` must define these Pydantic models with `extra="forbid"` inherited from `V1Model`:

```python
from typing import Any, Literal

from pydantic import Field

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class CaseSummaryDTO(V1Model):
    id: str
    title: str
    topic: str
    created_by: str
    created_at: str
    updated_at: str


class ThesisSummaryDTO(V1Model):
    id: str
    statement: str
    created_by: str
    created_at: str


class EvidenceRecordDTO(V1Model):
    link_id: str
    statement_id: str
    statement_text: str
    statement_kind: str
    span_id: str | None
    verbatim_text: str | None
    locator: dict[str, Any] | None
    role: Literal["supports", "contradicts", "contextualizes"]
    reason: str
    scope: dict[str, Any]
    observed_period: str | None
    available_at: str
    review_state: str


class AssessmentDTO(V1Model):
    id: str
    thesis_id: str
    conclusion: Literal["supported", "contradicted", "insufficient_evidence"]
    rationale: str
    gaps: list[str]
    provisional: bool
    review: dict[str, Any] | None


class CausalStepDTO(V1Model):
    id: str
    sequence: int
    description: str


class CaseListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    items: list[CaseSummaryDTO]
    page: CursorPage


class DossierResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    case: CaseSummaryDTO
    theses: list[ThesisSummaryDTO]
    focus_thesis_id: str
    assessment: AssessmentDTO | None
    causal_chain: list[CausalStepDTO]
    evidence: dict[str, list[EvidenceRecordDTO]]
    competitive_explanations: list[str]
    gaps: list[str]
```

- [ ] **Step 5: Implement the dossier query module**

Create `CaseReadQueries` with `list_cases(*, cursor: str | None, limit: int) -> CaseListResponse` and `dossier(*, case_id: UUID, thesis_id: UUID | None, basis: HistoricalBasis) -> DossierResponse` as its only public methods. Keep cursor encode/decode and ORM-to-DTO conversion as private functions in the same file.

The implementation must:

1. Resolve the requested thesis inside the requested case; if absent, use `latest_thesis_for_case`.
2. Use `visible_links(thesis.id, cutoff=basis.cutoff)`.
3. Partition evidence by role without dropping `contextualizes`.
4. Resolve SourceStatement and SourceSpan for each link.
5. Use `latest_assessment_for_thesis(thesis.id, cutoff=basis.cutoff)` and keep review separate.
6. Derive `gaps` only from the frozen assessment; return `competitive_explanations=[]` until a formal ledger object exists.
7. Encode cursors as URL-safe base64 JSON containing `created_at` and `id`; reject malformed cursors with `ValidationError`.

Do not invent author, reliability, confidence, status label or prose not present in the ledger.

- [ ] **Step 6: Add routes and explicit domain errors**

```python
# backend/app/api/v1/cases.py
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.cases import CaseReadQueries
from app.schemas.v1.cases import CaseListResponse, DossierResponse

router = APIRouter(prefix="/research-cases", tags=["research-cases-v1"])


@router.get("", response_model=CaseListResponse)
def list_cases(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return CaseReadQueries(db).list_cases(cursor=cursor, limit=limit)


@router.get("/{case_id}/dossier", response_model=DossierResponse)
def dossier(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    return CaseReadQueries(db).dossier(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
```

Replace the temporary `KeyError` handler from Task 1 with a dedicated `NotFoundError` handler. `CaseReadQueries.dossier` raises `NotFoundError("research case not found")` or `NotFoundError("thesis not found in research case")`.

- [ ] **Step 7: Run tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_api_v1_common.py tests/test_case_read_api_v1.py tests/test_workbench_api.py tests/test_time_travel.py -q
```

Expected: PASS; remove the Task 1 `xfail` marker.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1 backend/app/schemas/v1/cases.py backend/app/queries/cases.py backend/app/repositories/research.py backend/app/main.py backend/tests/test_api_v1_common.py backend/tests/test_case_read_api_v1.py
git commit -m "feat: add case dossier read contract"
```

## Task 4: Build a connected graph query

**Files:**

- Create: `backend/app/schemas/v1/graph.py`
- Create: `backend/app/queries/graph.py`
- Modify: `backend/app/api/v1/cases.py`
- Modify: `backend/app/repositories/instruments.py`
- Test: `backend/tests/test_graph_read_api_v1.py`

- [ ] **Step 1: Write failing graph tests**

```python
def edge_pairs(payload):
    return {
        (edge["semantic_kind"], edge["source"], edge["target"])
        for edge in payload["edges"]
    }


def test_graph_is_connected_from_evidence_to_fund(api_client, workbench_case):
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/graph",
        params={"thesis_id": str(workbench_case.thesis.id)},
    )
    assert response.status_code == 200
    payload = response.json()
    pairs = edge_pairs(payload)
    assert (
        "contains_thesis",
        str(workbench_case.case.id),
        str(workbench_case.thesis.id),
    ) in pairs
    assert any(edge["semantic_kind"] == "company_stock" for edge in payload["edges"])
    assert any(edge["semantic_kind"] == "holding" for edge in payload["edges"])
    assert payload["paths"]


def test_graph_excludes_future_disclosure(api_client, workbench_case):
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/graph",
        params={"cutoff": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 200
    assert not any(
        edge["semantic_kind"] == "holding" for edge in response.json()["edges"]
    )
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_graph_read_api_v1.py -q
```

Expected: FAIL because the route and connecting edges do not exist.

- [ ] **Step 3: Define graph DTOs**

```python
from typing import Any, Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class GraphNodeDTO(V1Model):
    id: str
    kind: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeDTO(V1Model):
    id: str
    semantic_kind: str
    source: str
    target: str
    review_state: str | None = None
    available_at: str | None = None
    valid_interval: dict[str, str | None] | None = None
    source_refs: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPathDTO(V1Model):
    node_ids: list[str]
    edge_ids: list[str]
    label: str


class GraphResponse(V1Model):
    schema_version: Literal["graph/v1"] = "graph/v1"
    basis: HistoricalBasisDTO
    nodes: list[GraphNodeDTO]
    edges: list[GraphEdgeDTO]
    paths: list[GraphPathDTO]
    page: CursorPage
```

- [ ] **Step 4: Implement `RelationshipGraphQueries.load`**

Create `RelationshipGraphQueries` with one public method: `load(*, case_id: UUID, thesis_id: UUID | None, basis: HistoricalBasis, focus: str | None, depth: int, limit: int) -> GraphResponse`. Its constructor accepts one SQLAlchemy `Session` and constructs `ResearchRepository` and `InstrumentRepository` internally.

Required construction order:

1. Add ResearchCase and selected Thesis nodes plus deterministic `contains_thesis:{case}:{thesis}` edge.
2. Add reviewed and machine-generated visible EvidenceLinks with explicit `review_state`; do not present machine links as reviewed.
3. Add causal step/edge nodes.
4. Filter ThemeRole by `applicable_from/to` against `basis.cutoff.date()`.
5. Add Company and Stock nodes plus deterministic `company_stock:{company}:{stock}` edges.
6. Include HoldingDisclosure only when `published_at <= basis.cutoff`; add Fund nodes and holding edges.
7. Include ValuationSnapshot only when `as_of_date <= basis.cutoff.date()`.
8. Generate `paths` by traversing the assembled adjacency map; never fabricate a path across missing edges.
9. Return at most `limit` nodes and all edges whose endpoints are present. If truncated, set `page.has_more=True`; cursor expansion is deferred until a real large graph fixture exists.

- [ ] **Step 5: Add the route**

```python
@router.get("/{case_id}/graph", response_model=GraphResponse)
def graph(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    focus: str | None = None,
    depth: int = Query(default=4, ge=1, le=8),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return RelationshipGraphQueries(db).load(
        case_id=case_id,
        thesis_id=thesis_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        focus=focus,
        depth=depth,
        limit=limit,
    )
```

- [ ] **Step 6: Verify graph and legacy regressions**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_graph_read_api_v1.py tests/test_workbench_api.py tests/test_projection.py -q
```

Expected: PASS. The legacy `/workbench` contract remains unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/v1/graph.py backend/app/queries/graph.py backend/app/api/v1/cases.py backend/app/repositories/instruments.py backend/tests/test_graph_read_api_v1.py
git commit -m "feat: expose connected evidence graph"
```

## Task 5: Add document library reads

**Files:**

- Create: `backend/app/schemas/v1/documents.py`
- Create: `backend/app/queries/documents.py`
- Create: `backend/app/api/v1/documents.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/repositories/documents.py`
- Test: `backend/tests/test_document_read_api_v1.py`

- [ ] **Step 1: Write failing document tests**

```python
def test_documents_list_frozen_versions(api_client, document, span):
    response = api_client.get("/api/v1/documents")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == str(document.id)
    assert item["content_sha256"] == document.content_sha256
    assert item["span_count"] >= 1
    assert item["parse_state"] == "parsed"


def test_document_detail_returns_spans_and_citations(api_client, document, span):
    response = api_client.get(f"/api/v1/documents/{document.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["id"] == str(document.id)
    assert payload["spans"][0]["verbatim_text"] == span.verbatim_text
    assert payload["spans"][0]["locator"] == span.locator


def test_documents_cutoff_excludes_future_available_version(api_client, document):
    response = api_client.get(
        "/api/v1/documents", params={"cutoff": "2000-01-01T00:00:00Z"}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_document_read_api_v1.py -q
```

Expected: FAIL because `/api/v1/documents` does not exist.

- [ ] **Step 3: Add repository queries**

```python
def visible_versions(self, *, cutoff: datetime, limit: int):
    return list(
        self._session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.available_at <= cutoff)
            .order_by(DocumentVersion.available_at.desc(), DocumentVersion.id.desc())
            .limit(limit + 1)
        )
    )


def spans_for_version(self, version_id: uuid.UUID):
    return list(
        self._session.scalars(
            select(SourceSpan)
            .where(SourceSpan.document_version_id == version_id)
            .order_by(SourceSpan.id)
        )
    )
```

- [ ] **Step 4: Define honest DTOs for the current schema**

```python
class DocumentSummaryDTO(V1Model):
    id: str
    content_sha256: str
    source_url: str
    published_at: str | None
    available_at: str
    acquired_at: str
    parser_version: str
    supersedes_id: str | None
    span_count: int
    statement_count: int
    parse_state: Literal["parsed", "unparsed"]


class SourceSpanDTO(V1Model):
    id: str
    document_version_id: str
    locator: dict
    verbatim_text: str
    citations: list[dict]


class DocumentListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    items: list[DocumentSummaryDTO]
    page: CursorPage


class DocumentDetailResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    document: DocumentSummaryDTO
    spans: list[SourceSpanDTO]
```

Do not fabricate title, publisher, document type, MIME type, blob URL or parse failure stage; delivery 2 adds those fields with a migration.

- [ ] **Step 5: Implement query and routes**

```python
router = APIRouter(prefix="/documents", tags=["documents-v1"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    q: str | None = None,
    cutoff: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return DocumentReadQueries(db).list_documents(
        query=q,
        basis=HistoricalBasis.from_cutoff(cutoff),
        limit=limit,
    )


@router.get("/{version_id}", response_model=DocumentDetailResponse)
def document_detail(version_id: uuid.UUID, db: Session = Depends(get_db)):
    return DocumentReadQueries(db).detail(version_id=version_id)
```

`DocumentReadQueries.detail` must count citations by joining SourceSpan → SourceStatement → EvidenceLink and must raise `NotFoundError` for a missing version.

- [ ] **Step 6: Verify tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_document_read_api_v1.py tests/test_documents.py tests/test_evidence_links.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/v1/documents.py backend/app/queries/documents.py backend/app/api/v1/documents.py backend/app/api/v1/router.py backend/app/repositories/documents.py backend/tests/test_document_read_api_v1.py
git commit -m "feat: expose frozen document library reads"
```

## Task 6: Add grouped ledger search

**Files:**

- Create: `backend/app/schemas/v1/search.py`
- Create: `backend/app/queries/search.py`
- Create: `backend/app/api/v1/search.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_search_read_api_v1.py`

- [ ] **Step 1: Write failing search tests**

```python
def test_search_groups_case_thesis_and_statement(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "GPU"})
    assert response.status_code == 200
    payload = response.json()
    assert {group["object_type"] for group in payload["groups"]} >= {
        "thesis",
        "evidence",
    }
    for group in payload["groups"]:
        for hit in group["hits"]:
            assert hit["deep_link"].startswith("/")


def test_search_cutoff_excludes_future_evidence(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": "2000-01-01T00:00:00Z"},
    )
    assert response.status_code == 200
    evidence = next(
        (g for g in response.json()["groups"] if g["object_type"] == "evidence"),
        None,
    )
    assert evidence is None or evidence["hits"] == []
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_search_read_api_v1.py -q
```

Expected: FAIL because the search route does not exist.

- [ ] **Step 3: Define the search interface and DTO**

```python
class SearchHitDTO(V1Model):
    object_type: Literal["case", "thesis", "evidence", "company", "stock", "fund"]
    object_id: str
    title: str
    snippet: str
    case_id: str | None
    review_state: str | None
    available_at: str | None
    deep_link: str


class SearchGroupDTO(V1Model):
    object_type: str
    hits: list[SearchHitDTO]


class SearchResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    groups: list[SearchGroupDTO]
    page: CursorPage
```

`LedgerSearchQueries.search(q, types, basis, limit)` is the module interface. Implement case-insensitive SQL matching with `func.lower(column).contains(q.lower())` for SQLite/PostgreSQL portability in this delivery. Evidence search must join EvidenceLink and filter `available_at <= basis.cutoff`; a SourceStatement existing in the ledger without a visible link is not a normal-user evidence search hit.

- [ ] **Step 4: Add route validation**

```python
@router.get("", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2, max_length=200),
    types: str | None = None,
    cutoff: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    requested = set(types.split(",")) if types else None
    return LedgerSearchQueries(db).search(
        q=q,
        types=requested,
        basis=HistoricalBasis.from_cutoff(cutoff),
        limit=limit,
    )
```

Reject unknown types with `422 validation_failed`; do not silently ignore them.

- [ ] **Step 5: Verify tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_search_read_api_v1.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/v1/search.py backend/app/queries/search.py backend/app/api/v1/search.py backend/app/api/v1/router.py backend/tests/test_search_read_api_v1.py
git commit -m "feat: add grouped ledger search"
```

## Task 7: Add an honest overview query

**Files:**

- Create: `backend/app/schemas/v1/overview.py`
- Create: `backend/app/queries/overview.py`
- Create: `backend/app/api/v1/overview.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_overview_read_api_v1.py`

- [ ] **Step 1: Write failing overview tests**

```python
def test_overview_uses_ledger_counts_and_visible_assessment(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["case"]["id"] == str(workbench_case.case.id)
    assert payload["assessment"]["provisional"] is True
    assert payload["totals"]["evidence_total"] >= 1
    assert payload["totals"]["pending_review"] >= 1
    assert payload["task_queue"] == []
    assert payload["activity"] == []


def test_overview_does_not_invent_reliability_or_maturity(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    text = response.text
    assert "reliable_pct" not in text
    assert "maturity" not in text
    assert "ready_for_review" not in text
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_overview_read_api_v1.py -q
```

Expected: FAIL because the overview route does not exist.

- [ ] **Step 3: Define an honest v1 overview DTO**

```python
class OverviewTotalsDTO(V1Model):
    evidence_total: int
    pending_review: int
    major_gaps: int


class KeyChangeDTO(V1Model):
    id: str
    tag: Literal["新增", "更新", "风险", "缺口"]
    text: str
    occurred_at: str
    source_label: str


class OverviewResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    case: CaseSummaryDTO
    thesis: dict | None
    assessment: AssessmentDTO | None
    key_changes: list[KeyChangeDTO]
    framework: list[dict]
    totals: OverviewTotalsDTO
    task_queue: list[dict]
    evidence_changes: list[dict]
    activity: list[dict]
```

`key_changes` comes from the newest visible EvidenceLinks and their statements. `framework` comes from causal steps. `pending_review` counts visible machine-generated EvidenceLinks plus unreviewed assessments. `task_queue`, `evidence_changes` and `activity` return empty arrays until delivery 3 creates their projections. This is explicit partial capability, not mock prose.

- [ ] **Step 4: Implement query and route**

```python
@router.get("", response_model=OverviewResponse)
def overview(
    case_id: uuid.UUID,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    return OverviewQueries(db).load(
        case_id=case_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
```

`OverviewQueries.load` must reuse `CaseReadQueries.dossier` for the selected case rather than reimplement assessment/evidence visibility.

- [ ] **Step 5: Verify tests**

Run:

```bash
cd backend
./.venv/bin/pytest tests/test_overview_read_api_v1.py tests/test_case_read_api_v1.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/v1/overview.py backend/app/queries/overview.py backend/app/api/v1/overview.py backend/app/api/v1/router.py backend/tests/test_overview_read_api_v1.py
git commit -m "feat: add ledger-backed research overview"
```

## Task 8: Implement the frontend HTTP adapter

**Files:**

- Create: `frontend/src/data/dto/v1.ts`
- Create: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/domain/types.ts`
- Modify: `frontend/src/components/WorkspaceOverview.tsx`
- Modify: `frontend/src/pages/DocumentLibraryPage.tsx`
- Test: `frontend/src/tests/HttpResearchAdapter.test.ts`

- [ ] **Step 1: Write failing adapter tests with mocked fetch**

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";

afterEach(() => vi.unstubAllGlobals());

describe("HttpResearchAdapter", () => {
  it("maps a dossier DTO without leaking wire-only basis fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            schema_version: "v1",
            basis: { cutoff: "2024-05-31T00:00:00Z", is_historical: true },
            case: {
              id: "c1",
              title: "AI 算力链",
              topic: "ai_compute",
              created_by: "u1",
              created_at: "2024-01-01T00:00:00Z",
              updated_at: "2024-01-01T00:00:00Z",
            },
            theses: [],
            focus_thesis_id: "t1",
            assessment: null,
            causal_chain: [],
            evidence: { supports: [], contradicts: [], contextualizes: [] },
            competitive_explanations: [],
            gaps: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
      )
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const dossier = await adapter.getCaseDossier("c1", { cutoff: "2024-05-31T00:00:00Z" });
    expect(dossier.case.id).toBe("c1");
    expect(dossier.focus_thesis_id).toBe("t1");
    expect((dossier as unknown as Record<string, unknown>).basis).toBeUndefined();
  });

  it("maps the stable error envelope to PageStateError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "permission_denied",
              message: "forbidden",
              request_id: "r1",
              details: {},
            },
          }),
          { status: 403, headers: { "content-type": "application/json" } }
        )
      )
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await expect(adapter.getCaseSummaries()).rejects.toMatchObject({
      kind: "permission_denied",
    });
  });
});
```

- [ ] **Step 2: Verify failure**

Run:

```bash
cd frontend
npm test -- --run src/tests/HttpResearchAdapter.test.ts
```

Expected: FAIL because `HttpResearchAdapter` does not exist.

- [ ] **Step 3: Define wire-only DTOs**

In `frontend/src/data/dto/v1.ts`, mirror the JSON wire fields from Tasks 1–7. Prefix exported names with `V1`, for example:

```typescript
export interface V1HistoricalBasis {
  cutoff: string;
  is_historical: boolean;
  ledger_high_watermark?: string | null;
  projection_built_at?: string | null;
  projection_schema_version?: string | null;
}

export interface V1ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
    details: Record<string, unknown>;
  };
}

export interface V1CaseSummary {
  id: string;
  title: string;
  topic: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}
```

Define `V1DossierResponse`, `V1GraphResponse`, `V1DocumentListResponse`, `V1DocumentDetailResponse`, `V1OverviewResponse` and `V1SearchResponse` with the exact backend field names. Do not import these DTOs into page/components.

- [ ] **Step 4: Implement one HTTP seam**

```typescript
export class HttpResearchAdapter implements ResearchClient {
  constructor(private readonly options: { baseUrl: string }) {}

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.options.baseUrl}${path}`, {
        headers: { Accept: "application/json" },
      });
    } catch {
      throw new PageStateError("backend_unavailable");
    }
    if (!response.ok) {
      const payload = (await response.json()) as V1ErrorEnvelope;
      if (payload.error.code === "permission_denied") {
        throw new PageStateError("permission_denied", payload.error.message);
      }
      throw new PageStateError("backend_unavailable", payload.error.message);
    }
    return (await response.json()) as T;
  }

  async getCaseSummaries(): Promise<ResearchCaseSummary[]> {
    const dto = await this.get<V1CaseListResponse>("/research-cases");
    return dto.items.map(mapCaseSummary);
  }

  async getCaseDossier(caseId: string, query: DossierQuery = {}): Promise<ResearchCaseDossier> {
    const params = new URLSearchParams();
    if (query.thesisId) params.set("thesis_id", query.thesisId);
    if (query.cutoff) params.set("cutoff", query.cutoff);
    const suffix = params.size ? `?${params.toString()}` : "";
    const dto = await this.get<V1DossierResponse>(
      `/research-cases/${encodeURIComponent(caseId)}/dossier${suffix}`
    );
    return mapDossier(dto);
  }
}
```

Implement every read method used by the five live pages: `getOverview`, `getCaseSummaries`, `getCaseDossier`, `getRelationshipGraph`, `getDocuments`, `getDocumentDetail`, and `search`. `getReviewQueue` and `submitReviewDecision` must throw `PageStateError("backend_unavailable", "review API is not available in live-read delivery")`; never delegate those methods to `MockResearchAdapter`.

Mapping rules:

- Backend `created_by` maps to frontend `author`.
- Missing prototype-only presentation fields use neutral values in the mapper, not invented financial claims: empty bullets/tabs/logs and `has_markdown=false`.
- Historical `basis` controls the page banner through a mapper-provided `HistoricalSnapshotContext`; it is not copied into unrelated domain objects.
- Backend graph `semantic_kind` maps to the existing frontend `EdgeKind`; add `contains_thesis` and `company_stock` to the frontend union before mapping.
- Change `WorkspaceOverview.totals.reliable_pct` to `number | null`; map it to `null` and render `—` with the label “尚无人工质量口径”.
- Change `SourceDocumentView.title`, `publisher`, and `document_type` to nullable fields. Map unavailable backend metadata to `null`; the library renders “元数据待补” instead of inventing a publisher or document class.
- Map `parse_state="parsed"` to `parse_quality="ok"` and `parse_state="unparsed"` to `parse_quality="partial"`; do not claim parser success when no spans exist.

- [ ] **Step 5: Select adapter by explicit environment configuration**

Modify `researchClient.ts`:

```typescript
function defaultClient(): ResearchClient {
  const baseUrl = import.meta.env.VITE_RESEARCH_API_URL;
  return baseUrl
    ? new HttpResearchAdapter({ baseUrl: baseUrl.replace(/\/$/, "") })
    : new MockResearchAdapter();
}

let _client: ResearchClient = defaultClient();

export function resetResearchClient(): void {
  _client = defaultClient();
}
```

Mock mode remains available for isolated UI state tests. A deployed build that sets `VITE_RESEARCH_API_URL` must never fall back to mock on HTTP failure.

- [ ] **Step 6: Verify adapter tests and typecheck**

Run:

```bash
cd frontend
npm test -- --run src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts
npm run build
```

Expected: PASS and successful TypeScript/Vite build.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/data/dto/v1.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/researchClient.ts frontend/src/domain/types.ts frontend/src/components/WorkspaceOverview.tsx frontend/src/pages/DocumentLibraryPage.tsx frontend/src/tests/HttpResearchAdapter.test.ts
git commit -m "feat: connect frontend through HTTP research adapter"
```

## Task 9: Prove the live read slice end to end

**Files:**

- Create: `frontend/src/tests/LiveReadContract.test.ts`
- Modify: `frontend/playwright.config.ts`
- Modify: `frontend/e2e/workbench.spec.ts`
- Modify: `docs/design/backend-design.md`

- [ ] **Step 1: Add a cross-adapter semantic contract test**

```typescript
import { describe, expect, it } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";

describe.runIf(process.env.RESEARCH_API_TEST_URL)("live read contract", () => {
  const client = new HttpResearchAdapter({
    baseUrl: process.env.RESEARCH_API_TEST_URL!,
  });

  it("can traverse case -> dossier -> graph -> source document", async () => {
    const cases = await client.getCaseSummaries();
    expect(cases.length).toBeGreaterThan(0);
    const dossier = await client.getCaseDossier(cases[0].id);
    const graph = await client.getRelationshipGraph(cases[0].id);
    const documents = await client.getDocuments();
    expect(dossier.focus_thesis_id).toBeTruthy();
    expect(graph.nodes.length).toBeGreaterThan(0);
    expect(documents.length).toBeGreaterThan(0);
    const detail = await client.getDocumentDetail(documents[0].id);
    expect(detail.spans.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Add a live Playwright project**

Keep existing mock screenshot/state projects. Add one project named `live-read` that sets:

```typescript
use: {
  baseURL: "http://127.0.0.1:5173",
},
metadata: {
  researchApiUrl: "http://127.0.0.1:8000/api/v1",
}
```

Start the backend with seeded SQLite and the frontend with `VITE_RESEARCH_API_URL=http://127.0.0.1:8000/api/v1`. The live test must visit overview, dossier, relationship graph and documents; it must assert that no visible text contains the mock-only urban-NOA fixture.

- [ ] **Step 3: Run backend full verification**

Run:

```bash
cd backend
./.venv/bin/pytest -q
```

Expected: all non-PG/non-Neo4j tests PASS; only explicitly marked integration tests may skip.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd frontend
npm test -- --run
npm run build
```

Expected: all tests PASS and build succeeds. Fix the pre-existing `WorkspaceOverview` undefined `framework` failure before claiming this delivery complete; do not weaken its assertions.

- [ ] **Step 5: Run the live contract and E2E**

Terminal 1:

```bash
cd backend
DATABASE_URL=sqlite:///./evidence_gate.db ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd frontend
VITE_RESEARCH_API_URL=http://127.0.0.1:8000/api/v1 npm run dev -- --host 127.0.0.1
```

Terminal 3:

```bash
cd frontend
RESEARCH_API_TEST_URL=http://127.0.0.1:8000/api/v1 npm test -- --run src/tests/LiveReadContract.test.ts
npx playwright test --project=live-read
```

Expected: PASS. Capture the exact test totals in the commit message or handoff; do not reuse old counts.

- [ ] **Step 6: Update the backend design status accurately**

In `docs/design/backend-design.md`, change only the implemented capabilities from “缺失/拟新增” to “已实现”. Preserve the remaining Proposal, jobs, events, permissions and provenance gaps. Add the exact commands and results from Steps 3–5.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/tests/LiveReadContract.test.ts frontend/playwright.config.ts frontend/e2e/workbench.spec.ts docs/design/backend-design.md
git commit -m "test: verify live backend read slice"
```

## Final release gate

The delivery is complete only when all statements are true:

- [ ] A build with `VITE_RESEARCH_API_URL` performs no read through `MockResearchAdapter`.
- [ ] Overview, case list/dossier, graph, documents and search all use `/api/v1` DTOs.
- [ ] Review methods fail visibly in live mode instead of returning mock proposals.
- [ ] Every historical endpoint accepts and returns one consistent cutoff basis.
- [ ] The graph contains real case→thesis and company→stock edges and excludes future holdings.
- [ ] No endpoint invents confidence, reliability, maturity, ready-for-review, recommendation or real-time-holding claims.
- [ ] Legacy `/api/research-cases/{id}/workbench` tests still pass.
- [ ] Backend focused/full tests, frontend unit tests, build and live E2E all pass.

After this gate, write and approve `2026-07-31-backend-ingestion-review-jobs.md`. Do not extend this plan opportunistically into Proposal migrations or workflow infrastructure.
