# Live Provider Enforcement Design

## Goal

Actual application runs must use the configured external LLM and Gildata
credentials. Missing credentials or upstream failures must stop the operation
and be recorded as failures; they must never produce mock research output or a
simulated successful completion.

Automated tests remain isolated from external services and may use explicit
test doubles under `APP_ENV=test`.

## Runtime modes

`APP_ENV` defines a closed provider policy:

- `test`: deterministic LLM mock behavior remains available to the automated
  test suite. Local `.env` files are not loaded.
- every non-test value, including unset, `development`, and `production`: an
  external LLM is required. `LLM_API_KEY` must be present or client creation
  fails before any research output is generated.

Gildata already follows this policy: `GILDATA_TOKEN` is required whenever the
client is constructed, with no mock fallback. This behavior remains unchanged.

The developer's gitignored `backend/.env` will identify a live runtime and
continue to hold `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, and
`GILDATA_TOKEN`. Exported process variables keep precedence over the file.

## Entry points and data flow

FastAPI, the AI Engine CLI, and the automatic-research Worker load
`backend/.env` before constructing provider-dependent services. The Worker
ordering is already protected by a subprocess regression test.

On an automatic-research job:

1. The Worker claims and commits the queued job.
2. `LLMClient.from_env()` constructs an OpenAI-compatible external client.
3. Provider calls happen outside long-lived database locks.
4. Validated model output is persisted with the configured model version in
   the immutable AI audit record.
5. Provider/configuration exceptions mark the job and run failed; they do not
   invoke `_mock_response` and do not publish a successful terminal state.

Gildata ingestion constructs `GildataMCPClient.from_env()`. Missing tokens,
transport failures, protocol failures, and application-level failures remain
explicit errors and do not create provider snapshots from fabricated data.

## Configuration

The local, gitignored `backend/.env` must contain:

```dotenv
APP_ENV=production
LLM_API_KEY=<secret>
LLM_BASE_URL=<OpenAI-compatible endpoint>
LLM_MODEL=<provider model identifier>
LLM_TEMPERATURE=0
LLM_SEED=<optional integer>
GILDATA_TOKEN=<secret>
```

No credential value may be printed, committed, included in an exception, or
stored in a test artifact. The Gildata URL carries the token as a query
parameter, so validation output must report only success/failure and tool/result
counts, never request URLs.

## Failure behavior

- Missing LLM credentials outside tests: fail during client construction.
- Invalid LLM credentials, endpoint, model, timeout, or malformed response:
  propagate the provider failure through the existing audited failure path.
- Missing or invalid Gildata token and MCP failures: return/record an upstream
  failure through the existing ingestion path.
- No live-runtime error is allowed to retry through deterministic mock logic.
- Previously persisted mock audit rows remain historical records; this change
  does not rewrite immutable history.

## Verification

Automated verification will cover:

1. `APP_ENV=test` without a key constructs the deterministic mock client.
2. unset/development/production runtime without a key fails closed.
3. a configured non-test runtime constructs an external client.
4. the Worker continues to load `.env` before database/provider imports.
5. relevant and full backend suites pass without contacting external services.

Controlled live verification will then:

1. construct the Worker runtime from `backend/.env` and confirm the model
   version is not prefixed by `mock-`;
2. send a minimal, non-sensitive JSON request to the configured LLM and verify
   a parsed JSON response;
3. call Gildata `tools/list`, reporting only the number and names of tools;
4. issue one minimal non-sensitive Gildata financial-data query and validate
   the response envelope without printing licensed payload content;
5. run one isolated-database research job with the live LLM and assert its
   audit model version is the configured external model and the run did not
   succeed through mock output.

Live verification may consume a small amount of LLM and Gildata quota. It does
not write to the normal application database.

## Non-goals

- Removing deterministic mocks from the automated test suite.
- Committing credentials or changing secret-management infrastructure.
- Adding a second financial-data Provider.
- Claiming that Gildata is automatically queried for every research evidence
  gap; generalized automatic source acquisition remains a separate feature.
