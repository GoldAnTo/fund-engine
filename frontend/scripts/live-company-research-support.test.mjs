import assert from "node:assert/strict";
import { lstat, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import test from "node:test";

import {
  assertLoopbackUrl,
  assertWorkspace,
  buildVerifierEnvironment,
  chooseRunError,
  createPrivateRuntime,
  parseVerifierArgs,
  removePrivateRuntime,
  startOwnedProcess,
  stopOwnedProcess,
  waitUntil,
} from "./live-company-research-support.mjs";

test("parseVerifierArgs accepts only the bounded two-token timeout option", () => {
  assert.deepEqual(parseVerifierArgs([]), { timeoutMs: 180_000 });
  assert.deepEqual(parseVerifierArgs(["--timeout-seconds", "90"]), { timeoutMs: 90_000 });

  for (const args of [
    ["--timeout-seconds=90"],
    ["--timeout-seconds"],
    ["--timeout-seconds", "29"],
    ["--timeout-seconds", "301"],
    ["--unknown", "1"],
  ]) {
    assert.throws(() => parseVerifierArgs(args), /usage:/);
  }
});

test("buildVerifierEnvironment exposes only the verifier's closed environment", () => {
  const hostEnvironment = {
    PATH: "/tools",
    HOME: "/hostile/home",
    DATABASE_URL: "postgresql://hostile/database",
    RESEARCH_TENANT_TOKENS: "hostile-token",
    VITE_RESEARCH_CLIENT: "mock",
    LLM_API_KEY: "hostile-key",
    BASH_ENV: "/hostile/bash-env",
    PYTHONPATH: "/hostile/pythonpath",
    TMPDIR: "",
  };

  const environment = buildVerifierEnvironment({
    host: hostEnvironment,
    databaseUrl: "sqlite:////private/live.sqlite",
    token: "test-only-token",
    backendUrl: "http://127.0.0.1:41001",
  });

  assert.deepEqual(environment, {
    PATH: "/tools",
    APP_ENV: "production",
    DATABASE_URL: "sqlite:////private/live.sqlite",
    RESEARCH_TENANT_TOKENS: JSON.stringify({ "test-only-token": "live-company-research-verifier" }),
    VITE_BACKEND_URL: "http://127.0.0.1:41001",
    RESEARCH_BEARER_TOKEN: "test-only-token",
    VITE_RESEARCH_CLIENT: "",
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  });
  for (const key of ["HOME", "LLM_API_KEY", "BASH_ENV", "PYTHONPATH"]) {
    assert.equal(key in environment, false, `${key} must not be inherited`);
  }
  assert.equal("TMPDIR" in environment, false, "empty allowlisted values must not be inherited");
});

test("assertLoopbackUrl permits only credential-free HTTP on 127.0.0.1", () => {
  const accepted = "http://127.0.0.1:41001/api/underwriting/v1/product/objects";
  const parsed = assertLoopbackUrl(accepted);
  assert.ok(parsed instanceof URL);
  assert.equal(parsed.hostname, "127.0.0.1");

  for (const url of [
    "https://example.com/a",
    "https://127.0.0.1/a",
    "http://localhost/a",
    "http://localhost.evil.test/a",
    "http://user:pass@127.0.0.1/a",
    "http://user@127.0.0.1/a",
    "http://:pass@127.0.0.1/a",
    "http://:@127.0.0.1/a",
    "not a URL",
    "http://127.1/a",
    "http://2130706433/a",
    "http://0177.0.0.1/a",
    "http://0x7f000001/a",
    "http://127.0.0.1:99999/a",
    null,
  ]) {
    assert.throws(() => assertLoopbackUrl(url), /loopback/);
  }
});

test("assertWorkspace rejects snapshot drift while preserving a valid workspace", () => {
  const expected = {
    projectId: "project-1",
    companyId: "company-1",
    status: "awaiting_evidence_review",
    progress: 25,
  };
  const workspace = {
    schema_version: "underwriting.v1",
    project_id: "project-1",
    company: {
      id: "company-1",
      object_id: "company-1",
    },
    preparation: {
      status: "awaiting_evidence_review",
      progress: 25,
    },
    draft: {
      lock_version: 2,
    },
  };

  assert.strictEqual(assertWorkspace(workspace, expected), workspace);

  const projectDrift = structuredClone(workspace);
  projectDrift.project_id = "project-2";
  assert.throws(() => assertWorkspace(projectDrift, expected), /project identity/);

  const progressDrift = structuredClone(workspace);
  progressDrift.preparation.progress = 24;
  assert.throws(() => assertWorkspace(progressDrift, expected), /progress/);

  const schemaDrift = structuredClone(workspace);
  schemaDrift.schema_version = "underwriting.v2";
  assert.throws(() => assertWorkspace(schemaDrift, expected), /schema/);

  const companyIdDrift = structuredClone(workspace);
  companyIdDrift.company.id = "company-2";
  assert.throws(() => assertWorkspace(companyIdDrift, expected), /company identity/);

  const companyObjectIdDrift = structuredClone(workspace);
  companyObjectIdDrift.company.object_id = "company-2";
  assert.throws(() => assertWorkspace(companyObjectIdDrift, expected), /company identity/);

  const unknownState = structuredClone(workspace);
  unknownState.preparation.status = "unexpected";
  assert.throws(() => assertWorkspace(unknownState, expected), /status/);

  const allowedButUnexpectedState = structuredClone(workspace);
  allowedButUnexpectedState.preparation.status = "building_model";
  assert.throws(() => assertWorkspace(allowedButUnexpectedState, expected), /status/);

  for (const [nestedRecord, pattern] of [
    ["company", /company identity/],
    ["preparation", /status/],
    ["draft", /draft lock/],
  ]) {
    const missingNestedRecord = structuredClone(workspace);
    delete missingNestedRecord[nestedRecord];
    assert.throws(() => assertWorkspace(missingNestedRecord, expected), pattern);
  }

  for (const lockVersion of [0, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    const invalidLockVersion = structuredClone(workspace);
    invalidLockVersion.draft.lock_version = lockVersion;
    assert.throws(() => assertWorkspace(invalidLockVersion, expected), /draft lock/);
  }
});

test("createPrivateRuntime creates a private owned directory and removePrivateRuntime is idempotent", async () => {
  const runtime = await createPrivateRuntime();
  const sentinel = `${runtime.directory}/owned-sentinel`;

  assert.equal((await lstat(runtime.directory)).mode & 0o777, 0o700);
  await writeFile(sentinel, "owned");
  await removePrivateRuntime(runtime);
  await removePrivateRuntime(runtime);
  await assert.rejects(lstat(runtime.directory), { code: "ENOENT" });
});

test("removePrivateRuntime refuses a directory substituted after creation", async () => {
  const runtime = await createPrivateRuntime();
  const movedDirectory = `${runtime.directory}.moved`;
  const victimSentinel = `${runtime.directory}/victim-sentinel`;

  try {
    await rename(runtime.directory, movedDirectory);
    await mkdir(runtime.directory, { mode: 0o700 });
    await writeFile(victimSentinel, "victim");

    await assert.rejects(removePrivateRuntime(runtime), /identity changed/);
    assert.equal(await readFile(victimSentinel, "utf8"), "victim");
  } finally {
    await rm(runtime.directory, { recursive: true, force: true });
    await rm(movedDirectory, { recursive: true, force: true });
  }
});

test("waitUntil times out for a running child and reports unexpected child exits", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "sleeper",
  });

  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "never ready",
        timeoutMs: 30,
        intervalMs: 5,
        processes: [sleeper],
      }),
      /timed out/,
    );
  } finally {
    await stopOwnedProcess(sleeper);
  }

  const worker = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "worker",
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "never ready",
        timeoutMs: 1_000,
        intervalMs: 5,
        processes: [worker],
      }),
      /worker exited with 7/,
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("chooseRunError preserves a workflow failure over cleanup failures", () => {
  const workflowError = new Error("workflow failed");
  const cleanupError = new Error("cleanup failed");

  assert.strictEqual(chooseRunError(workflowError, [cleanupError]), workflowError);
  assert.strictEqual(chooseRunError(null, [cleanupError]), cleanupError);
  assert.strictEqual(chooseRunError(null, []), null);
});
