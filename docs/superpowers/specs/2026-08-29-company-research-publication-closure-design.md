# Company Research Publication Closure Design

## 1. Objective

Complete the existing Company Research workflow after the durable
`awaiting_judgment_review` boundary. The first live Alphabet project must move
through explicit human judgment confirmation, zero-write publication preview,
idempotent immutable publication, frozen revision replay, and deterministic
Markdown export.

The current Alphabet result is honestly `not_answerable`. Publication must
freeze that conclusion and its 31 explicit gaps without inventing direction,
confidence, target price, value, or expected return.

## 2. Current Boundary

The implemented preparation pipeline currently ends at:

```text
awaiting_judgment_review / judgment_context / 85
```

It already persists and authenticates reviewed evidence, research gaps,
business and driver maps, a financial bridge, scenarios, an optional valuation,
judgment context, and a machine-draft memo. Generic product publication rejects
Company Research projects by design. The Company Research API and UI do not yet
offer judgment confirmation, publication, replay, or export.

The existing rejection in the generic `RevisionPublisher` remains in place.
Company Research receives a dedicated high-level publication boundary.

## 3. Scope

### In scope

- Explicitly confirm the current machine judgment and memo.
- Advance preparation from 85% to 95% with an immutable human-confirmed memo.
- Produce a zero-write publication preview from authenticated current heads.
- Atomically freeze an immutable Company Research revision.
- Advance preparation from 95% to 100% only after the revision is durable.
- Read and replay a selected frozen revision without consulting current heads.
- Export a selected frozen revision as deterministic Markdown with SHA-256.
- Add the corresponding real HTTP client and workbench UI controls.
- Run the existing live Alphabet project through the completed workflow.

### Out of scope

- Filling the 31 current research gaps.
- Producing an answerable valuation or investment direction for Alphabet.
- A second-cutoff successor run and its ChangeSet UI.
- CATL or another company adapter.
- Relaxing the generic product publication guard.
- Authentication or multi-user reviewer identity. The current local product
  records the closed reviewer label `human:local-user`.

## 4. Lifecycle

The new lifecycle is:

```text
awaiting_judgment_review / judgment_context / 85
  -> explicit judgment confirmation
ready_to_freeze / memo / 95
  -> zero-write preview
  -> explicit immutable publication
completed / null / 100
```

Judgment confirmation and publication are separate operations. A preview never
changes status. A failed confirmation leaves the project at 85%. A failed
publication leaves it at 95%.

## 5. Domain and Persistence

### 5.1 Human-confirmed memo

The current `memo` artifact is a structured `machine_draft`. Confirmation
appends exactly one successor `memo` artifact rather than rewriting the machine
draft. Its closed payload contains:

```text
candidate_status = human_confirmed
reviewer = human:local-user
markdown = normalized non-empty Markdown
assessment_status
business_map_ref
driver_map_ref
financial_bridge_ref
scenario_set_ref
valuation_set_ref
gap_keys
strongest_counterevidence
next_verification_events
```

Line endings are normalized to LF, surrounding whitespace is removed, and the
text is limited to 100,000 characters. The successor repeats the exact typed
references and assessment from the authenticated machine memo. The browser
cannot submit or replace artifact references.

The confirmation request carries the current draft lock version, the expected
machine memo artifact ID, and the expected machine memo content hash. The
service locks the project, preparation, draft, current artifact heads, and audit
events in the repository's canonical order. It revalidates the complete source,
review, model, historical-basis, and audit histories before appending anything.

Confirmation appends a `judgment_confirmed` audit event whose payload contains
the machine memo ID/hash, confirmed memo ID/hash, assessment status, and
reviewer. The memo append, event append, draft CAS, and preparation transition
run in one savepoint.

### 5.2 Frozen revision

`CompanyResearchPublicationService` reuses the existing immutable research
revision, boundary, and manifest persistence primitives while owning
Company-Research-specific validation and projection. It freezes exactly one
authenticated current artifact for every required kind:

```text
evidence_index
research_gaps
business_map
driver_map
financial_bridge
scenario_set
judgment_context
memo (human_confirmed)
valuation_set (only when present and governed)
```

The revision manifest includes project, Company, Securities, cutoff, historical
basis, draft lock version, exact artifact IDs/versions/content hashes, model and
strategy versions, assessment, blockers, strongest counterevidence, and next
verification events. It contains no current-head lookup instructions.

Publication appends a `company_research_published` audit event and advances the
preparation to `completed / null / 100` inside the same outer savepoint that
creates the boundary, manifest, revision, and all revision-artifact links.

An `Idempotency-Key` is mandatory. The same key and same manifest returns the
same revision. Reusing the key for different content fails closed. A concurrent
different key can produce at most one first revision.

## 6. Public API

All endpoints live below:

```text
/api/underwriting/v1/product/company-research/projects/{project_id}
```

### 6.1 Confirm judgment

```text
POST /judgment-confirmations
```

Request:

```json
{
  "schema_version": "underwriting.v1",
  "expected_lock_version": 3,
  "expected_memo_id": "uuid",
  "expected_memo_content_hash": "sha256",
  "markdown": "Current formal evidence is insufficient..."
}
```

Success returns the updated preparation, draft lock version, and confirmed memo
identity. Repeating the exact confirmation is idempotent; a stale or different
confirmation returns conflict.

### 6.2 Publication preview

```text
POST /publication-preview
```

Request carries `expected_lock_version`. Response includes Company, Securities,
cutoff, answerability, optional direction/confidence/value fields, blockers,
strongest counterevidence, next checks, artifact summary, and `manifest_hash`.
For this Alphabet case the optional investment fields are null and answerability
is `not_answerable`.

The operation is read-only: it does not flush, commit, rollback, append events,
or change preparation state.

### 6.3 Publish

```text
POST /publish
Idempotency-Key: <non-empty key>
```

Request carries `expected_lock_version` and the exact `manifest_hash` returned
by preview. Success returns HTTP 201 with the frozen revision summary. A
response-loss replay with the same key returns the same revision.

### 6.4 Revision replay

```text
GET /revisions/{revision_id}
```

The response is assembled only from frozen revision records and their frozen
artifact links. Later workspace heads must not change it.

### 6.5 Export

```text
GET /revisions/{revision_id}/export
```

Response:

```json
{
  "schema_version": "underwriting.v1",
  "filename": "alphabet-company-research-<revision-id>.md",
  "media_type": "text/markdown",
  "content": "...",
  "content_hash": "sha256"
}
```

The Markdown includes revision ID, cutoff, Company and Securities, historical
basis and strategy/model versions, assessment, memo, exact facts with source
locators, gaps, assumptions, counterevidence, and next checks. Serialization is
deterministic. The server computes SHA-256 over UTF-8 content; the browser
recomputes it before enabling download.

## 7. UI

At 85%, the workbench overview displays the machine assessment, blockers,
counterevidence, next checks, an editable Markdown memo initialized from the
honest machine result, and `确认当前判断`.

At 95%, it displays `预览冻结版本`. The confirmation dialog names Alphabet,
GOOG and GOOGL, the cutoff, `not_answerable`, the absence of value/return
ranges, strongest counterevidence, and the fact that the revision is immutable.
The final action is `冻结并发布`.

At 100%, the workbench displays the frozen revision ID, publication time,
selected frozen version, replayed nine-module content, and `导出 Markdown`.
The live page never calls low-level draft or generic publisher endpoints.

All mutation buttons are disabled while a request is in flight. Stale responses
cannot overwrite newer workspace state. A 409 conflict refreshes the workspace
and explains that the user must review the latest judgment. Validation and
integrity failures remain bounded and do not expose internal provider output.

## 8. Invariants

The completed live flow must preserve the pre-confirmation values of:

- evidence head ID, version, input hash, content hash, source refs, and payload;
- research-gaps root ID, version, input hash, content hash, source refs, and
  payload;
- the ordered seven evidence decisions: six confirmed and one rejected;
- historical basis ID and its authenticated cutoff/source/definition/parser
  hashes;
- Company and Security identities;
- all existing machine model artifacts.

Only the human-confirmed memo successor, publication records, audit events,
preparation projection, and expected draft lock increments may be new.

## 9. Failure Semantics

- Stale draft or memo expectations: conflict, zero writes.
- Missing, foreign, substituted, malformed, or tampered artifacts/events:
  validation or integrity failure, zero writes.
- Preview manifest mismatch: validation failure, zero writes.
- Duplicate idempotency key with different content: conflict, zero writes.
- Exception at any publication persistence stage: complete savepoint rollback.
- Export hash or frozen-link mismatch: integrity failure; never fall back to
  current heads.

## 10. Verification

The implementation is accepted only when all of the following pass:

1. Domain and service RED-to-GREEN tests for confirmation, preview, publication,
   replay, export, idempotency, concurrency, and rollback.
2. HTTP tests for all five high-level endpoints and their closed DTOs.
3. UI tests for the 85%, 95%, and 100% states, stale responses, conflicts, and
   client-side export hash verification.
4. Existing Company Research, revision, draft, boundary, runtime, frontend
   decoder, typecheck, and production-build suites.
5. A live run of project `19a046e8-2f48-4e1e-949d-cdd84a66bb5e` from 85% through
   completed 100%, followed by restart, frozen replay, deterministic export,
   and exact invariant comparison.

