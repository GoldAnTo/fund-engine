# Gildata And AI Extraction Hardening Design

## Goal

Make the two-round Gildata walkthrough and live AI extraction trustworthy, not
merely reachable. A successful run must preserve document/span identity,
remain idempotent when replayed, honour licensed-source rights, finish live
extraction within explicit bounds, and report the API's current candidate
contract without silently converting failures into zero-output success.

## Confirmed root causes

1. The extraction endpoint returns `candidate_count`, `candidates`, and
   `claim_type`, while the walkthrough still reads the retired
   `statement_count`, `statements`, and `kind` fields. The September 2 live
   response created three candidates, but the summary recorded zero.
2. The failed September 2 extraction calls all ended near the configured
   60-second deadline. The local deployment also overrides retries to one
   attempt. The current high-speed model answers a small health request and a
   previously failing 705-character extraction request, so the recorded 503s
   were provider deadline failures rather than a deterministic parser defect.
3. Legacy Gildata ingest freezes every source kind under one shared URI and
   lets `DocumentService` infer a predecessor from that URI. Unrelated reports,
   announcements, or news items are therefore linked as document revisions.
4. Reused documents receive a new `SourceSpan` on every ingest. Worse, a
   natural-key match can reuse an older document while attaching text from a
   different response body, breaking the span-to-document content invariant.
5. Quote ingest trusts the provider's first returned security code instead of
   checking it against the requested `quote_stock_code`.
6. Gildata token possession is implemented, but the deployment-owned
   downstream-use rights and immutable `SourceContract`/`ProviderRecord`
   required for formal evidence publication are not yet connected to this
   ingest path.

## Chosen approach

Harden the existing synchronous command path rather than replace it with the
larger governed acquisition worker in this change. The current path already
owns the walkthrough contract and has focused regression coverage. The repair
will reuse governance and admission services from the governed pipeline where
their invariants apply, while keeping the live walkthrough small and
resumable.

This change intentionally does not rewrite old append-only walkthrough data.
The September 2 artifacts remain evidence of the previous failure. Acceptance
uses a fresh run-scoped database and new audit files.

## Gildata document identity and persistence

Each provider result gets a stable internal URI with both source kind and exact
content digest:

```text
gildata://<source-kind>/<sha256>
```

The exact bytes are the identity for the legacy full-body documents used by
the walkthrough. Freeze calls disable automatic predecessor inference. A
successor relation may be created only when the provider exposes an explicit,
stable publication identity and the ingest code can prove that the new body is
a revision of that same publication; this walkthrough does not currently have
that proof, so it creates no inferred revision chains.

For research reports, announcements, and news, the full frozen body produces
exactly one full-body span. That span is inserted only when the document is
new. Replaying identical bytes reuses the existing document, case attachment,
contract, provider record, and span. Ingest must verify that the span text
digest equals the frozen document digest before returning success.

Macro slices may have multiple metric spans and therefore cannot use the
full-body equality invariant per span. They use deterministic locator and text
hashes, and replay reuses the existing span set without appending duplicates.

The summary distinguishes `*_created` and `*_reused` counts and reports the
number of spans actually created. Existing response field names remain
available where compatibility requires them, but their meaning is documented
and tested.

## Quote identity validation

The requested code is normalized to its bare numeric form. If Gildata returns
a non-empty security code whose normalized value differs, ingest raises a safe
provider-response error and rolls back the round. It must never create a Stock
or valuation snapshot for the wrong security. A missing returned code may use
the explicitly requested code, preserving the current fallback.

## Licensed-source governance

Two explicit deployment flags remain fail-closed:

```dotenv
GILDATA_ALLOW_AI_PROCESSING=true
GILDATA_ALLOW_DISPLAY=true
```

Only the literal value `true`, case-insensitive, grants each right. The Gildata
token is never interpreted as a downstream-use grant.

Every newly frozen or exactly reused Gildata document is reconciled with one
immutable `SourceContract` and one provider record through the existing source
governance service. Metadata includes the provider name, opaque provider record
identity, exact retrieval reference, request scope, content digest, and
contract version. Reuse is accepted only when the existing immutable contract
is compatible with the current declaration.

Source admission gains one narrow branch for a verified `gildata://` document
whose parser, provider identity, active contract, AI right, and display right
all match. Generic non-HTTP schemes and uncontracted or restricted Gildata
references remain inadmissible. Proposal review continues to use the shared
admission result; there is no route-level bypass.

## AI provider resilience

`LLMClient` keeps SDK retries disabled so there is only one retry authority.
The application client performs bounded attempts for transient transport,
timeout, 408, 429, and 5xx provider failures. Authentication, permission,
request-shape, SDK response-validation, explicit refusal, and content-filter
failures are terminal. Malformed responses are also terminal by default.

`StatementExtractor` is the sole narrow exception: it explicitly opts into the
versioned `extract-json-retry-v2` protocol correction when the client parser
cannot obtain a complete JSON object or the required top-level `statements`
list. The fixed correction message asks for a fresh compact response under the
original extraction contract, caps the retry at three statements, and repeats
the quote and normalized-text character ceilings. It never contains the
previous response, exception text, or source-derived content. When a trusted
system message already exists, the correction is appended to that message so
the provider receives one unambiguous system contract; the caller's messages
remain unmodified. A later transport retry retains that one correction.
The extractor independently enforces the three-statement retry cap before
persistence, so prompt compliance is never the sole control.
Transport and malformed-response retries consume the same `LLM_MAX_ATTEMPTS`
budget and therefore cannot multiply into nested retries.

Retries use bounded exponential backoff with injectable sleep for deterministic
tests. A valid `Retry-After` value is honoured within the configured ceiling.
The public and persisted error stays secret-free. A terminal failure records
attempt count and failure category; a success records total LLM attempts and
malformed-response retry count on its one `AIRun`.

The existing `LLM_MAX_OUTPUT_TOKENS` setting is parsed as a finite positive
integer and forwarded as the current `max_completion_tokens` parameter to the
OpenAI-compatible endpoint. The tested walkthrough floor is 4096 tokens. This
matters for MiniMax M2.x because its non-disableable reasoning and final JSON
share the completion budget; the previous 2048-token floor produced explicit
`finish_reason=length` failures on 968–2416-character spans. The 4096-token
ceiling plus the compact protocol completed the longest reproduced input in one
attempt. The ceiling still bounds latency and cost, and extraction keeps a
finite per-attempt timeout. MiniMax documents both the M2.x thinking behavior
and `max_completion_tokens` semantics in its
[OpenAI-compatible API schema](https://platform.minimaxi.com/docs/api-reference/text/api/openapi-chat-openai.json).
Because the client uses `max_completion_tokens` unconditionally, the declared
OpenAI SDK dependency floor is 1.45.0.

No retry is used to paper over invalid individual candidates. The versioned
`extract-v5` prompt asks for at most five high-value statements per span and
receives a trusted `max_statements` equal to
`min(20, narrative_span_count * 5)`. Thus the one-span Gildata path remains
bounded at five while multi-span PDF and macro extraction retain up to twenty
items. The prompt also limits verbatim quotes to 120 Unicode characters and
normalized text to 80 Unicode characters. The extractor enforces the dynamic
initial limit, the three-item malformed-retry limit, and both field ceilings,
counting every overflow or oversized item as rejected. Candidate validation
remains per-item.
An already exact offset is retained even when the quote repeats. A wrong offset
is repaired only when the non-blank verbatim quote occurs exactly once in its
declared span; missing, blank, cross-span, or ambiguous quotes are rejected
while valid siblings are kept. The audit summary records returned, accepted,
rejected, deterministically repaired-offset, total-attempt, and malformed-retry
counts. The acceptance parser requires the retry diagnostics on `extract-v4`
and `extract-v5` but continues to recognize the earlier v1–v3 summary formats.

The response envelope is normalized before content parsing. Provider
`content_filter`, explicit refusal, and MiniMax `input_sensitive` /
`output_sensitive` flags are terminal refusals. MiniMax's top-level
`base_resp.status_code` is accepted in both SDK mapping and object forms.
Documented retryable timeout, rate-limit, and internal/system codes consume the
same bounded attempt budget as transport failures; sensitive-content codes are
terminal refusals and all other non-zero codes are terminal provider-envelope
errors, following MiniMax's
[error-code table](https://platform.minimaxi.com/docs/api-reference/errorcode).
Missing or type-confused safety, refusal, base-status, and call-payload
fields fail closed without exposing their values. Only
`finish_reason=stop` may reach the JSON parser; missing, tool-call, and unknown
finish reasons, plus call payloads attached to a stop response, fail closed as
response-validation errors. `finish_reason=length` remains eligible for the
extraction-only compact retry but is persisted as the distinct safe
`output_limit` category if the shared attempt budget is exhausted. A
`<think>...</think>` wrapper is stripped only when anchored at the start of the
response, so literal closing tags inside valid JSON strings are preserved.

## Walkthrough contract and recovery

The P3 collector consumes the current extraction response contract:

- `candidate_count`
- `candidates`
- `claim_type`
- `reason`

It verifies `candidate_count == len(candidates)` before counting a document as
successful. An HTTP 201 with zero candidates is recorded as an honest empty
result with its reason; it is not confused with a field mismatch.

P3 runs all eligible documents in bounded batches. Failed documents remain
`failed` and can be resumed with the same run ID without repeating successful
or `extracted_empty` documents. Summary counts are derived from the final API
state and cross-checked against `AIRun` and candidate rows, rather than using
subtraction that assumes every response has the expected shape.

The CLI constructs credential-dependent clients only after argument parsing so
`--help` remains usable. Live startup validates tenant credentials, Gildata
rights, LLM retry settings, timeout, and output-token budget without printing
their values.

## Failure and transaction semantics

- Any Gildata transport, protocol, identity, contract, or content-integrity
  failure rolls back that ingest request and returns the existing safe 503
  boundary where appropriate.
- A transient LLM failure writes exactly one failed `AIRun` for the logical
  extraction operation, including the total attempt count, then returns 503.
- Successful retries create one successful `AIRun`, not one row per transport
  attempt.
- No partial candidate set survives a terminal extraction failure.
- Secrets, provider URLs containing credentials, licensed bodies, and raw
  upstream errors are excluded from summaries and committed artifacts.

## Test strategy

Implementation follows red-green-refactor in these slices:

1. Reproduce the walkthrough response-field drift with a contract-level test.
2. Reproduce duplicate spans, content/natural-key collision, and false
   supersession with ingest tests.
3. Reproduce quote-code mismatch and prove transaction rollback.
4. Add fail-closed rights parsing, immutable contract/provider-record creation,
   and contract-aware admission tests.
5. Add LLM output-budget, retry classification, `Retry-After`, shared-budget
   backoff, extraction-only malformed correction, refusal, and sanitized
   diagnostics tests.
6. Add resumable P3 aggregation tests, including success-with-candidates,
   honest empty success, transient failure, and final database reconciliation.
7. Run focused suites, the complete backend suite, and `git diff --check`.

## Live acceptance

A fresh run-scoped database must demonstrate all of the following:

1. Both configured Gildata rounds return 201 and the requested/returned quote
   identities match.
2. Replaying both rounds creates no new documents, spans, contracts, provider
   records, case attachments, or valuation snapshots.
3. No unrelated Gildata documents have a `supersedes_id`, every full-body span
   matches its document digest, and every Gildata document has one compatible
   active contract and provider record.
4. Every eligible non-degenerate document finishes in `extracted` or
   `extracted_empty`; none remains `failed` or `not_attempted`.
5. The P3 summary candidate totals equal both the HTTP responses and persisted
   candidate rows. At least one real LLM-generated candidate is present.
6. Atomic-claim review publishes source statements, proposal review publishes
   at least one formally admissible Gildata evidence link, and downstream
   assessment receives non-empty evidence.
7. The summary contains no unresolved issue and no credential or licensed raw
   content.
