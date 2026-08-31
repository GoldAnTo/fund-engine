# Company Research Live Full-Flow E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one deterministic browser acceptance command that starts an isolated real API, database, Company Research worker, and non-mock frontend, then drives Alphabet research through publication and verified Markdown export.

**Architecture:** A small reusable Node support module owns bounded arguments, allowlisted environments, private temporary storage, child-process supervision, polling, and closed Company Research snapshot validation. A standalone verifier composes those primitives with Playwright, the existing product-foundation loader, FastAPI, the real worker, and Vite; a Python integration test invokes the same public command used by developers and CI.

**Tech Stack:** Node.js 24 ESM, Playwright, Vite, React, FastAPI, SQLAlchemy, SQLite, pytest, Vitest.

---

## File Structure

- Create `frontend/scripts/live-company-research-support.mjs`: pure validation plus bounded runtime, environment, temporary-directory, child-process, and polling utilities.
- Create `frontend/scripts/live-company-research-support.test.mjs`: Node unit and dynamic process/cleanup tests for the support boundary.
- Create `frontend/scripts/verify-live-company-research-ui.mjs`: environment bootstrap, component lifecycle, Playwright workflow, traffic audit, and final diagnostics.
- Create `backend/tests/test_verify_live_company_research_ui.py`: package-command contract and one real full-flow browser acceptance test.
- Modify `frontend/package.json`: expose `verify:live-company-research` and a focused support-test command.
- Modify `README.md`: document prerequisites, exact invocation, verified layers, isolation, and non-goals.

No Company Research domain service or UI component is expected to change. If a required visible control is absent during implementation, stop that task and amend the approved design before changing product behavior.

### Task 1: Closed verifier configuration and snapshot contracts

**Files:**
- Create: `frontend/scripts/live-company-research-support.mjs`
- Create: `frontend/scripts/live-company-research-support.test.mjs`

- [ ] **Step 1: Write failing tests for arguments, environment isolation, URLs, and snapshot validation**

Create the test file with Node's built-in test runner:

```js
import assert from "node:assert/strict";
import test from "node:test";

import {
  assertLoopbackUrl,
  assertWorkspace,
  buildVerifierEnvironment,
  parseVerifierArgs,
} from "./live-company-research-support.mjs";

test("accepts only one bounded timeout option", () => {
  assert.deepEqual(parseVerifierArgs([]), { timeoutMs: 180_000 });
  assert.deepEqual(parseVerifierArgs(["--timeout-seconds", "90"]), { timeoutMs: 90_000 });
  for (const argv of [
    ["--timeout-seconds=90"], ["--timeout-seconds"], ["--timeout-seconds", "29"],
    ["--timeout-seconds", "301"], ["--unknown", "1"],
  ]) assert.throws(() => parseVerifierArgs(argv), /usage:/u);
});

test("constructs a minimal child environment", () => {
  const env = buildVerifierEnvironment({
    host: {
      PATH: "/tools", HOME: "/host-home", DATABASE_URL: "postgresql://production",
      RESEARCH_TENANT_TOKENS: "secret", VITE_RESEARCH_CLIENT: "mock",
      LLM_API_KEY: "secret", BASH_ENV: "/host/bash", PYTHONPATH: "/host/python",
    },
    databaseUrl: "sqlite:////private/live.sqlite",
    token: "test-only-token",
    backendUrl: "http://127.0.0.1:41001",
  });
  assert.equal(env.PATH, "/tools");
  assert.equal(env.DATABASE_URL, "sqlite:////private/live.sqlite");
  assert.equal(env.VITE_BACKEND_URL, "http://127.0.0.1:41001");
  assert.equal(env.VITE_RESEARCH_CLIENT, "");
  assert.equal(env.RESEARCH_BEARER_TOKEN, "test-only-token");
  for (const key of ["HOME", "LLM_API_KEY", "BASH_ENV", "PYTHONPATH"])
    assert.equal(Object.hasOwn(env, key), false);
});

test("accepts loopback HTTP and rejects every external or credential-bearing URL", () => {
  assert.doesNotThrow(() => assertLoopbackUrl("http://127.0.0.1:41001/api/underwriting/v1/product/objects"));
  for (const url of ["https://example.com/a", "http://localhost.evil.test/a", "http://user:pass@127.0.0.1/a"])
    assert.throws(() => assertLoopbackUrl(url), /loopback/u);
});

test("authenticates exact Company Research state and identity", () => {
  const workspace = {
    schema_version: "underwriting.v1",
    project_id: "project-1",
    company: { id: "company-1", object_id: "company-1", external_key: "US:ALPHABET:COMPANY" },
    preparation: { status: "awaiting_evidence_review", progress: 25, current_step: "research_gaps" },
    draft: { lock_version: 2 }, selected_revision: null,
    change_summary: { reviewed_fact_count: 0, artifact_versions: { evidence_index: 1, research_gaps: 1 } },
    artifacts: [], modules: [],
  };
  assert.equal(assertWorkspace(workspace, {
    projectId: "project-1", companyId: "company-1", status: "awaiting_evidence_review", progress: 25,
  }), workspace);
  assert.throws(() => assertWorkspace({ ...workspace, project_id: "other" }, {
    projectId: "project-1", companyId: "company-1", status: "awaiting_evidence_review", progress: 25,
  }), /project identity/u);
  assert.throws(() => assertWorkspace({ ...workspace, preparation: { ...workspace.preparation, progress: 24 } }, {
    projectId: "project-1", companyId: "company-1", status: "awaiting_evidence_review", progress: 25,
  }), /progress/u);
});
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd frontend
node scripts/with-project-node.mjs --test scripts/live-company-research-support.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `live-company-research-support.mjs`.

- [ ] **Step 3: Implement the closed configuration and workspace validators**

Create `live-company-research-support.mjs` with these exact public functions:

```js
const USAGE = "usage: verify-live-company-research-ui.mjs [--timeout-seconds 30..300]";
const STATES = new Set([
  "queued", "preparing_sources", "awaiting_evidence_review", "building_model",
  "awaiting_judgment_review", "ready_to_freeze", "recoverable_failure", "blocked", "completed",
]);

export function parseVerifierArgs(argv) {
  if (argv.length === 0) return { timeoutMs: 180_000 };
  if (argv.length !== 2 || argv[0] !== "--timeout-seconds" || !/^\d+$/u.test(argv[1]))
    throw new Error(USAGE);
  const seconds = Number(argv[1]);
  if (!Number.isSafeInteger(seconds) || seconds < 30 || seconds > 300) throw new Error(USAGE);
  return { timeoutMs: seconds * 1000 };
}

export function buildVerifierEnvironment({ host, databaseUrl, token, backendUrl }) {
  const inherited = {};
  for (const key of ["PATH", "SystemRoot", "WINDIR", "TMPDIR", "TEMP", "TMP", "NVM_DIR", "PW_BROWSER_CHANNEL"])
    if (typeof host[key] === "string" && host[key]) inherited[key] = host[key];
  return {
    ...inherited,
    APP_ENV: "production",
    DATABASE_URL: databaseUrl,
    RESEARCH_TENANT_TOKENS: JSON.stringify({ [token]: "live-company-research-verifier" }),
    VITE_BACKEND_URL: backendUrl,
    RESEARCH_BEARER_TOKEN: token,
    VITE_RESEARCH_CLIENT: "",
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  };
}

export function assertLoopbackUrl(raw) {
  const value = new URL(raw);
  if (value.protocol !== "http:" || value.hostname !== "127.0.0.1" || value.username || value.password)
    throw new Error("verifier URL must be credential-free loopback HTTP");
  return value;
}

export function assertWorkspace(value, expected) {
  if (!value || value.schema_version !== "underwriting.v1") throw new Error("workspace schema mismatch");
  if (value.project_id !== expected.projectId) throw new Error("workspace project identity mismatch");
  if (value.company?.id !== expected.companyId || value.company?.object_id !== expected.companyId)
    throw new Error("workspace company identity mismatch");
  if (!STATES.has(value.preparation?.status) || value.preparation.status !== expected.status)
    throw new Error("workspace status mismatch");
  if (value.preparation.progress !== expected.progress) throw new Error("workspace progress mismatch");
  if (!Number.isSafeInteger(value.draft?.lock_version) || value.draft.lock_version < 1)
    throw new Error("workspace draft lock mismatch");
  return value;
}
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command.

Expected: 4 tests pass with zero failures.

- [ ] **Step 5: Commit the closed verifier contracts**

```bash
git add frontend/scripts/live-company-research-support.mjs frontend/scripts/live-company-research-support.test.mjs
git commit -m "test: define live company research verifier contracts"
```

### Task 2: Private runtime and supervised child processes

**Files:**
- Modify: `frontend/scripts/live-company-research-support.mjs`
- Modify: `frontend/scripts/live-company-research-support.test.mjs`
- Create: `backend/app/scripts/remove_private_runtime_contents.py`
- Create: `backend/tests/test_remove_private_runtime_contents.py`

- [ ] **Step 1: Add failing dynamic tests for private storage, mutation-safe cleanup, timeout, and early exit**

Append tests that use real temporary directories and child processes:

```js
import { lstat, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  chooseRunError,
  createPrivateRuntime,
  removePrivateRuntime,
  startOwnedProcess,
  stopOwnedProcess,
  waitUntil,
} from "./live-company-research-support.mjs";

test("creates mode-0700 storage and removes only the recorded directory", async () => {
  const runtime = await createPrivateRuntime();
  assert.equal((await lstat(runtime.directory)).mode & 0o777, 0o700);
  await writeFile(path.join(runtime.directory, "sentinel"), "owned", "utf8");
  await removePrivateRuntime(runtime);
  await removePrivateRuntime(runtime);
  await assert.rejects(() => lstat(runtime.directory), { code: "ENOENT" });
});

test("refuses cleanup after target substitution", async () => {
  const runtime = await createPrivateRuntime();
  const moved = `${runtime.directory}.moved`;
  await import("node:fs/promises").then(({ rename }) => rename(runtime.directory, moved));
  await mkdir(runtime.directory, { mode: 0o700 });
  await writeFile(path.join(runtime.directory, "victim"), "preserve", "utf8");
  await assert.rejects(() => removePrivateRuntime(runtime), /identity changed/u);
  assert.equal(await readFile(path.join(runtime.directory, "victim"), "utf8"), "preserve");
  await import("node:fs/promises").then(({ rm }) => Promise.all([
    rm(runtime.directory, { recursive: true }), rm(moved, { recursive: true }),
  ]));
});

test("polling fails on timeout and on an owned process exit", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "sleeper",
  });
  await assert.rejects(() => waitUntil(async () => false, {
    label: "never ready", timeoutMs: 30, intervalMs: 5, processes: [sleeper],
  }), /timed out/u);
  await stopOwnedProcess(sleeper);

  const exited = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "worker",
  });
  await assert.rejects(() => waitUntil(async () => false, {
    label: "worker state", timeoutMs: 1_000, intervalMs: 5, processes: [exited],
  }), /worker exited with 7/u);
});

test("cleanup failure never masks an earlier workflow failure", () => {
  const workflow = new Error("workflow failed");
  const cleanup = new Error("cleanup failed");
  assert.equal(chooseRunError(workflow, [cleanup]), workflow);
  assert.equal(chooseRunError(null, [cleanup]), cleanup);
  assert.equal(chooseRunError(null, []), null);
});
```

- [ ] **Step 2: Run the support tests and verify RED**

Run:

```bash
cd frontend
node scripts/with-project-node.mjs --test scripts/live-company-research-support.test.mjs
```

Expected: FAIL because the five runtime functions are not exported.

- [ ] **Step 3: Implement bounded runtime ownership and supervision**

Add imports and implementations to the support module. Store `dev` and `ino`
from the first `lstat` so a renamed-and-replaced path cannot pass cleanup:

```js
import { spawn } from "node:child_process";
import { chmod, lstat, mkdtemp, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const PREFIX = "fund-engine-live-company-research-";
const MAX_LOG_BYTES = 16_384;

export async function createPrivateRuntime() {
  const parent = await realpath(os.tmpdir());
  const directory = await mkdtemp(path.join(parent, PREFIX));
  await chmod(directory, 0o700);
  const stat = await lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o777) !== 0o700)
    throw new Error("private runtime validation failed");
  if (typeof process.getuid === "function" && stat.uid !== process.getuid())
    throw new Error("private runtime owner mismatch");
  return { parent, directory, dev: stat.dev, ino: stat.ino };
}

export async function removePrivateRuntime(runtime) {
  let stat;
  try { stat = await lstat(runtime.directory); }
  catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  if (path.dirname(runtime.directory) !== runtime.parent || !path.basename(runtime.directory).startsWith(PREFIX)
    || !stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o777) !== 0o700
    || (typeof process.getuid === "function" && stat.uid !== process.getuid())
    || stat.dev !== runtime.dev || stat.ino !== runtime.ino)
    throw new Error("private runtime identity changed; refusing cleanup");
  await rm(runtime.directory, { recursive: true });
}

export function chooseRunError(primary, cleanupErrors) {
  if (primary) return primary;
  return cleanupErrors[0] ?? null;
}

function appendBounded(current, chunk) {
  return `${current}${String(chunk)}`.slice(-MAX_LOG_BYTES);
}

export function startOwnedProcess(command, args, { cwd, env, name }) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  const owned = { child, name, stdout: "", stderr: "", expectedStop: false };
  child.stdout.on("data", (chunk) => { owned.stdout = appendBounded(owned.stdout, chunk); });
  child.stderr.on("data", (chunk) => { owned.stderr = appendBounded(owned.stderr, chunk); });
  return owned;
}

export function assertProcessesRunning(processes) {
  for (const owned of processes) {
    if (!owned.expectedStop && owned.child.exitCode !== null)
      throw new Error(`${owned.name} exited with ${owned.child.exitCode}`);
  }
}

export async function stopOwnedProcess(owned) {
  if (!owned || owned.child.exitCode !== null) return;
  owned.expectedStop = true;
  owned.child.kill("SIGTERM");
  const exited = new Promise((resolve) => owned.child.once("exit", resolve));
  const graceful = await Promise.race([
    exited.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 5_000)),
  ]);
  if (!graceful && owned.child.exitCode === null) {
    owned.child.kill("SIGKILL");
    await exited;
  }
}

export async function waitUntil(probe, { label, timeoutMs, intervalMs = 100, processes = [] }) {
  const deadline = Date.now() + timeoutMs;
  let latest = "not ready";
  while (Date.now() < deadline) {
    assertProcessesRunning(processes);
    try {
      const value = await probe();
      if (value) return value;
      latest = "not ready";
    } catch (error) {
      latest = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`${label} timed out: ${latest}`);
}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command.

Expected: 8 tests pass and no child process remains.

- [ ] **Step 5: Commit the runtime boundary**

```bash
git add frontend/scripts/live-company-research-support.mjs frontend/scripts/live-company-research-support.test.mjs
git commit -m "test: supervise live company research runtime"
```

### Task 3: Start the isolated real stack and create the project through the browser

**Files:**
- Create: `frontend/scripts/verify-live-company-research-ui.mjs`
- Modify: `frontend/scripts/live-company-research-support.mjs`
- Modify: `frontend/scripts/live-company-research-support.test.mjs`

- [ ] **Step 1: Add failing tests for traffic collection and exact successor rules**

Add pure tests before writing browser orchestration:

```js
import { createTrafficAudit, assertExactReviewSuccessor } from "./live-company-research-support.mjs";

test("traffic audit rejects external requests, failed API responses, and duplicate singleton writes", () => {
  const audit = createTrafficAudit("http://127.0.0.1:42000", "test-only-token");
  audit.recordRequest("POST", "http://127.0.0.1:42000/api/underwriting/v1/product/company-research/preview", {});
  audit.recordResponse(200, "POST", "http://127.0.0.1:42000/api/underwriting/v1/product/company-research/preview");
  assert.doesNotThrow(() => audit.assertSingleton("POST", "/api/underwriting/v1/product/company-research/preview"));
  assert.throws(() => audit.recordRequest("GET", "https://example.com/tracker", {}), /external request/u);
  assert.throws(() => audit.recordResponse(500, "GET", "http://127.0.0.1:42000/api/x"), /API response/u);
});

test("review successor advances exactly one fact and one version", () => {
  const before = { id: "e1", version: 1, payload: { facts: [
    { fact_key: "a", review_decision: null }, { fact_key: "b", review_decision: null },
  ] } };
  const after = { id: "e2", version: 2, payload: { facts: [
    { fact_key: "a", review_decision: "confirmed" }, { fact_key: "b", review_decision: null },
  ] } };
  assert.doesNotThrow(() => assertExactReviewSuccessor(before, after, "a"));
  assert.throws(() => assertExactReviewSuccessor(before, { ...after, version: 3 }, "a"), /version/u);
  assert.throws(() => assertExactReviewSuccessor(before, {
    ...after, payload: { facts: after.payload.facts.map((fact) => ({ ...fact, review_decision: "confirmed" })) },
  }, "a"), /unrelated fact/u);
});
```

- [ ] **Step 2: Run the support tests and verify RED**

Expected: FAIL because `createTrafficAudit` and `assertExactReviewSuccessor` do not exist.

- [ ] **Step 3: Implement traffic and review validators**

Add exact path cardinality without retaining headers or bodies:

```js
export function createTrafficAudit(uiBase) {
  const origin = new URL(uiBase).origin;
  const requests = [];
  const responses = [];
  return {
    recordRequest(method, raw) {
      const url = new URL(raw);
      if (url.origin !== origin && !["about:", "blob:", "data:"].includes(url.protocol))
        throw new Error(`unexpected external request: ${url.origin}${url.pathname}`);
      if (url.origin === origin && url.pathname.startsWith("/api/")) requests.push([method, url.pathname]);
    },
    recordResponse(status, method, raw) {
      const url = new URL(raw);
      if (url.origin === origin && url.pathname.startsWith("/api/")) {
        if (status < 200 || status >= 300) throw new Error(`unexpected API response ${status} ${method} ${url.pathname}`);
        responses.push([status, method, url.pathname]);
      }
    },
    assertSingleton(method, pathname) {
      const count = requests.filter(([seenMethod, seenPath]) => seenMethod === method && seenPath === pathname).length;
      if (count !== 1) throw new Error(`expected one ${method} ${pathname}; saw ${count}`);
    },
    requestCount(method, pathname) {
      return requests.filter(([seenMethod, seenPath]) => seenMethod === method && seenPath === pathname).length;
    },
    snapshot() { return { requests: [...requests], responses: [...responses] }; },
  };
}

export function assertExactReviewSuccessor(before, after, factKey) {
  if (after.id === before.id || after.version !== before.version + 1) throw new Error("review version did not advance exactly once");
  const previous = new Map(before.payload.facts.map((fact) => [fact.fact_key, fact.review_decision]));
  const next = new Map(after.payload.facts.map((fact) => [fact.fact_key, fact.review_decision]));
  if (previous.size !== next.size || !previous.has(factKey) || previous.get(factKey) !== null || next.get(factKey) !== "confirmed")
    throw new Error("reviewed fact successor mismatch");
  for (const [key, decision] of previous)
    if (key !== factKey && next.get(key) !== decision) throw new Error("review changed an unrelated fact");
  return after;
}
```

- [ ] **Step 4: Create the verifier bootstrap and first browser phase**

Implement the verifier with one `main()` and a `finally` that closes the browser,
stops Vite/API/worker, and removes only the owned runtime. The bootstrap commands are:

```js
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import {
  assertLoopbackUrl, assertProcessesRunning, buildVerifierEnvironment,
  chooseRunError, createPrivateRuntime, createTrafficAudit, parseVerifierArgs,
  removePrivateRuntime, startOwnedProcess, stopOwnedProcess, waitUntil,
} from "./live-company-research-support.mjs";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");
const backend = path.join(root, "backend");
const python = process.env.PYTHON || "python";
const token = "live-company-research-verifier-token";

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

async function responseJson(response) {
  const body = await response.json();
  if (!response.ok()) throw new Error(`browser API ${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
  return body;
}
```

Inside `main()`, create the schema and foundation before starting services:

```js
const runtime = await createPrivateRuntime();
const [apiPort, uiPort] = await Promise.all([freePort(), freePort()]);
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const uiBase = `http://127.0.0.1:${uiPort}`;
assertLoopbackUrl(apiOrigin); assertLoopbackUrl(uiBase);
const env = buildVerifierEnvironment({
  host: process.env,
  databaseUrl: `sqlite:///${path.join(runtime.directory, "company-research.sqlite")}`,
  token,
  backendUrl: apiOrigin,
});
execFileSync(python, ["-c", "from app.models.ledger import Base; from app.db import engine; Base.metadata.create_all(engine)"], {
  cwd: backend, env, stdio: ["ignore", "ignore", "pipe"],
});
execFileSync(python, ["-m", "app.scripts.load_product_foundation_fixture"], {
  cwd: backend, env, stdio: ["ignore", "ignore", "pipe"],
});
api = startOwnedProcess(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(apiPort)], {
  cwd: backend, env, name: "api",
});
worker = startOwnedProcess(python, ["-m", "app.scripts.run_company_research_worker", "--loop", "--poll-seconds", "0.1"], {
  cwd: backend, env, name: "company-research-worker",
});
vite = startOwnedProcess(process.execPath, ["./node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(uiPort)], {
  cwd: frontend, env, name: "vite",
});
```

Use this complete cleanup helper from `main()`'s `finally` block. Pass the
workflow exception, if any, to `chooseRunError` after cleanup:

```js
async function finalizeOwnedRuntime({ browser, worker, vite, api, runtime }) {
  const cleanupErrors = [];
  if (browser) try { await browser.close(); } catch (error) { cleanupErrors.push(error); }
  for (const owned of [worker, vite, api]) {
    try { await stopOwnedProcess(owned); } catch (error) { cleanupErrors.push(error); }
  }
  if (runtime) try { await removePrivateRuntime(runtime); } catch (error) { cleanupErrors.push(error); }
  return cleanupErrors;
}
```

The top-level sequence is `try { await runBrowserWorkflow(); } catch { store
primaryError; } finally { cleanupErrors = await finalizeOwnedRuntime({ browser,
worker, vite, api, runtime }); }`.
It then throws `chooseRunError(primaryError, cleanupErrors)` when non-null. The
single outer catch prints at most the first 1,000 characters of that safe error
message and sets `process.exitCode = 1`.

Wait for the authenticated search endpoint and frontend, then launch Playwright.
Pass an explicit browser-process environment rather than inheriting the
verifier host environment:

```js
browser = await chromium.launch({
  channel: env.PW_BROWSER_CHANNEL || undefined,
  env: Object.fromEntries(Object.entries({
    PATH: env.PATH, SystemRoot: env.SystemRoot, WINDIR: env.WINDIR,
    TMPDIR: runtime.directory, TEMP: runtime.directory, TMP: runtime.directory,
    NO_PROXY: env.NO_PROXY, no_proxy: env.no_proxy,
  }).filter(([, value]) => typeof value === "string" && value.length > 0)),
});
```

Register `pageerror`, console-error, request-failure, request, and response
handlers before navigation. Store only sanitized method/path/status metadata.

Drive creation with semantic selectors and capture the actual API bodies:

```js
await page.goto(`${uiBase}/research/new`, { waitUntil: "networkidle" });
await page.getByLabel("搜索公司、证券或行业").fill("Alphabet");
const searchResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/underwriting/v1/product/objects");
await page.getByRole("button", { name: "搜索对象" }).click();
const search = await responseJson(await searchResponse);
const alphabet = search.items.find((item) => item.external_key === "US:ALPHABET:COMPANY");
if (!alphabet) throw new Error("Alphabet company was not returned by the live API");
await page.getByRole("button", { name: "研究 Alphabet" }).click();
await page.getByRole("heading", { name: "确认默认研究方案" }).waitFor();
await page.getByText("关联证券：GOOG Class C；GOOGL Class A", { exact: false }).waitFor();
const initializeResponse = page.waitForResponse((response) =>
  response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/company-research/initializations"));
await page.getByRole("button", { name: "开始研究" }).click();
const initialized = await responseJson(await initializeResponse);
if (initialized.company_id !== alphabet.object_id) throw new Error("initialization company binding mismatch");
await page.waitForURL(new RegExp(`/research/projects/${initialized.project_id}$`, "u"));
```

- [ ] **Step 5: Run syntax and support checks**

```bash
cd frontend
node scripts/with-project-node.mjs --check scripts/verify-live-company-research-ui.mjs
node scripts/with-project-node.mjs --test scripts/live-company-research-support.test.mjs
```

Expected: syntax check exits 0 and the complete support suite passes.

- [ ] **Step 6: Commit isolated stack startup and browser creation**

```bash
git add frontend/scripts/live-company-research-support.mjs frontend/scripts/live-company-research-support.test.mjs frontend/scripts/verify-live-company-research-ui.mjs
git commit -m "test: start live company research browser flow"
```

### Task 4: Review every governed fact and let the real worker build the model

**Files:**
- Modify: `frontend/scripts/verify-live-company-research-ui.mjs`
- Modify: `frontend/scripts/live-company-research-support.test.mjs`

- [ ] **Step 1: Write a failing validator test for the completed model artifact contract**

Add a test proving missing, duplicate, or forbidden valuation artifacts fail:

```js
import { assertModelWorkspace } from "./live-company-research-support.mjs";

test("requires the exact not-answerable model artifact set", () => {
  const kinds = ["evidence_index", "research_gaps", "business_map", "driver_map", "financial_bridge", "scenario_set", "judgment_context", "memo"];
  const workspace = {
    preparation: { status: "awaiting_judgment_review", progress: 85 },
    artifacts: kinds.map((kind, index) => ({ id: `${kind}-${index}`, kind, version: 1, payload: kind === "memo" ? {
      candidate_status: "machine_draft", assessment_status: "not_answerable", direction: null, confidence: null,
    } : {} })),
  };
  assert.doesNotThrow(() => assertModelWorkspace(workspace));
  assert.throws(() => assertModelWorkspace({ ...workspace, artifacts: workspace.artifacts.slice(1) }), /artifact set/u);
  assert.throws(() => assertModelWorkspace({ ...workspace, artifacts: [...workspace.artifacts, { id: "v", kind: "valuation", version: 1, payload: {} }] }), /artifact set/u);
});
```

- [ ] **Step 2: Run the support tests and verify RED**

Expected: FAIL because `assertModelWorkspace` is not exported.

- [ ] **Step 3: Implement the exact model-workspace validator**

```js
const NOT_ANSWERABLE_ARTIFACTS = [
  "business_map", "driver_map", "evidence_index", "financial_bridge",
  "judgment_context", "memo", "research_gaps", "scenario_set",
];

export function assertModelWorkspace(workspace) {
  if (workspace.preparation?.status !== "awaiting_judgment_review" || workspace.preparation.progress !== 85)
    throw new Error("model workspace state mismatch");
  const kinds = workspace.artifacts.map((item) => item.kind).sort();
  if (JSON.stringify(kinds) !== JSON.stringify(NOT_ANSWERABLE_ARTIFACTS))
    throw new Error("model artifact set mismatch");
  const memo = workspace.artifacts.find((item) => item.kind === "memo");
  if (memo?.payload?.candidate_status !== "machine_draft" || memo.payload.assessment_status !== "not_answerable"
    || memo.payload.direction !== null || memo.payload.confidence !== null)
    throw new Error("not-answerable memo contract mismatch");
  return workspace;
}
```

- [ ] **Step 4: Implement browser-owned evidence review and controlled worker stages**

After project creation, wait for a browser workspace response at
`awaiting_evidence_review`/25. Stop the worker immediately after that state so
the final review's `building_model` transition cannot be skipped by a fast poll.
Read the evidence artifact from that authenticated workspace, derive all fact
keys from `payload.facts`, and require at least one fact.

For each fact key:

```js
const previousEvidence = evidence;
const reviewResponse = page.waitForResponse((response) => response.request().method() === "POST"
  && new URL(response.url()).pathname.endsWith(`/projects/${projectId}/evidence-reviews`));
await page.getByRole("button", { name: `确认事实 ${factKey}` }).click();
const reviewed = await responseJson(await reviewResponse);
evidence = assertExactReviewSuccessor(previousEvidence, reviewed.evidence_artifact, factKey);
await page.getByText(`事实 ${factKey} 已确认，证据版本 ${evidence.version}`, { exact: true }).waitFor();
```

After the last fact, capture a workspace response and require `building_model`
with the reviewed count equal to the number of fixture facts. Restart the same
real worker command, wait for `awaiting_judgment_review`/85, call
`assertModelWorkspace`, and verify the visible closed answerability message:

```js
await page.getByText("当前正式证据不足，不形成投资方向、置信度、目标价或预期回报。", { exact: true }).waitFor();
```

- [ ] **Step 5: Run support and syntax tests**

Run Task 3 Step 5 commands.

Expected: syntax exits 0 and 11 support tests pass.

- [ ] **Step 6: Commit evidence and model phases**

```bash
git add frontend/scripts/live-company-research-support.mjs frontend/scripts/live-company-research-support.test.mjs frontend/scripts/verify-live-company-research-ui.mjs
git commit -m "test: drive live company research model preparation"
```

### Task 5: Confirm, freeze, replay, and hash the downloaded Markdown

**Files:**
- Modify: `frontend/scripts/verify-live-company-research-ui.mjs`

- [ ] **Step 1: Add the judgment-confirmation phase through visible controls**

Capture the pre-confirmation memo and evidence decisions. Bind it before the
browser action:

```js
const machineMemo = modelWorkspace.artifacts.find((artifact) => artifact.kind === "memo");
if (!machineMemo) throw new Error("model workspace did not expose its machine memo");
```

Fill the existing
Markdown textbox with a deterministic human statement that preserves the
closed answerability boundary, click the visible confirmation button, and
capture its response:

```js
const humanMemo = "Current formal evidence is insufficient.";
await page.getByLabel("研究备忘录 Markdown").fill(humanMemo);
const confirmationResponse = page.waitForResponse((response) => response.request().method() === "POST"
  && new URL(response.url()).pathname.endsWith(`/projects/${projectId}/judgment-confirmations`));
await page.getByRole("button", { name: "确认当前判断" }).click();
const confirmation = await responseJson(await confirmationResponse);
if (confirmation.project_id !== projectId || confirmation.preparation.status !== "ready_to_freeze"
  || confirmation.preparation.progress !== 95 || confirmation.markdown !== humanMemo
  || confirmation.machine_memo.id !== machineMemo.id
  || confirmation.confirmed_memo.id === machineMemo.id)
  throw new Error("judgment confirmation successor mismatch");
await page.getByText("判断已确认，可以冻结版本。", { exact: true }).waitFor();
```

- [ ] **Step 2: Add publication preview and singleton publication**

Capture the preview response, validate its company, project, assessment,
cutoff, eight artifact kinds, non-empty manifest hash, and absence of value and
return ranges. Confirm the dialog exposes the same values, then capture the
publish response:

```js
const previewResponse = page.waitForResponse((response) => response.request().method() === "POST"
  && new URL(response.url()).pathname.endsWith(`/projects/${projectId}/publication-preview`));
await page.getByRole("button", { name: "预览冻结版本" }).click();
const preview = await responseJson(await previewResponse);
if (preview.project_id !== projectId || preview.company.external_key !== "US:ALPHABET:COMPANY"
  || preview.assessment.answerability !== "not_answerable" || preview.value_range !== null
  || preview.return_range !== null || !/^[0-9a-f]{64}$/u.test(preview.manifest_hash))
  throw new Error("publication preview contract mismatch");
await page.getByRole("heading", { name: "确认冻结版本" }).waitFor();
await page.getByText(preview.manifest_hash, { exact: true }).waitFor();
const publishResponse = page.waitForResponse((response) => response.request().method() === "POST"
  && new URL(response.url()).pathname.endsWith(`/projects/${projectId}/publish`));
await page.getByRole("button", { name: "冻结并发布" }).click();
const frozen = await responseJson(await publishResponse);
if (frozen.project_id !== projectId || frozen.preparation_status !== "completed"
  || frozen.current_step !== null || frozen.progress !== 100
  || frozen.manifest_hash === preview.manifest_hash)
  throw new Error("published revision contract mismatch");
```

The preview hash authenticates the pre-publication projection. Publication
adds the durable boundary, assessment, preparation, idempotency, and timestamp
identities before hashing the frozen manifest, so the frozen hash must differ;
the public frozen-revision response does not expose the persisted manifest
payload. The browser proves the binding by publishing from the dialog that
shows the exact preview hash, observing a successful singleton publish, and
then replaying the exact returned frozen revision. Backend publication tests
separately verify the persisted internal `preview_manifest_hash` binding.

- [ ] **Step 3: Add replay and actual-download hashing**

Wait until the automatic replay has loaded the exact revision. Click the
explicit replay control once to prove the visible action also works. Then use
Playwright's download event and the export API response together:

```js
await page.getByText(`冻结版本 ${frozen.id}`, { exact: true }).waitFor();
await page.getByRole("button", { name: "查看冻结版本" }).click();
await page.getByText("冻结版本已载入。", { exact: true }).waitFor();
const exportResponse = page.waitForResponse((response) => response.request().method() === "GET"
  && new URL(response.url()).pathname.endsWith(`/revisions/${frozen.id}/export`));
const downloadEvent = page.waitForEvent("download");
await page.getByRole("button", { name: "导出 Markdown" }).click();
const [download, exportHttpResponse] = await Promise.all([downloadEvent, exportResponse]);
const envelope = await responseJson(exportHttpResponse);
const downloadPath = await download.path();
if (!downloadPath) throw new Error("Markdown download did not produce a file");
const bytes = await readFile(downloadPath);
const contentHash = createHash("sha256").update(bytes).digest("hex");
if (download.suggestedFilename() !== envelope.filename
  || envelope.filename !== `alphabet-company-research-${frozen.id}.md`
  || envelope.media_type !== "text/markdown" || bytes.length === 0
  || contentHash !== envelope.content_hash || bytes.toString("utf8") !== envelope.content)
  throw new Error("Markdown download contract mismatch");
```

Reload the project through the browser, capture the final workspace, and
compare the saved evidence decisions and frozen artifact descriptors with the
pre-publication snapshot.

- [ ] **Step 4: Enforce final traffic cardinality and bounded failure output**

Before printing success, assert singleton browser writes for preview,
initialization, judgment confirmation, publication preview, and publish;
assert evidence-review request count equals the derived fixture fact count;
assert no page error, console error, request failure, failed API response, or
external request was recorded. The success line is exactly:

```text
PASS: default frontend completed live Alphabet company research through reviewed evidence, frozen revision replay, and verified Markdown export
```

In the top-level catch, print only the error message plus bounded stdout/stderr
tails labelled by component. Never print the environment, token, request body,
database, or response body. In `finally`, preserve the original failure if
shutdown or cleanup also fails; cleanup-only failure exits nonzero.

- [ ] **Step 5: Run syntax and support checks**

Run Task 3 Step 5 commands.

Expected: syntax exits 0 and all support tests pass.

- [ ] **Step 6: Commit publication closure**

```bash
git add frontend/scripts/verify-live-company-research-ui.mjs
git commit -m "test: close live company research publication flow"
```

### Task 6: Public command, real integration test, and documentation

**Files:**
- Modify: `frontend/package.json:8-18`
- Create: `backend/tests/test_verify_live_company_research_ui.py`
- Modify: `backend/pyproject.toml`
- Modify: `.github/workflows/backend.yml`
- Modify: `README.md:55-84`

- [ ] **Step 1: Write the failing command-contract test**

Create the Python test file:

```python
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
FRONTEND = ROOT / "frontend"
PASS_LINE = (
    "PASS: default frontend completed live Alphabet company research through "
    "reviewed evidence, frozen revision replay, and verified Markdown export"
)


def test_package_exposes_the_closed_live_company_research_command() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["verify:live-company-research"] == (
        "node scripts/with-project-node.mjs scripts/verify-live-company-research-ui.mjs"
    )
    assert package["scripts"]["test:live-company-research-support"] == (
        "node scripts/with-project-node.mjs --test "
        "scripts/live-company-research-support.test.mjs"
    )
```

- [ ] **Step 2: Run the command-contract test and verify RED**

```bash
cd backend
.venv/bin/python -m pytest tests/test_verify_live_company_research_ui.py::test_package_exposes_the_closed_live_company_research_command -q
```

Expected: FAIL with a missing `verify:live-company-research` key.

- [ ] **Step 3: Add the two package scripts**

Add exactly:

```json
"verify:live-company-research": "node scripts/with-project-node.mjs scripts/verify-live-company-research-ui.mjs",
"test:live-company-research-support": "node scripts/with-project-node.mjs --test scripts/live-company-research-support.test.mjs"
```

- [ ] **Step 4: Run the command-contract test and verify GREEN**

Run Step 2 again.

Expected: 1 test passes.

- [ ] **Step 5: Add the real full-flow pytest acceptance**

Register the `live_company_research` marker and gate only the real browser test
behind `RUN_LIVE_COMPANY_RESEARCH=1`. Ordinary backend pytest must report the
test as skipped; once opted in, missing Node, npm, repository backend venv,
frontend dependencies, or browser must fail rather than skip.

Invoke the public `npm run --silent verify:live-company-research` contract in a
minimal environment with `PYTHON=sys.executable`. Own npm as a new POSIX session
leader. The verifier has a 180-second internal bound; allow a 300-second outer
bound so its sequential browser/process/private-runtime cleanup retains a
120-second margin. On outer timeout, signal only the exact owned npm process
group with TERM and give the verifier's bounded signal handler time to remove
its separately detached, authenticated browser process group. If the npm group
does not exit, retain its unreaped leader as the exact capability, then send
KILL. Verify both the npm group and every recorded detached browser descendant
or group are absent.
Success is return code zero, exactly one PASS line on stdout, and empty stderr.
Failure output must be checked for sensitive names and values before returning
only a bounded prerequisite classification or generic safe diagnostic.

- [ ] **Step 6: Run the real browser acceptance**

```bash
cd "$(git rev-parse --show-toplevel)"
if test -x backend/.venv/bin/python; then
  BACKEND_PYTHON="$(pwd)/backend/.venv/bin/python"
else
  BACKEND_PYTHON="$(cd ../.. && pwd)/backend/.venv/bin/python"
fi
PW_BROWSER_CHANNEL=chrome RUN_LIVE_COMPANY_RESEARCH=1 "$BACKEND_PYTHON" -m pytest backend/tests/test_verify_live_company_research_ui.py -q
```

Expected: all verifier harness tests and the opted-in live test pass. The live
test starts from a fresh database and accepts exactly the single PASS line.

- [ ] **Step 7: Document the public command and boundary**

Add after the existing live event UI command in `README.md`:

```markdown
# 真实 Company Research 浏览器闭环：隔离 SQLite + 真实 API/worker + 非 mock 前端
cd frontend
PYTHON=../backend/.venv/bin/python PW_BROWSER_CHANNEL=chrome npm run verify:live-company-research
```

Document that the command loads only the governed Alphabet identity foundation,
performs every human write through the browser, uses the frozen authenticated
Alphabet fixture, reaches a frozen revision and verified Markdown download,
does not contact external providers, and removes its isolated database and
owned processes on exit.

- [ ] **Step 8: Commit the command and documentation**

```bash
git add frontend/package.json backend/tests/test_verify_live_company_research_ui.py README.md
git commit -m "docs: expose live company research acceptance"
```

### Task 7: Full verification and safety review

**Files:**
- Modify only if a failing check identifies a defect in the files introduced by Tasks 1-6.

- [ ] **Step 1: Run focused Node, frontend, and backend tests**

```bash
cd "$(git rev-parse --show-toplevel)"
if test -x backend/.venv/bin/python; then
  BACKEND_PYTHON="$(pwd)/backend/.venv/bin/python"
else
  BACKEND_PYTHON="$(cd ../.. && pwd)/backend/.venv/bin/python"
fi
"$BACKEND_PYTHON" -m pip install -e "./backend[dev]"
cd frontend
npm run test:live-company-research-support
npm test -- --run src/features/investment-research/NewResearchPage.test.tsx src/features/investment-research/ResearchWorkbenchPage.test.tsx
cd ..
RUN_LIVE_COMPANY_RESEARCH=1 "$BACKEND_PYTHON" -m pytest \
  backend/tests/test_verify_live_company_research_ui.py \
  backend/tests/underwriting/test_company_research_api.py::test_company_research_publication_closes_the_entire_public_http_workflow \
  backend/tests/underwriting/test_company_research_worker.py::test_claim_is_exclusive_and_success_stops_at_evidence_review \
  backend/tests/underwriting/test_company_research_worker.py::test_worker_builds_all_model_artifacts_after_last_evidence_review -q
```

Expected: all Node tests, 87 frontend tests, all live-verifier harness tests,
the opted-in browser acceptance, the full HTTP publication test, and both
worker transition tests pass.

- [ ] **Step 2: Run existing browser regression and the new live command**

```bash
cd frontend
npm run e2e
PYTHON="$BACKEND_PYTHON" PW_BROWSER_CHANNEL=chrome npm run verify:live-company-research
cd ..
```

Expected: existing mock Playwright tests pass; the live command prints the
exact PASS line once and exits 0.

- [ ] **Step 3: Run static quality checks**

```bash
cd frontend
npm run typecheck
npm run build
node scripts/with-project-node.mjs --check scripts/live-company-research-support.mjs
node scripts/with-project-node.mjs --check scripts/verify-live-company-research-ui.mjs
cd ..
"$BACKEND_PYTHON" -m ruff check backend/tests/test_verify_live_company_research_ui.py
git diff --check
git status --short
```

Expected: every command exits 0; status contains only intended files.

- [ ] **Step 4: Perform the explicit safety audit**

Inspect the final diff and confirm all of these statements are true:

- no child environment starts from `{ ...process.env }`;
- only loopback URLs are accepted and no browser external request is ignored;
- no Authorization value, environment dump, API body, or database content is logged;
- temporary cleanup revalidates parent, prefix, type, owner, mode, device, and inode;
- only exact owned child handles receive signals;
- timeout and early-exit paths are covered by dynamic tests;
- the original failure remains primary when cleanup also fails;
- project, company, artifact, memo, manifest, revision, and export hashes are checked;
- every human write originates from a Playwright click or fill-plus-click path.

### Task 7: Final lifecycle safety closure

- [x] Attach a rejection observer to every `waitForResponse` and `waitForEvent`
  promise before its triggering browser action; prove an action failure remains
  primary when browser close rejects an abandoned wait and no
  `unhandledRejection` or private-runtime residue remains.
- [x] Authenticate Playwright's browser PID as its POSIX process-group leader at
  launch, retain that authority only in private support state, and fail closed
  before browser workflow if the capability cannot be established.
- [x] On graceful browser-close timeout or rejection, signal the exact retained
  PGID with bounded TERM/KILL and verify group absence. Cover a real group whose
  renderer descendant ignores TERM.
- [x] Give the verifier cooperative signal ownership and cover the Python outer
  timeout with a real detached browser tree, exact recorded signals, and absence
  checks for every recorded PID and PGID.
- [x] Separate realistic cleanup-helper preflight time from the deterministic
  cleanup-stall bound, allocate test resources inside `try/finally`, and stress
  the case at least 30 times without helper or runtime residue.
- [x] Revalidate the quarantined path's complete identity immediately before
  nonrecursive `rmdir`. Preserve the approved same-UID-concurrent-mutation
  exclusion: Darwin offers no portable inode-conditional directory unlink, so
  only an empty same-UID replacement after the final check remains outside the
  threat model; nonempty replacements are never recursively removed.

- [ ] **Step 5: Commit only if verification required a correction**

```bash
git add frontend/scripts/live-company-research-support.mjs \
  frontend/scripts/live-company-research-support.test.mjs \
  frontend/scripts/verify-live-company-research-ui.mjs \
  frontend/package.json backend/tests/test_verify_live_company_research_ui.py README.md
git commit -m "fix: harden live company research acceptance"
```

If no correction was needed, do not create an empty commit.
