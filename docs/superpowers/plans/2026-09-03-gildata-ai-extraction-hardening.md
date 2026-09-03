# Gildata And AI Extraction Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make two-round Gildata ingest idempotent and provenance-correct, then make live AI extraction bounded, resumable, and accurately reported through the current API contract.

**Architecture:** Keep the existing synchronous command path, but give every Gildata payload a content-addressed provider URI, immutable source governance, and explicit no-supersession semantics. Put retry policy entirely inside `LLMClient`, keep extraction atomic, and move walkthrough response aggregation/configuration checks into pure tested helpers before running a fresh live database through P0–P12.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic, OpenAI-compatible SDK, httpx, pytest, SQLite live-walkthrough ledger.

---

### Task 1: Parse Gildata evidence rights without widening token authority

**Files:**
- Create: `backend/app/datasources/gildata/governance.py`
- Modify: `backend/tests/test_gildata_client.py`

- [ ] **Step 1: Write failing tests for fail-closed rights.**

```python
from app.datasources.gildata.governance import GildataEvidenceRights


def test_gildata_rights_default_to_false(monkeypatch):
    monkeypatch.delenv("GILDATA_ALLOW_AI_PROCESSING", raising=False)
    monkeypatch.delenv("GILDATA_ALLOW_DISPLAY", raising=False)

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is False
    assert rights.allow_display is False
    assert rights.formal_evidence_allowed is False


@pytest.mark.parametrize("value", ["1", "yes", "on", " true ", "false"])
def test_gildata_rights_accept_only_literal_true(monkeypatch, value):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", value)
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "TRUE")

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is False
    assert rights.allow_display is True
    assert rights.formal_evidence_allowed is False
```

- [ ] **Step 2: Run the tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py -k gildata_rights`

Expected: collection fails because `app.datasources.gildata.governance` does not exist.

- [ ] **Step 3: Add the immutable rights boundary.**

```python
from __future__ import annotations

import os
from dataclasses import dataclass


def _literal_true(name: str) -> bool:
    return os.getenv(name, "").lower() == "true"


@dataclass(frozen=True)
class GildataEvidenceRights:
    allow_ai_processing: bool
    allow_display: bool

    @property
    def formal_evidence_allowed(self) -> bool:
        return self.allow_ai_processing and self.allow_display

    @classmethod
    def from_env(cls) -> "GildataEvidenceRights":
        return cls(
            allow_ai_processing=_literal_true("GILDATA_ALLOW_AI_PROCESSING"),
            allow_display=_literal_true("GILDATA_ALLOW_DISPLAY"),
        )
```

- [ ] **Step 4: Run the focused tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py -k gildata_rights`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the isolated configuration change.**

```bash
git add backend/app/datasources/gildata/governance.py backend/tests/test_gildata_client.py
git commit -m "feat: declare Gildata evidence rights"
```

### Task 2: Make Gildata document and span persistence idempotent

**Files:**
- Modify: `backend/app/scripts/ingest_real_data.py`
- Modify: `backend/app/schemas/v1/commands.py`
- Modify: `backend/tests/test_gildata_client.py`
- Modify: `backend/tests/test_ingest_command_api.py`

- [ ] **Step 1: Write a failing replay test that covers documents, spans, and revision links.**

```python
def test_ingest_replay_reuses_document_and_span_without_false_supersession(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    first = ingest(session, _make_client())
    session.flush()
    before = {
        "documents": len(list(session.scalars(select(DocumentVersion)))),
        "spans": len(list(session.scalars(select(SourceSpan)))),
    }

    second = ingest(session, _make_client())
    session.flush()

    assert len(list(session.scalars(select(DocumentVersion)))) == before["documents"]
    assert len(list(session.scalars(select(SourceSpan)))) == before["spans"]
    gildata_documents = list(session.scalars(
        select(DocumentVersion).where(DocumentVersion.source_url.like("gildata://%"))
    ))
    assert all(document.supersedes_id is None for document in gildata_documents)
    assert first["spans"] > 0
    assert second["spans"] == 0
    assert second["spans_reused"] == before["spans"]
```

- [ ] **Step 2: Write a failing invariant test for same-title/date bodies.**

```python
def test_gildata_variant_body_never_attaches_to_an_unrelated_frozen_document(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    def client_for(body):
        markdown = (
            "报告标题：同名研报；\n发布时间：2026-09-03；\n"
            f"撰写机构：测试机构；\n原文：{body}"
        )
        return _FakeClient([[{"table_markdown": markdown}]], [], [])

    ingest(session, client_for("正文甲"), research_queries=["同名研报"])
    session.flush()
    ingest(session, client_for("正文乙"), research_queries=["同名研报"])
    session.flush()

    documents = list(session.scalars(
        select(DocumentVersion).where(DocumentVersion.source_url.like("gildata://research-report/%"))
    ))
    assert len(documents) == 2
    for document in documents:
        spans = list(session.scalars(
            select(SourceSpan).where(SourceSpan.document_version_id == document.id)
        ))
        assert len(spans) == 1
        assert hashlib.sha256(spans[0].verbatim_text.encode()).hexdigest() == document.content_sha256
```

- [ ] **Step 3: Run both tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py -k 'replay_reuses_document or variant_body'`

Expected: span count increases on replay and/or the second body is attached to the first document.

- [ ] **Step 4: Add content-addressed helpers and use status-bearing freeze calls.**

```python
def _provider_uri(source_kind: str, raw: bytes) -> str:
    return f"gildata://{source_kind}/{hashlib.sha256(raw).hexdigest()}"


def _freeze_full_body(
    documents: DocumentService,
    *,
    raw: bytes,
    source_kind: str,
    published_at: datetime | None,
    title: str,
) -> tuple[DocumentVersion, bool]:
    return documents.freeze_with_status(
        raw=raw,
        source_url=_provider_uri(source_kind, raw),
        published_at=published_at,
        parser_version=PARSER_VERSION,
        title=title,
        natural_key=f"gildata:{hashlib.sha256(raw).hexdigest()[:23]}",
        source_authority="licensed_research",
        infer_supersedes=False,
    )
```

For full-body sources, call `add_span` only when `created` is true, pass
`text_sha256=hashlib.sha256(raw).hexdigest()`, and increment
`summary["spans"]` only after insertion. Add `spans_reused` plus per-kind
`research_reports_reused`, `announcements_reused`, `news_reused`, and
`macro_series_reused` fields to `IngestResponse`. For a reused document, load
its existing spans and raise `GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)`
unless exactly one full-body span has the matching text digest.

- [ ] **Step 5: Make macro replay use deterministic span identity.**

Use the frozen body digest in the macro document URI, disable inferred
supersession, and only create metric spans when the macro document is new.
Every macro span must receive `text_sha256` calculated from its own text.

- [ ] **Step 6: Re-run the focused tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py -k 'idempotent or replay or variant or supersession'`

Expected: all selected tests pass with unchanged document and span counts on replay.

- [ ] **Step 7: Commit the persistence repair.**

```bash
git add backend/app/scripts/ingest_real_data.py backend/app/schemas/v1/commands.py backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py
git commit -m "fix: preserve Gildata document identity"
```

### Task 3: Reject a quote returned for the wrong security

**Files:**
- Modify: `backend/app/scripts/ingest_real_data.py`
- Modify: `backend/tests/test_gildata_client.py`
- Modify: `backend/tests/test_ingest_command_api.py`

- [ ] **Step 1: Write a failing service test for provider identity mismatch.**

```python
def test_ingest_rejects_quote_for_a_different_security(session):
    from app.scripts.ingest_real_data import find_stock_by_code, ingest

    mismatched_quote = (
        "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
        "|---|---|---|---|---|---|\n"
        "|工业富联|601138.SH|50.00|20|3|2.0e11|"
    )
    client = _FakeClient([], [], [{"table_markdown": mismatched_quote}])

    with pytest.raises(GildataMCPError, match=GILDATA_RESPONSE_ERROR_MESSAGE):
        ingest(
            session,
            client,
            research_queries=[],
            quote_stock_code="688256",
        )

    assert find_stock_by_code(session, "601138") is None
```

- [ ] **Step 2: Write a failing API test for rollback and safe error text.**

```python
def test_ingest_command_rolls_back_a_quote_identity_mismatch(
    cmd_client, cmd_seeded
):
    from app.api.v1.commands.ingest import get_gildata_client
    from app.main import app
    from app.models.ledger import DocumentVersion, ResearchCase

    mismatched_quote = (
        "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
        "|---|---|---|---|---|---|\n"
        "|工业富联|601138.SH|50.00|20|3|2.0e11|"
    )
    client = _FakeClient([], [], [{"table_markdown": mismatched_quote}])
    app.dependency_overrides[get_gildata_client] = lambda: client
    before = cmd_seeded.scalar(select(func.count()).select_from(DocumentVersion))
    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))

    response = cmd_client.post(
        "/api/v1/documents/ingest",
        json={
            "case_id": str(case.id),
            "research_queries": [],
            "quote_stock_code": "688256",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == GILDATA_REQUEST_ERROR_MESSAGE
    assert cmd_seeded.scalar(select(func.count()).select_from(DocumentVersion)) == before
    app.dependency_overrides.pop(get_gildata_client, None)
```

- [ ] **Step 3: Run both tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py -k quote_identity_mismatch`

Expected: ingest creates the returned security instead of rejecting it.

- [ ] **Step 4: Validate normalized codes before stock creation.**

```python
def _bare_security_code(value: str) -> str:
    return value.strip().upper().split(".", 1)[0]


returned_code = quote.get("stock_code", "").strip()
if returned_code and _bare_security_code(returned_code) != _bare_security_code(quote_stock_code):
    raise GildataMCPError(GILDATA_RESPONSE_ERROR_MESSAGE)
resolved_code = returned_code or quote_stock_code
```

- [ ] **Step 5: Re-run tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py -k quote`

Expected: selected quote tests pass and the mismatch path leaves no partial writes after the API rollback.

- [ ] **Step 6: Commit the identity guard.**

```bash
git add backend/app/scripts/ingest_real_data.py backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py
git commit -m "fix: validate Gildata quote identity"
```

### Task 4: Record immutable Gildata contracts and provider records

**Files:**
- Modify: `backend/app/api/v1/commands/ingest.py`
- Modify: `backend/app/scripts/ingest_real_data.py`
- Modify: `backend/tests/test_gildata_client.py`
- Modify: `backend/tests/test_ingest_command_api.py`

- [ ] **Step 1: Write a failing contract/replay test.**

```python
def test_gildata_ingest_records_one_compatible_contract_and_provider_record_per_document(
    session, monkeypatch
):
    from app.models.ledger import DocumentVersion
    from app.models.source_governance import ProviderRecord, SourceContract
    from app.scripts.ingest_real_data import ingest

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")

    ingest(session, _make_client())
    session.flush()
    ingest(session, _make_client())
    session.flush()

    documents = list(session.scalars(select(DocumentVersion).where(
        DocumentVersion.source_url.like("gildata://%")
    )))
    contracts = list(session.scalars(select(SourceContract)))
    records = list(session.scalars(select(ProviderRecord)))
    assert len(contracts) == len(records) == len(documents)
    assert all(c.source_type == c.research_source_type == "licensed_provider" for c in contracts)
    assert all(c.provider_or_tenant == "gildata" for c in contracts)
    assert all(c.allow_ai_processing and c.allow_display for c in contracts)
```

- [ ] **Step 2: Run the test and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py -k compatible_contract`

Expected: no source contracts or provider records exist.

- [ ] **Step 3: Reconcile governance immediately after every freeze.**

```python
def _record_gildata_governance(
    session: Session,
    *,
    document: DocumentVersion,
    source_kind: str,
    query: str,
    rights: GildataEvidenceRights,
    declared_by: str,
) -> None:
    digest = document.content_sha256
    SourceGovernanceService(session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={
            "research_source_type": "licensed_provider",
            "provider_name": "gildata",
            "provider_record_id": f"{source_kind}:{digest}",
            "retrieval_reference": document.source_url,
            "request_scope": {"source_kind": source_kind, "query_sha256": hashlib.sha256(query.encode()).hexdigest()},
            "permissions": {
                "ai_processing": rights.allow_ai_processing,
                "display": rights.allow_display,
                "export": False,
                "api": False,
            },
            "contract_version": "gildata-local-rights-v1",
            "downstream_restrictions": ["仅限当前 Case 研究与人工审核"],
        },
        declared_by=declared_by,
        incoming_source_url=document.source_url,
    )
```

Compute `rights` once per logical ingest. Add a `declared_by` keyword argument
whose service default is `system:gildata-ingest`; have
`ingest_documents(..., tenant_id=...)` pass `declared_by=f"tenant:{tenant_id}"`.
Call this helper for both new and reused documents so an incompatible immutable
declaration fails closed.

- [ ] **Step 4: Re-run governance and ingest suites and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py`

Expected: both suites pass and replay creates no duplicate governance rows.

- [ ] **Step 5: Commit the governance wiring.**

```bash
git add backend/app/api/v1/commands/ingest.py backend/app/scripts/ingest_real_data.py backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py
git commit -m "feat: govern Gildata ingest evidence"
```

### Task 5: Admit only active, rights-bearing Gildata provider documents

**Files:**
- Modify: `backend/app/services/source_admission.py`
- Modify: `backend/app/queries/review_queue.py`
- Modify: `backend/app/services/event_review_queue.py`
- Modify: `backend/tests/test_source_admission.py`
- Modify: `backend/tests/test_event_review_queue.py`
- Modify: `backend/tests/test_proposal_review_api.py`

- [ ] **Step 1: Write failing source-admission tests.**

```python
from types import SimpleNamespace


def _licensed_gildata_contract(*, ai: bool, display: bool):
    return SimpleNamespace(
        source_type="licensed_provider",
        research_source_type="licensed_provider",
        provider_or_tenant="gildata",
        allow_ai_processing=ai,
        allow_display=display,
        effective_from=None,
        effective_until=None,
    )


def test_active_rights_bearing_gildata_document_is_admissible():
    admission = classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=True, display=True),
    )
    assert admission.can_accept is True


@pytest.mark.parametrize("ai,display", [(False, True), (True, False), (False, False)])
def test_gildata_document_without_both_rights_is_not_admissible(ai, display):
    admission = classify_document_source(
        source_url="gildata://research-report/" + "a" * 64,
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=ai, display=display),
    )
    assert admission.can_accept is False
```

- [ ] **Step 2: Write a failing publication-boundary test.**

```python
def _seed_authorised_gildata_proposal(cmd_session):
    from app.models.source_governance import SourceContract

    uri = "gildata://research-report/" + "a" * 64
    proposal = _seed_event_evidence_proposal(cmd_session, source_url=uri)
    statement = cmd_session.get(
        SourceStatement, uuid.UUID(proposal.payload["source_statement_id"])
    )
    span = cmd_session.get(SourceSpan, statement.source_span_id)
    document = cmd_session.get(DocumentVersion, span.document_version_id)
    document.parser_version = "gildata-mcp-1"
    document.content_sha256 = "a" * 64
    cmd_session.add(SourceContract(
        document_version_id=document.id,
        source_type="licensed_provider",
        research_source_type="licensed_provider",
        provider_or_tenant="gildata",
        allow_ai_processing=True,
        allow_display=True,
        allow_export=False,
        allow_api=False,
        region="cn",
        effective_from=None,
        effective_until=None,
        retention_policy="case_retained",
        deletion_policy="manual",
        downstream_restrictions=["仅限测试 Case"],
        contract_version="test-v1",
        intake_metadata={},
        declared_by="human:test",
        created_at=datetime.now(timezone.utc),
    ))
    cmd_session.add(proposal)
    cmd_session.commit()
    return proposal


def test_confirmed_authorised_gildata_proposal_publishes_evidence_link(
    cmd_client, cmd_session
):
    proposal = _seed_authorised_gildata_proposal(cmd_session)
    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "许可范围内人工确认",
            "reviewer_id": "human:reviewer",
            "expected_version": proposal.version,
        },
    )
    assert response.status_code == 201
    assert response.json()["published_entity_id"]
```

- [ ] **Step 3: Run tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py backend/tests/test_proposal_review_api.py -k gildata`

Expected: `gildata://` remains rejected by the generic public-URL classifier.

- [ ] **Step 4: Add and consume one shared classifier.**

```python
def classify_document_source(
    *, source_url: str | None, parser_version: str | None,
    content_verified: bool, contract: SourceContract | None,
) -> SourceAdmission:
    if _is_authorised_gildata_reference(
        source_url=source_url,
        parser_version=parser_version,
        content_verified=content_verified,
        contract=contract,
    ):
        base = SourceAdmission(
            SourceStatus.ACCESSIBLE,
            "授权 Gildata 资料已冻结并可用于正式证据。",
            True,
        )
    else:
        base = classify_source(source_url, parser_version, content_verified)
    return apply_source_contract(base, contract)
```

`_is_authorised_gildata_reference` must require a 64-character lowercase hex
digest path, `gildata-mcp-` parser, verified content, a
`licensed_provider` contract whose `research_source_type` is also
`licensed_provider`, and case-insensitive provider identity `gildata`.
Replace direct `classify_source` + `apply_source_contract` pairs in proposal
context and event queue counting with `classify_document_source`.

- [ ] **Step 5: Run admission/publication tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py backend/tests/test_proposal_review_api.py`

Expected: all suites pass, including generic invalid-scheme rejection.

- [ ] **Step 6: Commit the admission seam.**

```bash
git add backend/app/services/source_admission.py backend/app/queries/review_queue.py backend/app/services/event_review_queue.py backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py backend/tests/test_proposal_review_api.py
git commit -m "feat: admit authorised Gildata evidence"
```

### Task 6: Bound and classify live LLM retries

**Files:**
- Modify: `backend/app/ai/client.py`
- Modify: `backend/tests/test_ai_client_determinism.py`

- [ ] **Step 1: Write failing output-budget and retry-policy tests.**

```python
class _SequenceCompletions:
    def __init__(self, first_error):
        self.first_error = first_error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise self.first_error
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = '{"ok": true}'
        return response


class _HTTPStatusCompletions:
    def __init__(self, status_code, *, retry_after=None, succeeds_after=False):
        request = httpx.Request("POST", "https://llm.example.invalid/v1/chat/completions")
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        response = httpx.Response(status_code, request=request, headers=headers)
        self.error = httpx.HTTPStatusError(
            "provider status", request=request, response=response
        )
        self.calls = 0
        self.succeeds_after = succeeds_after

    def create(self, **_kwargs):
        self.calls += 1
        if self.succeeds_after and self.calls > 1:
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = '{"ok": true}'
            return response
        raise self.error


def test_live_call_forwards_configured_max_output_tokens():
    fake = _FakeOpenAIClient()
    client = LLMClient(model_version="test-model", client=fake, max_output_tokens=2048)
    client.chat_json([{"role": "user", "content": "{}"}])
    assert fake.chat.completions.calls[0]["max_tokens"] == 2048


def test_transient_failure_retries_with_bounded_backoff():
    sleep_calls = []
    completions = _SequenceCompletions(httpx.ReadTimeout("slow"))
    client = LLMClient(
        model_version="test-model",
        client=_ClientWithCompletions(completions),
        max_attempts=2,
        retry_base_seconds=0.25,
        retry_max_seconds=1.0,
        sleep=sleep_calls.append,
    )
    assert client.chat_json([{"role": "user", "content": "{}"}]) == {"ok": True}
    assert sleep_calls == [0.25]


def test_nonretryable_400_is_attempted_once():
    completions = _HTTPStatusCompletions(400)
    client = LLMClient(
        model_version="test-model",
        client=_ClientWithCompletions(completions),
        max_attempts=3,
        sleep=lambda _: pytest.fail("must not sleep"),
    )
    with pytest.raises(LLMProviderError):
        client.chat_json([{"role": "user", "content": "{}"}])
    assert completions.calls == 1


def test_retry_after_is_honoured_but_clamped():
    sleep_calls = []
    completions = _HTTPStatusCompletions(
        429, retry_after="30", succeeds_after=True
    )
    client = LLMClient(
        model_version="test-model",
        client=_ClientWithCompletions(completions),
        max_attempts=2,
        retry_base_seconds=0.25,
        retry_max_seconds=2.0,
        sleep=sleep_calls.append,
    )
    assert client.chat_json([{"role": "user", "content": "{}"}]) == {"ok": True}
    assert sleep_calls == [2.0]
```

- [ ] **Step 2: Run the tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_client_determinism.py -k 'max_output or bounded_backoff or nonretryable'`

Expected: constructor arguments/forwarded output budget and status classification are absent.

- [ ] **Step 3: Add validated configuration and one retry authority.**

Add constructor fields `max_output_tokens`, `retry_base_seconds`,
`retry_max_seconds`, and injected `sleep`. Parse
`LLM_MAX_OUTPUT_TOKENS`, `LLM_RETRY_BASE_SECONDS`, and
`LLM_RETRY_MAX_SECONDS` in `from_env`; reject non-finite/non-positive bounds.
Forward `max_tokens=self._max_output_tokens` in `create_kwargs`.

Use a private `_is_transient_provider_error(exc)` that returns true for
`httpx.TimeoutException`, `httpx.NetworkError`, `TimeoutError`,
`ConnectionError`, OpenAI connection/timeout failures, and OpenAI status
errors with status 408, 429, or 500–599. Other 4xx and malformed successful
responses are terminal. Read `Retry-After` only from a retryable status
response, accept finite non-negative seconds only, and clamp it to
`retry_max_seconds`. Otherwise use exponential backoff. Before each retry call:

```python
delay = min(self._retry_base_seconds * (2 ** attempt), self._retry_max_seconds)
self._sleep(delay)
```

Preserve `LLM_PROVIDER_ERROR_MESSAGE` as the only raised message.

- [ ] **Step 4: Add environment validation tests.**

```python
@pytest.mark.parametrize("name,value", [
    ("LLM_MAX_OUTPUT_TOKENS", "0"),
    ("LLM_RETRY_BASE_SECONDS", "nan"),
    ("LLM_RETRY_MAX_SECONDS", "-1"),
])
def test_from_env_rejects_invalid_llm_bounds(monkeypatch, name, value):
    _isolate_llm_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        LLMClient.from_env()
```

- [ ] **Step 5: Run the whole client suite and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_client_determinism.py`

Expected: all tests pass; existing secret-sanitization and programming-error tests stay green.

- [ ] **Step 6: Commit the LLM policy.**

```bash
git add backend/app/ai/client.py backend/tests/test_ai_client_determinism.py
git commit -m "fix: bound live LLM extraction retries"
```

### Task 7: Make extraction audit counts explain rejected model items

**Files:**
- Modify: `backend/app/ai/extraction.py`
- Modify: `backend/tests/test_ai_engine.py`
- Modify: `backend/tests/test_engine_commands_api.py`

- [ ] **Step 1: Write a failing mixed-candidate test.**

```python
def test_extract_keeps_valid_candidate_and_audits_rejected_sibling(
    session, span
):
    client = LLMClient(model_version="test-model", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": span.verbatim_text,
        "kind": "reported_claim",
    }
    invalid = {**valid, "quote": "not present in source"}
    with patch.object(
        client, "chat_json", return_value={"statements": [valid, invalid]}
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id, session
        )
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert "llm returned 2" in run.output_summary
    assert "accepted 1" in run.output_summary
    assert "rejected 1" in run.output_summary
```

- [ ] **Step 2: Run the test and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engine.py -k rejected_sibling`

Expected: the current summary records only created candidates.

- [ ] **Step 3: Count protocol outputs separately from admitted candidates.**

Before iterating model items, store `llm_returned_count` only when
`statements` is a list; otherwise raise `LLMMalformedResponseError`. Increment
`llm_rejected_count` for every unknown span, invalid shape, missing key,
invalid quote, or domain validation rejection. Emit all three counts in the
successful `AIRun.output_summary` without logging candidate content.

- [ ] **Step 4: Re-run extraction and command suites and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engine.py backend/tests/test_engine_commands_api.py`

Expected: all tests pass and extraction remains atomic on provider failure.

- [ ] **Step 5: Commit the audit improvement.**

```bash
git add backend/app/ai/extraction.py backend/tests/test_ai_engine.py backend/tests/test_engine_commands_api.py
git commit -m "fix: audit AI candidate rejection counts"
```

### Task 8: Repair walkthrough aggregation, startup, and resume semantics

**Files:**
- Modify: `backend/app/scripts/walkthrough_support.py`
- Modify: `backend/scripts/walkthrough_cambricon_case.py`
- Modify: `backend/tests/test_walkthrough_support.py`

- [ ] **Step 1: Write failing pure response aggregation tests.**

```python
def test_summarize_extract_response_uses_candidate_contract():
    result = summarize_extract_response({
        "candidate_count": 2,
        "candidates": [
            {"claim_type": "reported_claim"},
            {"claim_type": "research_opinion"},
        ],
        "reason": None,
    })
    assert result == {
        "candidate_count": 2,
        "claim_types": {"reported_claim": 1, "research_opinion": 1},
        "reason": None,
    }


def test_summarize_extract_response_rejects_count_mismatch():
    with pytest.raises(WalkthroughResponseError, match="candidate_count"):
        summarize_extract_response({"candidate_count": 1, "candidates": []})
```

- [ ] **Step 2: Write a failing CLI test proving `--help` needs no credentials.**

```python
def test_walkthrough_help_does_not_require_runtime_credentials():
    environment = {key: value for key, value in os.environ.items() if key != "RESEARCH_TENANT_TOKENS"}
    result = subprocess.run(
        [sys.executable, "scripts/walkthrough_cambricon_case.py", "--help"],
        cwd=BACKEND_DIR, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--stages" in result.stdout
```

- [ ] **Step 3: Run tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py -k 'summarize_extract or help_does_not_require'`

Expected: helper is absent and import-time credential resolution breaks `--help`.

- [ ] **Step 4: Add the pure response helper and update P3.**

```python
class WalkthroughResponseError(RuntimeError):
    pass


def summarize_extract_response(response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        raise WalkthroughResponseError("extract response must be an object")
    candidates = response.get("candidates")
    count = response.get("candidate_count")
    if not isinstance(candidates, list) or not isinstance(count, int) or count != len(candidates):
        raise WalkthroughResponseError("candidate_count does not match candidates")
    claim_types: dict[str, int] = {}
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("claim_type"), str):
            raise WalkthroughResponseError("candidate claim_type is missing")
        kind = item["claim_type"]
        claim_types[kind] = claim_types.get(kind, 0) + 1
    return {"candidate_count": count, "claim_types": claim_types, "reason": response.get("reason")}
```

Use this result in `phase3_extract`; rename summary fields to `candidates` and
`claim_types`. Preserve successful zero-output documents with their `reason`.
After the batch, call the documents read endpoint again and derive
`pending_after` from actual `extraction_state` values.

- [ ] **Step 5: Move credential resolution after argument parsing and add preflight.**

Keep `AUTH_HEADERS` unset at import time. In `main`, parse arguments first,
then resolve tenant headers and require both Gildata rights. Validate that live
P3 has `LLM_MAX_ATTEMPTS >= 2`, positive timeout, and positive output budget.
Do not print credential values.

- [ ] **Step 6: Re-run walkthrough support tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py`

Expected: all tests pass, including credential-free `--help`.

- [ ] **Step 7: Commit the walkthrough repair.**

```bash
git add backend/app/scripts/walkthrough_support.py backend/scripts/walkthrough_cambricon_case.py backend/tests/test_walkthrough_support.py
git commit -m "fix: reconcile live extraction walkthrough"
```

### Task 9: Add a programmatic live acceptance audit

**Files:**
- Create: `backend/app/scripts/audit_gildata_ai_walkthrough.py`
- Create: `backend/tests/test_walkthrough_acceptance_audit.py`
- Modify: `backend/scripts/walkthrough_cambricon_case.py`

- [ ] **Step 1: Write failing database-audit tests.**

```python
from dataclasses import replace

from app.scripts.audit_gildata_ai_walkthrough import (
    WalkthroughAuditFacts,
    evaluate_walkthrough_facts,
)


def _accepted_facts():
    return WalkthroughAuditFacts(
        gildata_rounds=2,
        pending_extractions=0,
        summary_candidate_count=8,
        persisted_candidate_count=8,
        gildata_supersession_count=0,
        duplicate_full_body_span_count=0,
        invalid_full_body_span_count=0,
        gildata_document_count=4,
        gildata_contract_count=4,
        gildata_provider_record_count=4,
        published_gildata_evidence_count=1,
    )


def test_audit_rejects_duplicate_full_body_spans():
    result = evaluate_walkthrough_facts(
        replace(_accepted_facts(), duplicate_full_body_span_count=1)
    )
    assert result.ok is False
    assert "duplicate_full_body_span" in result.issue_codes


def test_audit_accepts_consistent_two_round_live_state():
    result = evaluate_walkthrough_facts(_accepted_facts())
    assert result.ok is True
    assert result.metrics["gildata_rounds"] == 2
    assert result.metrics["pending_extractions"] == 0
    assert result.metrics["candidate_count"] > 0
    assert result.metrics["published_evidence_links"] > 0
```

- [ ] **Step 2: Run tests and confirm RED.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_acceptance_audit.py`

Expected: audit module does not exist.

- [ ] **Step 3: Implement the read-only audit.**

Implement frozen `WalkthroughAuditFacts` and `WalkthroughAuditResult`
dataclasses. `collect_walkthrough_facts(session, summary)` performs the
read-only SQLAlchemy counts; `evaluate_walkthrough_facts(facts)` applies these
checks:

```python
checks = {
    "two_ingest_rounds": facts.gildata_rounds == 2,
    "no_pending_extractions": facts.pending_extractions == 0,
    "candidate_totals_match": facts.persisted_candidate_count == facts.summary_candidate_count,
    "no_false_supersession": facts.gildata_supersession_count == 0,
    "duplicate_full_body_span": facts.duplicate_full_body_span_count == 0,
    "full_body_span_integrity": facts.invalid_full_body_span_count == 0,
    "one_contract_per_document": facts.gildata_contract_count == facts.gildata_document_count,
    "one_provider_record_per_document": facts.gildata_provider_record_count == facts.gildata_document_count,
    "formal_evidence_published": facts.published_gildata_evidence_count > 0,
}
```

Return a frozen result with `ok`, `issue_codes`, and numeric `metrics`. Do not
include source bodies, tokens, credential-bearing URLs, or raw provider errors.
Have the terminal walkthrough stage store this audit in the summary and add an
issue for every failed check.

- [ ] **Step 4: Run audit and walkthrough tests and confirm GREEN.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_acceptance_audit.py backend/tests/test_walkthrough_support.py`

Expected: all tests pass.

- [ ] **Step 5: Commit the acceptance gate.**

```bash
git add backend/app/scripts/audit_gildata_ai_walkthrough.py backend/tests/test_walkthrough_acceptance_audit.py backend/scripts/walkthrough_cambricon_case.py
git commit -m "test: gate Gildata AI live acceptance"
```

### Task 10: Verify code and run a fresh live walkthrough

**Files:**
- Inspect: `docs/evaluation/walkthrough/cambricon_walkthrough_<run-id>_summary.json`
- Inspect: `docs/evaluation/walkthrough/cambricon_walkthrough_<run-id>.jsonl`
- Inspect: `backend/evidence_walkthrough_<run-id>.db`

- [ ] **Step 1: Run the complete focused regression set.**

Run:

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_gildata_client.py \
  backend/tests/test_ingest_command_api.py \
  backend/tests/test_source_admission.py \
  backend/tests/test_event_review_queue.py \
  backend/tests/test_proposal_review_api.py \
  backend/tests/test_ai_client_determinism.py \
  backend/tests/test_ai_engine.py \
  backend/tests/test_engine_commands_api.py \
  backend/tests/test_walkthrough_support.py \
  backend/tests/test_walkthrough_acceptance_audit.py
```

Expected: zero failures.

- [ ] **Step 2: Run the full backend suite.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests`

Expected: zero failures.

- [ ] **Step 3: Run both Gildata rounds in a fresh ledger.**

Run:

```bash
RESEARCH_TENANT_TOKENS='{"walkthrough-token":{"tenant_id":"walkthrough","roles":[]}}' \
LLM_MAX_ATTEMPTS=2 \
LLM_TIMEOUT_SECONDS=90 \
LLM_MAX_OUTPUT_TOKENS=2048 \
WALKTHROUGH_RUN_ID=gildata-ai-hardened-20260903 \
backend/.venv/bin/python backend/scripts/walkthrough_cambricon_case.py --stages p0_p1_p2
```

Expected: both P2 responses are 201 and the state checkpoint is written.

- [ ] **Step 4: Replay both rounds and prove idempotency before extraction.**

Run the command from Step 3 a second time with the same run ID.

Expected: the acceptance audit reports no new document, span, contract,
provider-record, case-attachment, or valuation rows and no false
supersession.

- [ ] **Step 5: Resume P3 until no eligible document remains pending.**

Run:

```bash
RESEARCH_TENANT_TOKENS='{"walkthrough-token":{"tenant_id":"walkthrough","roles":[]}}' \
LLM_MAX_ATTEMPTS=2 \
LLM_TIMEOUT_SECONDS=90 \
LLM_MAX_OUTPUT_TOKENS=2048 \
WALKTHROUGH_RUN_ID=gildata-ai-hardened-20260903 \
backend/.venv/bin/python backend/scripts/walkthrough_cambricon_case.py --stages p3
```

Repeat only while the summary/state reports eligible pending versions. Each
successful or honestly empty version must be skipped on the next resume.

- [ ] **Step 6: Finish publication, assessment, and terminal audit.**

Run the same environment with:

```bash
backend/.venv/bin/python backend/scripts/walkthrough_cambricon_case.py \
  --stages p4_p5,p6,p7_p8,p9_plus
```

Expected: at least one atomic claim, source statement, formally admitted
Gildata evidence link, and non-empty-evidence assessment exist; final audit is
`ok: true` with no unresolved summary issue.

- [ ] **Step 7: Inspect artifacts for secrets and exact count reconciliation.**

Run:

```bash
rg -n 'token=|Authorization|Bearer |LLM_API_KEY|GILDATA_TOKEN' \
  docs/evaluation/walkthrough/cambricon_walkthrough_gildata-ai-hardened-20260903* \
  || true
backend/.venv/bin/python -m app.scripts.audit_gildata_ai_walkthrough \
  --database backend/evidence_walkthrough_gildata-ai-hardened-20260903.db \
  --summary docs/evaluation/walkthrough/cambricon_walkthrough_gildata-ai-hardened-20260903_summary.json
```

Expected: secret scan has no matches and the audit exits zero.

- [ ] **Step 8: Run final repository checks and request code review.**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; unrelated pre-existing user changes remain untouched.
