# Gildata Formal Evidence Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow explicitly licensed Gildata material to publish formal EvidenceLinks without weakening the rejection of untrusted non-HTTP(S) sources.

**Architecture:** Gildata ingest derives one immutable `SourceContract` per frozen document from explicit deployment-owned downstream-use flags. Source admission gains a narrow contract-aware branch for `gildata://` provider references and otherwise preserves the existing strict HTTP(S) classifier. Proposal review and event-queue summary consume the same admission function, so reviewers, publication, and counts cannot disagree.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, python-dotenv.

---

### Task 1: Make Gildata downstream-use rights explicit and fail closed

**Files:**
- Create: `backend/app/datasources/gildata/governance.py`
- Modify: `backend/tests/test_gildata_client.py`

- [ ] **Step 1: Write failing configuration tests.**

```python
from app.datasources.gildata.governance import GildataEvidenceRights

def test_gildata_evidence_rights_default_to_no_formal_use(monkeypatch):
    monkeypatch.delenv("GILDATA_ALLOW_AI_PROCESSING", raising=False)
    monkeypatch.delenv("GILDATA_ALLOW_DISPLAY", raising=False)
    assert GildataEvidenceRights.from_env().formal_evidence_allowed is False

def test_gildata_evidence_rights_require_both_explicit_grants(monkeypatch):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    assert GildataEvidenceRights.from_env().formal_evidence_allowed is True
```

- [ ] **Step 2: Run the focused test and verify it fails because the module does not exist.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py -k evidence_rights`

Expected: import failure for `app.datasources.gildata.governance`.

- [ ] **Step 3: Add the immutable configuration unit.**

```python
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
            allow_ai_processing=_env_true("GILDATA_ALLOW_AI_PROCESSING"),
            allow_display=_env_true("GILDATA_ALLOW_DISPLAY"),
        )
```

`_env_true` accepts only case-insensitive `true`; unset and every other value
are false. It must not read or return the Gildata token.

- [ ] **Step 4: Re-run the focused test.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py -k evidence_rights`

Expected: 2 passed.

- [ ] **Step 5: Commit the configuration boundary.**

```bash
git add backend/app/datasources/gildata/governance.py backend/tests/test_gildata_client.py
git commit -m "feat: declare Gildata evidence rights"
```

### Task 2: Admit only an authorised Gildata provider reference

**Files:**
- Modify: `backend/app/services/source_admission.py`
- Modify: `backend/app/queries/review_queue.py`
- Modify: `backend/tests/test_source_admission.py`
- Modify: `backend/tests/test_event_review_queue.py`

- [ ] **Step 1: Write failing admission tests for the new seam.**

```python
def test_authorised_gildata_contract_admits_its_provider_uri():
    result = classify_document_source(
        source_url="gildata://research_report/content-sha256",
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=True, display=True),
    )
    assert result.can_accept is True
    assert result.status is SourceStatus.ACCESSIBLE

def test_gildata_uri_without_both_contract_rights_remains_rejected():
    result = classify_document_source(
        source_url="gildata://research_report/content-sha256",
        parser_version="gildata-mcp-1",
        content_verified=True,
        contract=_licensed_gildata_contract(ai=True, display=False),
    )
    assert result.can_accept is False
```

- [ ] **Step 2: Run the tests and verify the helper is absent.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_source_admission.py -k gildata`

Expected: import failure for `classify_document_source`.

- [ ] **Step 3: Add one shared `classify_document_source` helper.**

```python
def classify_document_source(*, source_url, parser_version, content_verified, contract):
    if _is_authorised_gildata_reference(source_url, parser_version, contract):
        base = SourceAdmission(SourceStatus.ACCESSIBLE, "授权 Gildata 资料已冻结并可用于正式证据。", True)
    else:
        base = classify_source(source_url, parser_version, content_verified)
    return apply_source_contract(base, contract)
```

`_is_authorised_gildata_reference` requires all of: a `gildata://` URI with a
non-empty path after the source kind, `gildata-mcp-` parser version,
`source_type == research_source_type == "licensed_provider"`,
`provider_or_tenant == "gildata"`, and verified parsed content. It must not
grant access itself; `apply_source_contract` still enforces permissions and
effective dates. `proposal_evidence_context` and event-queue counting must use
this helper instead of calling `classify_source` directly.

- [ ] **Step 4: Re-run source and queue regressions.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py`

Expected: all pass; invalid generic `gildata://` references remain invalid.

- [ ] **Step 5: Commit the admission seam.**

```bash
git add backend/app/services/source_admission.py backend/app/queries/review_queue.py backend/app/services/event_review_queue.py backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py
git commit -m "feat: admit authorised Gildata evidence"
```

### Task 3: Attach an immutable Gildata contract at ingest time

**Files:**
- Modify: `backend/app/scripts/ingest_real_data.py`
- Modify: `backend/tests/test_gildata_client.py`
- Modify: `backend/tests/test_ingest_command_api.py`

- [ ] **Step 1: Write a failing API-path test.**

```python
def test_ingest_records_authorised_gildata_contracts(fake_gildata, cmd_seeded, monkeypatch):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    case = _first_case(cmd_seeded)
    assert fake_gildata.post("/api/v1/documents/ingest", json={"case_id": str(case.id)}).status_code == 201
    contracts = list(cmd_seeded.scalars(select(SourceContract)))
    assert any(c.provider_or_tenant == "gildata" and c.allow_ai_processing and c.allow_display for c in contracts)
```

- [ ] **Step 2: Run it and verify no Gildata contract exists.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ingest_command_api.py -k authorised_gildata_contracts`

Expected: assertion failure because the contract list is empty.

- [ ] **Step 3: Freeze unique provider references and record contracts.**

For every report, announcement, news item, and macro document, derive an
opaque per-content provider URI using the immutable SHA-256 of the exact bytes:

```python
source_url = f"gildata://{source_kind}/{hashlib.sha256(raw).hexdigest()}"
```

Immediately after `DocumentService._freeze`, call
`SourceGovernanceService(session).record_event_intake(...)` with
`source_type="licensed_provider"`, `research_source_type="licensed_provider"`,
`provider_name="gildata"`, `provider_record_id=<stable document natural key>`,
`retrieval_reference=<provider URI>`, and permissions from
`GildataEvidenceRights.from_env()`. Use the case tenant as `declared_by` when
available, otherwise `"system:gildata-ingest"`; rights remain false unless
both deployment flags are true. Call the same function on reused frozen
documents so incompatible existing contracts fail closed.  The provider URI
must use the same kind/title/publication-date natural key as `DocumentService`;
using a body hash would conflict when the provider returns revised bytes for an
otherwise deduplicated material.

- [ ] **Step 4: Re-run ingest regressions.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py`

Expected: all pass, including idempotent re-ingest.

- [ ] **Step 5: Commit ingest provenance.**

```bash
git add backend/app/scripts/ingest_real_data.py backend/tests/test_gildata_client.py backend/tests/test_ingest_command_api.py
git commit -m "feat: govern Gildata ingest evidence"
```

### Task 4: Prove formal publication through the real command boundary

**Files:**
- Modify: `backend/tests/test_proposal_review_api.py`
- Modify: `backend/tests/test_engine_commands_api.py`

- [ ] **Step 1: Write a failing end-to-end API test.**

```python
def test_confirmed_authorised_gildata_proposal_publishes_formal_evidence(cmd_client, cmd_session):
    proposal = _seed_gildata_proposal(cmd_session, rights=(True, True))
    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={"outcome": "confirmed", "reason": "licence permits use", "reviewer_id": "human", "expected_version": proposal.version},
    )
    assert response.status_code == 201
    assert response.json()["published_entity_id"]
```

- [ ] **Step 2: Run it and verify formal publication is blocked before the new admission path lands.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_proposal_review_api.py -k authorised_gildata`

Expected: 422 `event evidence source cannot be accepted for formal publication`.

- [ ] **Step 3: Keep the proposal endpoint unchanged; make the shared admission seam satisfy the contract.**

No route-level bypass is allowed. The passing test must succeed solely because
the proposal route's existing `proposal_evidence_context(...).admission`
becomes contract-aware.

- [ ] **Step 4: Re-run publication and engine regressions.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_proposal_review_api.py backend/tests/test_engine_commands_api.py`

Expected: all pass.

- [ ] **Step 5: Commit the API proof.**

```bash
git add backend/tests/test_proposal_review_api.py backend/tests/test_engine_commands_api.py
git commit -m "test: cover licensed Gildata evidence publication"
```

### Task 5: Run a new authorised live walkthrough and record the result

**Files:**
- Modify: `backend/scripts/walkthrough_cambricon_case.py`
- Modify: `backend/tests/test_walkthrough_support.py`
- Modify: `docs/superpowers/specs/2026-09-03-gildata-formal-evidence-design.md`

- [ ] **Step 1: Write a failing support test requiring rights configuration before a live walk.**

```python
def test_configured_gildata_rights_fail_closed_without_both_grants(monkeypatch):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.delenv("GILDATA_ALLOW_DISPLAY", raising=False)
    with pytest.raises(WalkthroughConfigurationError, match="GILDATA_ALLOW_DISPLAY"):
        configured_gildata_evidence_rights()
```

- [ ] **Step 2: Run it and verify the guard is absent.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py -k gildata_rights`

Expected: import failure for `configured_gildata_evidence_rights`.

- [ ] **Step 3: Add the fail-closed walkthrough guard and call it before API setup.**

The helper must require both deployment flags and expose no secret. The
walkthrough then performs P0–P12 under a new `WALKTHROUGH_RUN_ID`; its summary
must distinguish a provider 503 from an evidence-admission failure.

- [ ] **Step 4: Run focused tests, then the authenticated live walk.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py`

Expected: all pass.

Run: `WALKTHROUGH_RUN_ID=licensed-gildata-<UTC> backend/.venv/bin/python backend/scripts/walkthrough_cambricon_case.py`

Expected: P4.5 confirms at least one admissible proposal, and P6 reviews a
published EvidenceLink. If the LLM provider returns 503, report that external
failure explicitly and retain the completed Gildata admission checks.

- [ ] **Step 5: Run final regression and commit only code, tests, and design/plan documents.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_gildata_client.py backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py backend/tests/test_ingest_command_api.py backend/tests/test_proposal_review_api.py backend/tests/test_engine_commands_api.py backend/tests/test_walkthrough_support.py && git diff --check`

Expected: all selected tests pass and no whitespace errors.

```bash
git add backend/app/datasources/gildata/governance.py backend/app/services/source_admission.py backend/app/queries/review_queue.py backend/app/services/event_review_queue.py backend/app/scripts/ingest_real_data.py backend/app/scripts/walkthrough_support.py backend/scripts/walkthrough_cambricon_case.py backend/tests/test_gildata_client.py backend/tests/test_source_admission.py backend/tests/test_event_review_queue.py backend/tests/test_ingest_command_api.py backend/tests/test_proposal_review_api.py backend/tests/test_engine_commands_api.py backend/tests/test_walkthrough_support.py docs/superpowers/specs/2026-09-03-gildata-formal-evidence-design.md docs/superpowers/plans/2026-09-03-gildata-formal-evidence.md
git commit -m "feat: publish authorised Gildata evidence"
```
