# SSE Official Mirror Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete live SSE announcement acquisition by falling back from an invalid main-site response to the same PDF path on SSE's exact official Traditional Chinese mirror, without weakening source or PDF validation.

**Architecture:** The canonical `www.sse.com.cn` reference remains immutable and is always attempted first. The SSE adapter alone recognizes the transport's structured response-type or unsupported-content-encoding rejection, derives one exact `big5.sse.com.cn/site/cht/www.sse.com.cn/...` URL, and sends it through the existing bounded transport. Encoded main-site bytes are never read or decompressed. Expanding the exact host boundary upgrades the frozen source policy to `b-scope-v2`.

**Tech Stack:** Python 3.11, httpx, pytest, existing governed acquisition adapters and smoke CLI.

---

### Task 1: Version the expanded source boundary

**Files:**
- Modify: `backend/app/domain/acquisition.py`
- Modify: `backend/app/acquisition/policy.py`
- Modify: `backend/tests/test_acquisition_policy.py`
- Modify: `backend/tests/test_sse_source.py`

- [ ] **Step 1: Write failing policy and descriptor tests**

Update the active-version assertion and exact-host cases:

```python
def test_b_scope_policy_version_tracks_exact_network_authority():
    assert B_SCOPE_POLICY.version == "b-scope-v2"

@pytest.mark.parametrize(
    "host",
    [
        "query.sse.com.cn",
        "www.sse.com.cn",
        "static.sse.com.cn",
        "big5.sse.com.cn",
        "www.szse.cn",
        "disc.static.szse.cn",
    ],
)
def test_b_scope_policy_allows_only_declared_exchange_hosts(host: str):
    assert B_SCOPE_POLICY.allows_host(host)
```

Add `evil.big5.sse.com.cn`, `notbig5.sse.com.cn`, and
`big5.sse.com.cn.evil.test` to the denied-host table. Update the SSE descriptor
test and its test transport to include only the new exact host, never a suffix.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_acquisition_policy.py tests/test_sse_source.py::test_descriptor_and_contract_are_exact_and_do_not_widen_policy -q
```

Expected: failures because the policy is still `b-scope-v1` and does not allow
`big5.sse.com.cn`.

- [ ] **Step 3: Apply the minimal policy change**

Change:

```python
B_SCOPE_POLICY_VERSION: Final = "b-scope-v2"
```

Add `big5.sse.com.cn` to `B_SCOPE_POLICY.exact_hosts` and the SSE
`ExchangeSourceDescriptor.allowed_hosts`. Leave `suffix_hosts` empty.

- [ ] **Step 4: Run the policy/contract tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_acquisition_contract.py tests/test_acquisition_policy.py tests/test_sse_source.py::test_descriptor_and_contract_are_exact_and_do_not_widen_policy -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the boundary change**

```bash
git add backend/app/domain/acquisition.py backend/app/acquisition/policy.py backend/app/datasources/exchanges/sse.py backend/tests/test_acquisition_policy.py backend/tests/test_sse_source.py
git commit -m "feat: authorize exact SSE official mirror"
```

### Task 2: Classify response-type failures structurally

**Files:**
- Modify: `backend/app/datasources/exchanges/http.py`
- Modify: `backend/tests/test_exchange_http.py`

- [ ] **Step 1: Write a failing diagnostic classification test**

Extend the existing PDF MIME/body rejection test to capture the exception:

```python
with pytest.raises(SourceProtocolError, match="unsupported exchange response type") as captured:
    transport.request("GET", canonical, expected="pdf")

assert captured.value.retryable is False
assert captured.value.diagnostics == {"error_type": "response_type"}
```

Verify that diagnostics contain no URL, headers, or response bytes.

- [ ] **Step 2: Run the test and verify RED**

Run the exact edited test from `tests/test_exchange_http.py` with `-q`.

Expected: the message assertion passes but diagnostics are currently empty.

- [ ] **Step 3: Add the minimal structured diagnostic**

For every rejection branch in `_validate_response_type`, raise:

```python
raise SourceProtocolError(
    "unsupported exchange response type",
    diagnostics={"error_type": "response_type"},
) from None
```

Do not classify status, redirect, encoding, size, empty-body, or JSON-schema
failures as `response_type`; the acceptance amendment later gives unsupported
content encoding its own `content_encoding` classification.

- [ ] **Step 4: Run HTTP transport tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_exchange_http.py -q
```

Expected: all transport tests pass.

- [ ] **Step 5: Commit the classification**

```bash
git add backend/app/datasources/exchanges/http.py backend/tests/test_exchange_http.py
git commit -m "fix: classify exchange response type failures"
```

### Task 3: Implement the single bounded mirror fallback

**Files:**
- Modify: `backend/app/datasources/exchanges/sse.py`
- Modify: `backend/tests/test_sse_source.py`

- [ ] **Step 1: Write failing fallback behavior tests**

Add tests proving these behaviors with `httpx.MockTransport`:

```python
def test_fetch_falls_back_to_exact_official_mirror_after_invalid_main_response():
    # query returns a canonical www reference
    # www redirects to static, static returns text/html, mirror returns a valid PDF
    # assert request hosts are www -> static -> big5
    # assert mirror path is /site/cht/www.sse.com.cn + canonical path
    # assert envelope.final_url is the exact mirror URL
    # assert envelope bytes are the mirror PDF

def test_fetch_does_not_use_mirror_when_canonical_pdf_is_valid():
    # assert exactly one PDF request to the canonical URL

@pytest.mark.parametrize("failure", ["timeout", "status", "redirect", "size"])
def test_fetch_does_not_use_mirror_for_non_response_type_failures(failure: str):
    # construct each transport failure and assert no request reaches big5.sse.com.cn
```

Also test that a restored persisted canonical reference uses the same fallback and
that a mirror redirect/final-path change is rejected.

- [ ] **Step 2: Run the new tests and verify RED**

Run each newly added test by node id.

Expected: the response-type case raises `SourceProtocolError` without requesting
the mirror; the safety cases establish the pre-change behavior.

- [ ] **Step 3: Implement URL derivation and fallback**

Add constants and a private derivation helper:

```python
_PDF_MIRROR_ORIGIN: Final = "https://big5.sse.com.cn"
_PDF_MIRROR_PREFIX: Final = "/site/cht/www.sse.com.cn"

@staticmethod
def _official_mirror_url(canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    return f"{_PDF_MIRROR_ORIGIN}{_PDF_MIRROR_PREFIX}{parsed.path}"
```

In `fetch`, attempt the canonical request first. Catch only
`SourceProtocolError` where
`exc.diagnostics.get("error_type")` is one of the approved trigger values
`response_type` or `content_encoding`, then request the derived mirror once.
Accept the mirror response only when `response.final_url` equals the derived URL
exactly. Keep the existing accepted canonical/static final-URL check for a
successful primary request. A mirror-side encoding failure propagates and cannot
trigger another fallback.

- [ ] **Step 4: Run SSE adapter tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_sse_source.py -q
```

Expected: all SSE tests pass.

- [ ] **Step 5: Run acquisition recovery and runner regressions**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_acquisition_runner.py tests/test_acquisition_worker_recovery.py tests/test_acquisition_source_smoke_script.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the adapter fallback**

```bash
git add backend/app/datasources/exchanges/sse.py backend/tests/test_sse_source.py
git commit -m "fix: fetch SSE PDFs from official mirror"
```

### Task 4: Record live acceptance evidence

**Files:**
- Modify: `docs/operations/acquisition-sources.md`
- Modify: `docs/evaluation/reports/acquisition-sse.json`
- Modify if regenerated: `docs/evaluation/reports/acquisition-szse.json`

- [ ] **Step 0: Classify and handle the observed encoded denial response**

Write failing transport and SSE tests proving that an unsupported main-site
content encoding exposes only `{"error_type": "content_encoding"}` and invokes
the exact mirror once without consuming or decompressing the body. Prove that the
same error from the mirror propagates without another request, while timeout,
network, status, redirect, size, and empty-body failures still do not invoke the
mirror. Implement the minimal structured classification and add
`content_encoding` to the SSE adapter's two-value fallback trigger set. Run the
exchange/SSE suites before the live commands below.

- [ ] **Step 1: Document the exact fallback contract**

State that SSE retains the main-site canonical URL, uses the exact official
Traditional Chinese mirror only after a structured response-type or unsupported
content-encoding rejection, records the actual final URL hash, never decompresses
the rejected response, and never relaxes PDF validation. Record policy
`b-scope-v2` and the validation date.

- [ ] **Step 2: Run the real SSE smoke**

Run from `backend/`:

```bash
.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source sse --security-code 688256 \
  --start 2025-04-19 --end 2025-04-20 \
  --output ../docs/evaluation/reports/acquisition-sse.json
```

Expected: exit 0, `status=succeeded`, `live_success=true`, at least one accepted
reference and at least one fetched PDF with non-zero byte size and SHA-256.

- [ ] **Step 3: Re-run the real SZSE control smoke**

Run from `backend/`:

```bash
.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source szse --security-code 000001 \
  --start 2025-04-18 --end 2025-04-20 \
  --output ../docs/evaluation/reports/acquisition-szse.json
```

Expected: exit 0, `status=succeeded`, `live_success=true`, and fetched PDFs.

- [ ] **Step 4: Validate reports contain no unsafe raw data**

Run smoke-script tests and inspect that reports contain hashes/counts but no raw
URLs, titles, headers, cookies, tokens, or document bytes.

- [ ] **Step 5: Commit the live evidence**

```bash
git add docs/operations/acquisition-sources.md docs/evaluation/reports/acquisition-sse.json docs/evaluation/reports/acquisition-szse.json
git commit -m "test: verify live SSE official mirror acquisition"
```

### Task 5: Complete regression and review

**Files:**
- Verify: `backend/`
- Verify: worktree diff from `4d3764e`

- [ ] **Step 1: Run the complete acquisition slice**

```bash
cd backend
.venv/bin/pytest tests/test_acquisition_contract.py tests/test_acquisition_policy.py tests/test_exchange_http.py tests/test_sse_source.py tests/test_szse_source.py tests/test_acquisition_runner.py tests/test_acquisition_worker_recovery.py tests/test_acquisition_source_smoke_script.py -q
```

Expected: all pass, except only previously documented optional skips.

- [ ] **Step 2: Run the full backend suite**

```bash
cd backend
.venv/bin/pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Review the final diff**

Check that the diff contains no direct database shortcuts, fixture-based live
success, generic scraping, suffix-host expansion, secret logging, or modifications
to `AutoResearchService`.

- [ ] **Step 4: Commit any review-only corrections and confirm clean status**

Run `git diff --check`, `git status --short`, and `git log --oneline -8`.
Expected: no whitespace errors and no uncommitted files.
