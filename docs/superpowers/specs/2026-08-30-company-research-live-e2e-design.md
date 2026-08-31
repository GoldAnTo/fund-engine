# Company Research Live Full-Flow E2E Design

**Date:** 2026-08-30

**Status:** Approved for implementation

## Context

The Company Research product already has a complete public HTTP workflow and a
browser workbench for initialization, evidence review, model preparation,
judgment confirmation, publication, frozen-revision replay, and Markdown
export. The backend also has a governed, bounded Alphabet fixture and a real
recoverable worker. Existing browser E2E coverage, however, runs the frontend
in mock mode. Backend integration tests and mocked browser tests therefore do
not prove that the normal browser, Vite proxy, bearer authentication, FastAPI,
database, and worker operate together.

This design adds one deterministic live-browser acceptance path for that
missing boundary. "Live" means that the normal frontend HTTP adapter talks to
a real API, database, and Company Research worker. It does not mean reaching
external providers or production infrastructure.

## Goals

- Prove the normal browser can create an Alphabet Company Research project from
  an empty, isolated database and drive it to a frozen revision.
- Exercise the real API, persistence layer, worker orchestration, governed
  Alphabet source/model fixture, Vite proxy, and bearer authentication.
- Perform every human workflow write through visible browser controls.
- Prove frozen-revision replay and Markdown export remain bound to the
  published revision.
- Keep the acceptance path deterministic, secret-free, isolated, bounded, and
  safe to run locally or in CI.

## Non-goals

- Calling external data vendors, a real LLM, or the public internet.
- Replacing the existing backend service/API tests or mock-mode Playwright
  suite.
- Proving an `answerable` result. The current authenticated Alphabet fixture is
  accepted according to its real answerability result; a second answerable
  golden case is a separate project.
- Exercising production Docker volumes or production credentials.
- Seeding a prepared project, generated artifact, review decision, or frozen
  revision before the browser flow begins.

## Chosen Approach

Add a standalone Node verifier, exposed through the package script
`npm run verify:live-company-research`. The verifier owns the full lifecycle
of one run:

1. Create a private temporary directory and an isolated SQLite database.
2. Create the schema and install only the supported Alphabet product
   foundation.
3. Start a real FastAPI server.
4. Start the real Company Research worker in polling mode.
5. Start Vite with mock mode disabled and the bearer token held by the proxy.
6. Start Playwright and execute the browser workflow.
7. Stop every child process and safely remove the temporary directory on both
   success and failure.

The new verifier remains separate from the existing mock Playwright project.
This avoids shared ports, shared databases, mock-client leakage, and lifecycle
coupling. It should reuse the proven process, free-port, readiness, bounded-log,
and proxy-authentication patterns from the existing live event UI verifier
where doing so preserves the closed Company Research semantics.

Alternatives rejected:

- A multi-`webServer` Playwright project makes ownership of schema setup,
  workers, ports, and cleanup less explicit and increases collision risk.
- A backend integration test combined with a mocked browser test still leaves
  the real browser-to-worker boundary unproved.
- External-provider acceptance would be secret-dependent, nondeterministic,
  slower, and outside the contract this test needs to establish.

## Runtime Topology and Boundaries

The verifier starts all components on loopback-only dynamically allocated
ports. Each component receives a deliberately constructed environment. The API
uses the temporary SQLite database. Vite proxies browser `/api/...` requests to
that API and injects the verifier bearer token without exposing it to browser
JavaScript.

The only setup write permitted outside the browser is installation of the
supported Alphabet identity and security foundation required for company
search and initialization. The test must not pre-create a research project,
preparation, job, artifact, review, publication event, or revision. Once setup
is complete:

- initialization is triggered from the browser;
- source and model stages are executed by the real worker;
- evidence decisions, judgment confirmation, and publication are triggered
  through browser controls;
- revision replay and export use the product's public HTTP routes.

The worker consumes the repository's authenticated Alphabet fixture. No
network interception may fabricate Company Research API responses, and the
frontend must run with `VITE_RESEARCH_CLIENT` unset or empty.

## Browser Workflow and Assertions

### 1. Create the research project

The browser opens the normal new-research flow, searches for and selects
Alphabet Inc., requests a preview, and verifies the company identity plus the
GOOG and GOOGL security set. It submits initialization once and follows the
application navigation to the returned project workbench. Captured API traffic
must show exactly one successful initialization for the browser action.

### 2. Wait for source preparation

The worker claims and executes the source stage. The browser polls the
workspace until the server reports `awaiting_evidence_review` at 25 percent.
The workbench must expose the evidence index, research gaps, and the governed
set of pending facts. Counts should be derived from the returned authenticated
fixture contract rather than duplicated as an unrelated magic number in the
browser script.

### 3. Review evidence

The verifier confirms every pending fact through its visible "confirm fact"
button. This acceptance path does not manufacture a rejection branch. After
each write it waits for an authenticated successor workspace and proves:

- the evidence artifact version advances by exactly one;
- the reviewed-fact count advances by exactly one;
- project identity, fact identity, and all previously recorded decisions stay
  stable;
- no duplicate decision is created.

After the final decision, the server must transition to `building_model`. The
test must not invoke the model builder or enqueue a model job directly.

### 4. Wait for model preparation

The worker claims and executes the model stage. The browser polls until the
workspace reaches `awaiting_judgment_review` at 85 percent. It then proves the
expected governed artifact set is present, including the business map, driver
map, financial bridge, scenario set, judgment context, and machine memo.

The assertion follows the fixture's actual answerability contract. For the
current Alphabet case, a `not_answerable` result must remain closed: direction,
confidence, target value, and expected return may not be inferred or fabricated.

### 5. Confirm judgment and publish

The browser uses the workbench judgment-confirmation control. The successor
workspace must report `ready_to_freeze` at 95 percent and contain the confirmed
memo bound to the expected machine memo and draft lock version.

The browser opens the publication preview and verifies its project, company,
cutoff, assessment, artifact manifest, and manifest hash. It then confirms the
freeze through the publication dialog. The resulting workspace and revision
must report `completed`, no current preparation step, and 100 percent progress.

### 6. Replay and export

The browser reads the selected frozen revision using the application's normal
revision path and triggers Markdown export through the UI. The verifier proves:

- the revision belongs to the created project and selected company;
- the revision ID is the workspace's selected revision;
- the export filename is bound to that revision ID;
- the media type is Markdown and the content is non-empty;
- the reported content hash equals the hash of the returned content;
- rereading the workspace does not change evidence decisions or frozen artifact
  identities.

## Cross-cutting Traffic and UI Checks

The verifier records bounded metadata for browser requests and responses under
the local `/api/...` route. It fails if an expected write bypasses the browser,
if an API request fails, or if an unexpected external HTTP request is made.
The bearer value itself is never recorded.

The run also fails on:

- an unhandled page exception;
- a relevant browser console error;
- a failed API request;
- a component process exiting before its expected shutdown;
- status regression or an invalid state transition;
- project, artifact, memo, manifest, or revision identity drift;
- a non-advancing or skipped evidence version;
- duplicate initialization or publication;
- a bounded polling or total-run timeout.

Diagnostics may include sanitized process names, exit codes, bounded stderr or
stdout tails, HTTP methods, local paths, status codes, and current public
workflow states. They must not include environment dumps, database contents,
request bodies, Authorization values, bearer tokens, or provider secrets.

## Environment and Cleanup Safety

The child-process environment is allowlisted. Host values for database URLs,
tokens, proxy variables, provider keys, mock-client switches, shell startup
hooks, and Python import hooks must not silently influence the verifier. Values
needed by the isolated run are supplied explicitly.

The temporary directory must be created beneath a resolved private parent,
must not have existed before this run, must be a direct non-symlink child owned
by the current user, and must use restrictive permissions. The same conditions
are revalidated immediately before removal. If revalidation fails, the verifier
must not remove the target.

Content deletion is fd-relative and does not follow symlinks. The quarantined
root path is revalidated against its original owner, mode, device, and inode
immediately before the final nonrecursive `rmdir`. Darwin has no portable
inode-conditional directory unlink: a same-UID actor can still replace the
now-empty path in the interval after that last check and before `rmdir`, and an
empty replacement could then be removed. The verifier therefore retains the
approved exclusion of concurrent same-UID mutation for that interval. This is
the only remaining replacement race in the cleanup threat model; a nonempty
replacement is preserved because nonrecursive `rmdir` fails closed.

Shutdown is idempotent and bounded. It first asks owned child processes to exit,
then escalates only against the exact processes it started. On macOS and Linux,
browser launch must authenticate and retain privately that Playwright's browser
process is its POSIX process-group leader; otherwise launch fails before the
workflow. Browser startup is a retained, abortable ownership transition: a
signal during launch waits for the bounded launch to expose its process, then
authenticates and removes that exact group, while a signal during connection
removes the already-authenticated group immediately and observes the late
connection result. Runtime removal waits for that transition to settle. Browser
cleanup signals that exact group with bounded TERM/KILL and verifies its
absence, including renderer descendants. The verifier handles outer
TERM/HUP/INT cooperatively so the Python npm-session owner cannot strand the
browser's separate process group. After its TERM grace, the Python owner checks
the exact npm PGID even if the session leader has exited, escalates against any
remaining members, and verifies group absence. Cleanup preserves the original
business failure code. A cleanup failure is reported without masking an earlier
failure; if cleanup is the only failure, the run exits nonzero. Every Playwright
response or event wait has a rejection handler attached before the click that
triggers it, so a primary action failure cannot be overtaken by an abandoned wait
during browser shutdown.

## Implementation Shape

Expected production-facing files:

- `frontend/scripts/verify-live-company-research-ui.mjs` for lifecycle and
  browser orchestration;
- `backend/app/scripts/remove_private_runtime_contents.py` for fd-relative,
  fail-closed deletion of the verifier's authenticated private runtime;
- `frontend/package.json` for the stable command;
- `README.md` for prerequisites, invocation, coverage, and isolation semantics.

Tests should live beside the repository's existing runtime/script tests and
cover the verifier as a black box where practical. Frontend component changes
are allowed only if the current workbench lacks a necessary accessible control
or stable semantic locator; such changes must be minimal and separately tested.
The design does not authorize changes to Company Research domain behavior.

## Verification Strategy

Verification is layered:

1. Static and focused tests prove argument handling, environment isolation,
   mock-mode prohibition, external-network prohibition, bounded diagnostics,
   and cleanup guards.
2. Dynamic harness cases simulate early API/worker/Vite exits, timeouts,
   malformed state transitions, identity drift, and cleanup mutation.
3. One real-browser run executes the complete Alphabet workflow against the
   actual API, SQLite persistence, worker, and non-mock frontend.
4. Relevant backend Company Research tests, frontend Vitest coverage, existing
   mock Playwright tests, lint/static checks, and the new live verifier all pass
   before completion is claimed.

The live run should be serialized unless and until unique resource ownership is
proved under parallel execution. Poll intervals and total timeouts must be
explicit and configurable only within bounded ranges suitable for local and CI
execution.

## Acceptance Criteria

The work is complete when a documented command, starting from an empty isolated
database, reliably reaches a frozen Alphabet Company Research revision and
valid Markdown export through the normal browser application. The run leaves
no owned child process or temporary database behind, emits no secret-bearing
diagnostics, performs no external request, and all relevant regressions pass.
