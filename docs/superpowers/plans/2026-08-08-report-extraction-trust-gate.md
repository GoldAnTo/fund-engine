# Report Extraction Trust Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current direct `LLM/table -> SourceStatement` write path with an auditable `AtomicClaimCandidate -> validation -> human review -> SourceStatement` gate for frozen research documents.

**Architecture:** Keep `DocumentVersion` and `SourceSpan` as immutable source records. Parsing adapters create locatable spans; table rules and LLM extraction create immutable candidate claims plus per-run observations, never formal statements. A review command is the sole new production path that publishes a validated candidate as a `SourceStatement`; existing downstream recall, evidence-link, conclusion and projection readers continue to consume only formal statements.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, SQLite/PostgreSQL, Pydantic v2, Docling, optional PaddleOCR/PP-Structure, pytest.

---

## Scope, sequencing, and non-goals

This is **P0-A: the extraction trust gate**. It intentionally does not implement the full source-contract/blob system, the all-Case workbench, fund-data enrichment, or the final graphical review UI. Those need separate plans because they have independent database and permission boundaries.

The currently dirty `backend/app/services/event_research_scope.py` is outside scope. Start implementation only in an isolated worktree after that change is either committed by its owner or excluded from the worktree. Do not change its imports, lifecycle, or tests.

The P0-A result is usable through command APIs and a read-model queue. It makes the later UI implementation safe: no AI candidate is eligible for evidence linking until a reviewer has accepted it. `Docling` remains the preferred primary parser; PP-Structure is an optional secondary parser and quality comparison, not a second writer of duplicate spans.

### File map

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0020_atomic_claim_candidates.py` | Append-only candidate, extraction-observation and candidate-review tables; non-breaking statement provenance columns and Postgres immutability triggers. |
| `backend/app/models/ledger.py` | ORM records, allowed claim/authority/review literals, immutable-table list. |
| `backend/app/services/ingest.py`, `backend/app/repositories/documents.py` | Carries an explicit document authority stamp at freeze time; defaults legacy/unspecified material to `unknown`. |
| `backend/app/domain/atomic_claims.py` | Typed candidate draft, canonical-key computation, quote hash, deterministic quote/period/value/unit/authority validators. |
| `backend/app/repositories/atomic_claims.py` | Append-only persistence and queue/read queries; no update/delete methods. |
| `backend/app/services/atomic_claims.py` | Candidate admission, idempotency, candidate review and the sole publish-to-`SourceStatement` service. |
| `backend/app/ai/prompts.py` | `extract-v2` contract requiring exact quote and structured fields. |
| `backend/app/ai/extraction.py` | Candidate-producing table/LLM extraction with one auditable run reference; never calls `ResearchService.add_statement`. |
| `backend/app/services/table_extraction.py` | Preserves exact row quote and offsets with every deterministic table fact. |
| `backend/app/datasources/paddle_structure.py` | Optional PP-Structure adapter that produces comparison-only parsed spans/quality report. |
| `backend/app/datasources/docling.py` | Parser-result quality contract and router integration, without weakening locator-v1 round trips. |
| `backend/app/services/auto_research.py` | Stops before propose/assess when an input document has pending claim review; reports the review gate explicitly. |
| `backend/app/api/v1/commands/atomic_claims.py` | Candidate extraction/review commands. |
| `backend/app/api/v1/atomic_claims.py` | Read-only candidate review queue/details API. |
| `backend/app/api/v1/router.py`, `backend/app/schemas/v1/atomic_claims.py` | Router registration and strict request/response schemas. |
| `backend/app/services/extraction_evaluation.py` | Deterministic Gold v1 scoring and release-gate report. |
| `backend/tests/test_atomic_claims.py` | Model/service/repository invariants and review publication tests. |
| `backend/tests/test_atomic_claims_api.py` | API boundary, permission-state and idempotency tests. |
| `backend/tests/test_table_extraction.py`, `backend/tests/test_ai_engine.py` | Replace direct-statement expectations with candidate and validation assertions. |
| `backend/tests/test_pdf_parser_adapters.py` | Primary/secondary parser comparison and fail-closed parser tests. |
| `backend/tests/fixtures/extraction_gold_v1.json` | License-safe, redacted/synthetic executable gold fixture. |
| `docs/evaluation/extraction-gold-v1.md` | Private licensed-gold manifest, labeling rubric and acceptance thresholds. |
| `backend/pyproject.toml` | Optional `pp-structure` dependency group only; it must not become a mandatory API-server import. |

## Shared data contract

Use these names consistently in all tasks.

```python
ClaimType = Literal[
    "disclosed_fact", "reported_claim", "management_attribution",
    "forecast", "research_opinion",
]
AuthorityLevel = Literal[
    "primary_disclosure", "licensed_research", "secondary_source",
    "user_supplied", "unknown",
]
ValidationState = Literal["passed", "rejected", "needs_review"]
ReviewOutcome = Literal["confirmed", "modified", "rejected"]

@dataclass(frozen=True, slots=True)
class AtomicClaimDraft:
    source_span_id: UUID
    quote: str
    quote_start: int
    quote_end: int
    normalized_text: str
    claim_type: ClaimType
    assertion_actor: str | None
    subject: str | None
    predicate: str | None
    object_text: str | None
    numeric_value: str | None
    unit: str | None
    observed_period: date | None
    scope: dict[str, str]
```

The canonical candidate key is SHA-256 of: `document_version_id`, `source_span_id`, exact `quote` hash, `claim_type`, normalized typed fields, and the canonicalization version. It deliberately excludes model/prompt version. Thus identical re-runs return the original candidate; a new run is recorded as a new observation rather than creating a duplicate candidate.

An `AtomicClaimCandidate` stores the admitted draft and validation result. `AtomicClaimExtractionObservation` stores every model/rule run's raw output and shared `run_ref`. `AtomicClaimReview` is append-only and, for a confirmed/modified review, points to the newly published `SourceStatement`. No candidate row has a mutable “approved” flag.

### Task 1: Lock the migration and immutable data model

**Files:**
- Create: `backend/alembic/versions/0020_atomic_claim_candidates.py`
- Modify: `backend/app/models/ledger.py`
- Modify: `backend/app/services/ingest.py`
- Modify: `backend/app/repositories/documents.py`
- Test: `backend/tests/test_atomic_claims.py`

- [ ] **Step 1: Write migration/model failure tests**

```python
def test_candidate_and_review_rows_are_append_only(session):
    candidate = atomic_claims.admit(draft, run_ref=uuid.uuid4())
    review = atomic_claims.review(candidate.id, outcome="rejected", reviewer="alice", reason="not atomic")
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(AtomicClaimCandidate).where(AtomicClaimCandidate.id == candidate.id).values(quote="changed"))
    with pytest.raises(ImmutableLedgerError):
        session.execute(delete(AtomicClaimReview).where(AtomicClaimReview.id == review.id))

def test_legacy_source_statement_fields_remain_nullable(session):
    statement = SourceStatement(source_span_id=span.id, kind="research_opinion", normalized_text="legacy", created_at=now)
    session.add(statement); session.flush()
    assert statement.atomic_claim_candidate_id is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd backend && pytest tests/test_atomic_claims.py -q`

Expected: collection failure because the candidate ORM records and migration fields do not exist.

- [ ] **Step 3: Add the non-breaking migration and ORM records**

Create append-only `atomic_claim_candidates`, `atomic_claim_extraction_observations`, and `atomic_claim_reviews` tables. Add nullable `atomic_claim_candidate_id`, `quote_sha256`, `canonical_key`, `assertion_actor`, `authority_level`, and `review_state` columns to `source_statements`; never backfill old statements as reviewed, and expand the accepted statement kinds with `reported_claim`. Add nullable `source_authority` to `document_versions` with server default `unknown`, so historical documents cannot masquerade as primary disclosures. Add `idempotency_key` to `atomic_claim_reviews` with a unique `(atomic_claim_candidate_id, idempotency_key)` constraint. Add all three new tables to `IMMUTABLE_TABLES` and create the same PostgreSQL update/delete triggers used by prior ledger migrations.

```python
class AtomicClaimCandidate(Base):
    __tablename__ = "atomic_claim_candidates"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_span_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("source_spans.id"), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_start: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_end: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    assertion_actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority_level: Mapped[str] = mapped_column(String(32), nullable=False)
    structured_fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Run targeted model/migration tests**

Run: `cd backend && pytest tests/test_atomic_claims.py tests/test_documents.py -q`

Expected: PASS; immutability tests reject both ordinary updates and deletes.

- [ ] **Step 5: Commit the schema slice**

```bash
git add backend/alembic/versions/0020_atomic_claim_candidates.py backend/app/models/ledger.py backend/app/services/ingest.py backend/app/repositories/documents.py backend/tests/test_atomic_claims.py
git commit -m "feat: add immutable atomic claim ledger"
```

### Task 2: Build pure quote, structure, and authority validators

**Files:**
- Create: `backend/app/domain/atomic_claims.py`
- Test: `backend/tests/test_atomic_claims.py`

- [ ] **Step 1: Add failing validator cases**

```python
def test_validate_quote_requires_exact_contiguous_source_substring():
    result = validate_draft(span, draft(quote="订单增长", quote_start=5, quote_end=9))
    assert result.state == "passed"
    assert validate_draft(span, draft(quote="订单  增长", quote_start=5, quote_end=10)).state == "rejected"
    assert validate_draft(span, draft(quote="订单增长", quote_start=0, quote_end=4)).state == "rejected"

def test_secondary_report_cannot_admit_disclosed_fact():
    result = validate_draft(secondary_span, draft(claim_type="disclosed_fact"))
    assert result.state == "rejected"
    assert result.codes == ["authority.disclosed_fact_requires_primary"]

def test_forecast_is_not_reclassified_as_disclosed_fact_when_actor_is_management():
    result = validate_draft(primary_span, draft(claim_type="forecast", assertion_actor="management"))
    assert result.state == "passed"
```

- [ ] **Step 2: Run the validator tests and verify failure**

Run: `cd backend && pytest tests/test_atomic_claims.py -q`

Expected: FAIL because `validate_draft` and authority policy do not exist.

- [ ] **Step 3: Implement deterministic validation**

Implement `sha256_text`, `canonical_candidate_key`, `validate_draft`, and `validate_numeric_fields` in `app/domain/atomic_claims.py`. Quote validation must evaluate `span.verbatim_text[quote_start:quote_end] == quote` before any normalization and must calculate the hash from that exact Unicode string. Validate `quote_start >= 0`, `quote_end > quote_start`, non-empty normalized text, enum membership, unit/value pairing, ISO period, and structured-field types. Use `DocumentVersion.source_authority` (new nullable/non-breaking column with `unknown` default) to reject `disclosed_fact` unless authority is `primary_disclosure`; use `reported_claim` for licensed research or other secondary restatements.

Extend `DocumentService.freeze()` and `DocumentRepository.add_version()` with a keyword-only `source_authority: AuthorityLevel = "unknown"`; adapt each existing ingestion caller explicitly or accept the safe default. This is an interim explicit stamp only. The later `SourceContract` plan replaces it with contract-derived policy, but no missing contract may be interpreted as primary authority.

- [ ] **Step 4: Run pure validator tests**

Run: `cd backend && pytest tests/test_atomic_claims.py -q`

Expected: PASS; quote mutation, offset drift, bad unit/period pair and secondary-as-primary are rejected without an LLM call.

- [ ] **Step 5: Commit the validator slice**

```bash
git add backend/app/domain/atomic_claims.py backend/tests/test_atomic_claims.py
git commit -m "feat: validate atomic claim provenance"
```

### Task 3: Add candidate persistence, idempotency, and human publication

**Files:**
- Create: `backend/app/repositories/atomic_claims.py`
- Create: `backend/app/services/atomic_claims.py`
- Modify: `backend/app/services/research.py`
- Test: `backend/tests/test_atomic_claims.py`

- [ ] **Step 1: Write failing end-to-end service tests**

```python
def test_same_canonical_claim_is_not_duplicated_but_each_run_is_observed(session):
    first = service.admit(draft, run_ref=uuid.uuid4(), producer="llm", raw_payload={})
    second = service.admit(draft, run_ref=uuid.uuid4(), producer="llm", raw_payload={})
    assert first.id == second.id
    assert session.scalars(select(AtomicClaimCandidate)).all() == [first]
    assert len(session.scalars(select(AtomicClaimExtractionObservation)).all()) == 2

def test_only_confirmed_review_publishes_formal_statement(session):
    candidate = service.admit(valid_draft, run_ref=uuid.uuid4(), producer="llm", raw_payload={})
    rejected = service.review(candidate.id, outcome="rejected", reviewer="alice", reason="wrong period")
    assert rejected.published_statement_id is None
    confirmed = service.review(candidate.id, outcome="confirmed", reviewer="bob", reason="quote and period checked")
    assert confirmed.published_statement_id is not None
    assert session.get(SourceStatement, confirmed.published_statement_id).review_state == "reviewed"
```

- [ ] **Step 2: Run the service tests and verify failure**

Run: `cd backend && pytest tests/test_atomic_claims.py -q`

Expected: FAIL because repository/service methods do not exist.

- [ ] **Step 3: Implement append-only repository and service**

`AtomicClaimService.admit()` validates before persistence, inserts only a validated candidate, and always inserts one observation for the caller's `run_ref`; it returns the pre-existing candidate on canonical-key collision. `review()` inserts a new review; `confirmed` publishes a `SourceStatement` through a new, internal `ResearchService.publish_reviewed_statement()` method. `modified` first validates a reviewer-supplied replacement draft, records the original review with `successor_candidate_id`, and publishes only the valid replacement. `rejected` never publishes. No code path mutates candidate, review, or statement rows.

```python
def publish_reviewed_statement(self, candidate: AtomicClaimCandidate, *, reviewer: str) -> SourceStatement:
    return self._repo.add_statement(
        source_span_id=candidate.source_span_id,
        kind=candidate.claim_type,
        normalized_text=candidate.normalized_text,
        observed_period=candidate.structured_fields.get("observed_period"),
        atomic_claim_candidate_id=candidate.id,
        quote_sha256=candidate.quote_sha256,
        canonical_key=candidate.canonical_key,
        assertion_actor=candidate.assertion_actor,
        authority_level=candidate.authority_level,
        review_state="reviewed",
    )
```

- [ ] **Step 4: Run service and downstream compatibility tests**

Run: `cd backend && pytest tests/test_atomic_claims.py tests/test_recall.py tests/test_documents.py -q`

Expected: PASS; downstream recall still sees only published statements and no duplicate candidate is created by a rerun.

- [ ] **Step 5: Commit the service slice**

```bash
git add backend/app/repositories/atomic_claims.py backend/app/services/atomic_claims.py backend/app/services/research.py backend/tests/test_atomic_claims.py
git commit -m "feat: gate source statements behind claim review"
```

### Task 4: Convert table and LLM extraction into candidate producers

**Files:**
- Modify: `backend/app/services/table_extraction.py`
- Modify: `backend/app/ai/prompts.py`
- Modify: `backend/app/ai/client.py`
- Modify: `backend/app/ai/extraction.py`
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/tests/test_table_extraction.py`
- Modify: `backend/tests/test_ai_engine.py`

- [ ] **Step 1: Replace direct-statement tests with candidate-gate failures**

```python
def test_table_extraction_produces_validated_candidates_not_statements(session, document_service, document):
    span = document_service.add_span(document.id, {"page": 1}, TABLE_SNIPPET)
    result = StatementExtractor(client).extract(document.id, session)
    assert result.candidates
    assert session.scalars(select(SourceStatement)).all() == []
    assert result.candidates[0].quote in span.verbatim_text

def test_llm_output_without_exact_quote_or_offset_is_rejected(session, narrative_span):
    client.chat_json = Mock(return_value={"statements": [{"span_id": str(narrative_span.id), "quote": "改写", "quote_start": 0, "quote_end": 2, "claim_type": "research_opinion"}]})
    result = StatementExtractor(client).extract(narrative_span.document_version_id, session)
    assert result.rejected_count == 1
    assert session.scalars(select(SourceStatement)).all() == []
```

- [ ] **Step 2: Run extractor tests and verify failure**

Run: `cd backend && pytest tests/test_table_extraction.py tests/test_ai_engine.py -q`

Expected: FAIL because extraction still writes `SourceStatement` directly and prompt v1 does not request quote/offsets.

- [ ] **Step 3: Implement `extract-v2` and rule/LLM candidate conversion**

Change `EXTRACT_PROMPT_VERSION` to `extract-v2`. Require `quote`, `quote_start`, `quote_end`, `claim_type`, `assertion_actor`, `subject`, `predicate`, `object_text`, `numeric_value`, `unit`, `observed_period` and `scope` in each response. Update `_mock_extract` to emit a real substring and exact offsets. Extend `TableFact` with `quote`, `quote_start`, `quote_end`, `numeric_value`, `unit`, `metric_name`; derive the row quote directly from the unchanged table `SourceSpan`. `StatementExtractor.extract()` returns an `ExtractionResult(candidates, rejected_count, run_ref)` and writes only candidates/observations plus its single `AIRun`. It must record rejected candidate diagnostics in `AIRun.output_summary`, not silently drop them.

`AutoResearchService` must not invoke `EvidenceProposer` for a document that produced pending candidates; its run result must say `blocked_by_claim_review` and list the candidate IDs/count. It may proceed only after formal statements exist through reviews.

- [ ] **Step 4: Run focused extractor and auto-research tests**

Run: `cd backend && pytest tests/test_table_extraction.py tests/test_ai_engine.py tests/test_auto_research_api.py -q`

Expected: PASS; table and LLM paths produce candidates, invalid quote data is rejected, and auto-research cannot link unreviewed output.

- [ ] **Step 5: Commit the extraction slice**

```bash
git add backend/app/services/table_extraction.py backend/app/ai/prompts.py backend/app/ai/client.py backend/app/ai/extraction.py backend/app/services/auto_research.py backend/tests/test_table_extraction.py backend/tests/test_ai_engine.py
git commit -m "feat: extract reviewed atomic claim candidates"
```

### Task 5: Add primary/secondary parser quality routing without dual writes

**Files:**
- Create: `backend/app/datasources/paddle_structure.py`
- Modify: `backend/app/datasources/docling.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/tests/test_pdf_parser_adapters.py`

- [ ] **Step 1: Write adapter-contract tests using fake converter outputs**

```python
def test_secondary_parser_is_comparison_only_and_never_creates_duplicate_spans():
    primary = [parsed_span("营业收入 50亿元", page=1)]
    secondary = [parsed_span("营业收入50亿元", page=1)]
    result = compare_parser_results(primary, secondary)
    assert result.primary_spans == primary
    assert result.secondary_only_count == 1
    assert result.accepted_spans == primary

def test_scanned_document_requires_secondary_parser_or_fails_closed():
    with pytest.raises(PdfParseError, match="no verified parser output"):
        parse_with_quality_router(raw_scanned_pdf, primary=empty_primary, secondary=None)
```

- [ ] **Step 2: Run parser-contract tests and verify failure**

Run: `cd backend && pytest tests/test_pdf_parser_adapters.py -q`

Expected: FAIL because PP-Structure adapter and quality router do not exist.

- [ ] **Step 3: Implement the optional secondary parser**

Add `pp-structure = ["paddleocr>=3", "paddlepaddle>=3"]` as an optional dependency group. `PpStructureAdapter` must lazy-import Paddle packages, return `ParsedSpan` with page/bbox/text and parser version, and raise a specific `PpStructureNotInstalled` otherwise. Add `ParserQualityReport` containing primary/secondary counts, exact-text agreement, table agreement and warnings. The router writes only the selected primary parsed spans; secondary output is retained in the parse report/diagnostics for review, never independently fed to extraction. If neither parser produces a locatable span, set parse failure/partial state and create no candidates.

- [ ] **Step 4: Run parser and locator regression tests**

Run: `cd backend && pytest tests/test_pdf_parser_adapters.py tests/test_migrate_locators_v1.py tests/test_s5_v1_read_path.py -q`

Expected: PASS; existing Docling/Pypdf locator behavior remains compatible and scanned pages fail closed when OCR is unavailable.

- [ ] **Step 5: Commit the parser slice**

```bash
git add backend/app/datasources/paddle_structure.py backend/app/datasources/docling.py backend/pyproject.toml backend/tests/test_pdf_parser_adapters.py
git commit -m "feat: compare document parser quality"
```

### Task 6: Expose a strict candidate review API and protect downstream engine commands

**Files:**
- Create: `backend/app/schemas/v1/atomic_claims.py`
- Create: `backend/app/api/v1/commands/atomic_claims.py`
- Create: `backend/app/api/v1/atomic_claims.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/api/v1/commands/engine.py`
- Test: `backend/tests/test_atomic_claims_api.py`

- [ ] **Step 1: Write API contract tests**

```python
def test_queue_hides_raw_unvalidated_llm_payload(api_client, pending_candidate):
    response = api_client.get("/api/v1/atomic-claims?document_version_id=" + str(pending_candidate.document_version_id))
    assert response.status_code == 200
    assert response.json()["items"][0]["review_state"] == "pending"
    assert "raw_payload" not in response.json()["items"][0]

def test_confirm_review_returns_published_statement_and_is_idempotent(api_client, pending_candidate):
    body = {"outcome": "confirmed", "reviewer": "alice", "reason": "quote, period and authority verified", "idempotency_key": "confirm-candidate-001"}
    first = api_client.post(f"/api/v1/atomic-claims/{pending_candidate.id}/reviews", json=body)
    duplicate = api_client.post(f"/api/v1/atomic-claims/{pending_candidate.id}/reviews", json=body)
    assert first.status_code == 201
    assert duplicate.status_code == 409
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `cd backend && pytest tests/test_atomic_claims_api.py -q`

Expected: FAIL with 404 because routes/schemas do not exist.

- [ ] **Step 3: Implement endpoints and status semantics**

Implement:

```text
POST /api/v1/documents/{document_version_id}/atomic-claims/extract
GET  /api/v1/atomic-claims?document_version_id=&review_state=pending
GET  /api/v1/atomic-claims/{candidate_id}
POST /api/v1/atomic-claims/{candidate_id}/reviews
```

The extract command returns `202` with `run_ref`, candidate/rejected counts and `next_action="review_claims"`. Candidate detail returns quote, locator, typed fields, authority, validations and prior reviews. It never returns raw provider prompt/response. A review requires a non-empty reason, explicit reviewer and caller-provided `idempotency_key`; `modified` requires a complete replacement `AtomicClaimDraft`. Map invalid UUIDs to the repository’s established error envelope and duplicate review submissions with the same candidate/key to `409`.

- [ ] **Step 4: Run API and engine regression tests**

Run: `cd backend && pytest tests/test_atomic_claims_api.py tests/test_auto_research_api.py tests/test_research_ops_api.py -q`

Expected: PASS; pending candidates are visible for review but cannot reach propose/assess.

- [ ] **Step 5: Commit the API slice**

```bash
git add backend/app/schemas/v1/atomic_claims.py backend/app/api/v1/atomic_claims.py backend/app/api/v1/commands/atomic_claims.py backend/app/api/v1/router.py backend/app/api/v1/commands/engine.py backend/tests/test_atomic_claims_api.py
git commit -m "feat: add atomic claim review commands"
```

### Task 7: Make Gold v1 executable and enforce regression gates

**Files:**
- Create: `backend/app/services/extraction_evaluation.py`
- Create: `backend/tests/fixtures/extraction_gold_v1.json`
- Create: `backend/tests/test_extraction_evaluation.py`
- Create: `docs/evaluation/extraction-gold-v1.md`

- [ ] **Step 1: Write a failing gold-score test**

```python
def test_gold_v1_fails_on_quote_drift_and_fact_forecast_confusion():
    report = score_gold_items(GOLD_ITEMS, [wrong_quote_candidate, forecast_as_fact_candidate])
    assert report.quote_exact_rate == 0.0
    assert report.claim_type_confusion["forecast->disclosed_fact"] == 1
    assert report.passes is False

def test_gold_v1_accepts_exact_candidate_set():
    report = score_gold_items(GOLD_ITEMS, GOLD_CANDIDATES)
    assert report.quote_exact_rate == 1.0
    assert report.duplicate_count == 0
    assert report.passes is True
```

- [ ] **Step 2: Run evaluation tests and verify failure**

Run: `cd backend && pytest tests/test_extraction_evaluation.py -q`

Expected: collection failure because the scorer and Gold fixture do not exist.

- [ ] **Step 3: Implement scoring and licensed-data boundary**

Implement `score_gold_items()` with exact quote/offset/hash rate, atomic precision/recall/F1, duplicate count, claim-type confusion matrix, numeric/unit/period/entity field accuracy and review-approval rate. Set fixed release thresholds: quote/offset/hash rate `1.00`, duplicates `0`, disclosed-fact precision `>= 0.98`, overall atomic precision `>= 0.95`, high-impact recall `>= 0.90`, and no primary-authority policy violation. The public repository fixture must be synthetic or license-safe and executable; `docs/evaluation/extraction-gold-v1.md` defines the private 30–50 item, two-reviewer, adjudicated licensed corpus location and says it is never committed/exported without source permission.

- [ ] **Step 4: Run evaluation and full focused quality suite**

Run: `cd backend && pytest tests/test_extraction_evaluation.py tests/test_atomic_claims.py tests/test_table_extraction.py tests/test_ai_engine.py -q`

Expected: PASS; an exact approved set passes and every prescribed quality violation fails.

- [ ] **Step 5: Commit the quality gate**

```bash
git add backend/app/services/extraction_evaluation.py backend/tests/fixtures/extraction_gold_v1.json backend/tests/test_extraction_evaluation.py docs/evaluation/extraction-gold-v1.md
git commit -m "test: add report extraction gold gate"
```

### Task 8: Verify migration, immutable writes, and the narrow end-to-end path

**Files:**
- Modify: `backend/tests/test_research_flow_e2e.py`
- Modify: `docs/design/2026-08-08-research-operating-system-baseline.md` only if implementation reveals a necessary approved design change

- [ ] **Step 1: Write the full flow test before final integration**

```python
def test_frozen_document_to_reviewed_statement_to_evidence_flow(cmd_client, seeded_case):
    extracted = cmd_client.post(f"/api/v1/documents/{document_id}/atomic-claims/extract")
    assert extracted.status_code == 202
    candidate = extracted.json()["candidates"][0]
    assert cmd_client.post(f"/api/v1/theses/{thesis_id}/propose").status_code == 409
    reviewed = cmd_client.post(f"/api/v1/atomic-claims/{candidate['id']}/reviews", json=CONFIRM_BODY)
    assert reviewed.status_code == 201
    proposed = cmd_client.post(f"/api/v1/theses/{thesis_id}/propose")
    assert proposed.status_code == 201
```

- [ ] **Step 2: Run it and verify the intended gate first fails**

Run: `cd backend && pytest tests/test_research_flow_e2e.py::test_frozen_document_to_reviewed_statement_to_evidence_flow -q`

Expected: FAIL until the endpoints, review publication and engine gate are all wired.

- [ ] **Step 3: Implement only integration glue exposed by the failing test**

Ensure the command router, transaction boundaries and `AutoResearchService` return the established `409` error envelope for an attempt to propose from a document with pending claims. Do not special-case the test or bypass the review service.

- [ ] **Step 4: Run migration and regression verification**

Run:

```bash
cd backend && alembic upgrade head
cd backend && pytest tests/test_atomic_claims.py tests/test_atomic_claims_api.py tests/test_extraction_evaluation.py tests/test_table_extraction.py tests/test_ai_engine.py tests/test_pdf_parser_adapters.py tests/test_research_flow_e2e.py -q
```

Expected: migration completes; all focused tests pass; no update/delete is accepted for candidate, observation, review or formal ledger rows.

- [ ] **Step 5: Commit the integrated vertical slice**

```bash
git add backend/tests/test_research_flow_e2e.py
git commit -m "test: cover reviewed claim research flow"
```

## Acceptance checklist

- A model or table rule cannot write a `SourceStatement` directly.
- Every candidate has a continuous source quote, exact offsets, quote hash, immutable source locator, structured fields, authority level and validation report.
- Re-running the same extraction creates an observation/audit record but no duplicate candidate or formal statement.
- A licensed broker report’s company-data restatement remains `reported_claim`; `disclosed_fact` requires primary-disclosure authority.
- A reviewer can confirm, modify or reject a candidate through the API; only a confirmed/validly modified candidate produces a formal statement.
- Pending/rejected candidates cannot be recalled, linked as evidence, used in assessment, projected as formal graph evidence or exposed to stock/fund expression.
- Parser/OCR comparison cannot create a second independent evidence stream; unavailable OCR fails closed.
- Gold v1 runs in CI and enforces exact quote/offset/hash, duplicate, claim-type and authority gates. Licensed production gold materials remain outside the repository unless their contract permits retention.

## Follow-on plans, deliberately not folded into P0-A

1. **P0-B source governance and raw material retention:** `SourceContract`, provider record, blob/object version, AI/export permissions, retention/deletion and tenant enforcement. This gives `authority_level` its fully governed source rather than the initial explicit document stamp.
2. **P1 reviewer workbench:** the Case page’s side-by-side original report, highlighted candidate, edit/split/merge controls, review queue/SLA and next-action guidance. It consumes the APIs in Task 6; it does not change the ledger rules.
3. **P2 private Chinese gold operation:** 30–50 licensed, double-annotated reports across text PDFs, scans, tables and brokers; measured parser selection and model/prompt upgrades with the Gold v1 gate.
