# SSE Official Mirror Fallback Design

**Date:** 2026-08-13
**Status:** Approved in principle; awaiting written-spec review

## Problem

SSE announcement search returns canonical PDF paths under `www.sse.com.cn`. In the
current production network, those URLs redirect to `static.sse.com.cn`, which
returns an HTML response instead of the announced PDF. The transport correctly
rejects that response, so the live SSE acquisition smoke cannot finish.

The SSE-operated Traditional Chinese mirror exposes the same announcement path
under `big5.sse.com.cn/site/cht/www.sse.com.cn/...` and returns a valid PDF. The
adapter needs a narrow, auditable fallback without weakening MIME, byte, redirect,
or source-boundary checks.

## Decision

Keep the search result's `www.sse.com.cn` URL as the canonical source reference.
Fetch it first. Only when that attempt raises a non-retryable response-type
protocol error, or rejects an unsupported content encoding before MIME inspection,
may the SSE adapter request the corresponding official mirror URL.

The mirror URL is derived mechanically:

```text
https://www.sse.com.cn/<path>
  -> https://big5.sse.com.cn/site/cht/www.sse.com.cn/<path>
```

No provider-supplied fallback URL is accepted. Scheme, host, port, path, query,
fragment, user information, and traversal protections continue to be validated.
The mirror request must return `application/pdf`, start with `%PDF-`, remain under
the configured byte limit, and finish on the exact mirror URL.

## Policy Boundary

Add only `big5.sse.com.cn` to the exact host allowlist and to the SSE descriptor.
Do not add a suffix rule and do not accept sibling domains. Because this expands
network authority, bump the active source policy from `b-scope-v1` to
`b-scope-v2`. Persisted requests created under v1 remain v1 and cannot be silently
planned or replayed under v2.

## Fetch Flow

1. Validate that the reference was produced or restored by the SSE adapter.
2. Request its canonical `www.sse.com.cn` URL through the bounded transport.
3. If a valid PDF is returned, freeze it normally and do not call the mirror.
4. If the canonical request fails with the structured error type `response_type`
   or `content_encoding`, derive and request the mirror URL once. The latter is a
   rejection signal only: the adapter never decompresses or accepts those bytes.
5. Propagate all network, rate-limit, status, redirect, size, empty-body, and
   other protocol failures unchanged; they do not activate this fallback.
6. Record the actual mirror URL as `RetrievedEnvelope.final_url`; retain the
   canonical URL on the source reference so provenance contains both locations.
7. Cache and fetch-fence the successful bytes exactly as before.

## Error and Security Properties

- HTML is never accepted as PDF.
- A timeout or retryable provider outage does not trigger a second endpoint.
- A 4xx/5xx response does not trigger the mirror.
- Redirect problems and unexpected final URLs fail closed.
- Unsupported content encoding on the mirror fails normally and cannot trigger a
  second fallback. Encoded main-site bytes are never read, decompressed, or frozen.
- The mirror can only receive the same path already validated for the canonical
  SSE PDF; queries and fragments remain forbidden.
- If both official locations fail, the original acquisition job remains failed
  with sanitized diagnostics.

## Verification

Tests are written before production changes and must prove:

- policy v2 allows exactly the new official mirror and rejects lookalike hosts;
- normal canonical PDF retrieval never invokes the mirror;
- the exact HTML/content-type failure and unsupported main-site content encoding
  invoke the mechanically derived mirror;
- the returned envelope records the mirror final URL and valid PDF bytes;
- timeout, HTTP status, redirect, oversized body, empty body, and unrelated
  protocol errors do not invoke the mirror;
- restored references follow the same bounded behavior;
- the existing SSE, acquisition, and policy suites remain green;
- the live smoke searches SSE, fetches at least one real PDF, freezes its hash, and
  reports success without fixtures or credentials.

The generated live report remains committed as acceptance evidence.

## 2026-08-14 Acceptance Amendment

Live SSE verification showed that the official static CDN ignores
`Accept-Encoding: identity` for its bot-denial page and responds with
`Content-Encoding: gzip`. The bounded transport rejects this before MIME
inspection, so the original `response_type`-only trigger cannot reach the already
verified official mirror. The amendment above adds only the structured
`content_encoding` rejection as a trigger. It does not permit decompression,
broaden accepted response types, or turn network/status/redirect failures into
fallbacks.

## 2026-08-14 Execution-Attestation Amendment

Smoke report schema v3 separates the module CLI entrypoint from the public
in-process runner. Only `python -m app.scripts.smoke_acquisition_sources` can
record `cli_live` or `cli_dry_run`; every call through public `run()` records
`in_process_injected`, even when only a clock, Git resolver, or sleeper is
injected. Git commit and clean-worktree checks run with `git -C` against the
repository containing this script, so invoking the CLI from another directory
cannot attest to unrelated Git state. Existing v2 reports must be regenerated
through the documented CLI before they count as live acceptance evidence.
