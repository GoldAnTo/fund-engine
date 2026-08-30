import assert from "node:assert/strict";
import { lstat, mkdir, readdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import test from "node:test";

import {
  assertLoopbackUrl,
  assertProcessesRunning,
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

test("removePrivateRuntime leaves a victim created after its atomic cleanup claim", async () => {
  const runtime = await createPrivateRuntime();
  const victimSentinel = `${runtime.directory}/victim-sentinel`;
  await Promise.all(Array.from({ length: 200 }, (_, index) =>
    writeFile(`${runtime.directory}/owned-${index}`, "owned")));

  const cleanup = removePrivateRuntime(runtime);
  try {
    let claimed = false;
    for (let attempt = 0; attempt < 100 && !claimed; attempt += 1) {
      const entries = await readdir(runtime.parent);
      claimed = entries.some((entry) => entry.startsWith(".fund-engine-live-company-research-cleanup-"));
      if (!claimed) await new Promise((resolve) => setTimeout(resolve, 1));
    }
    assert.equal(claimed, true, "cleanup must atomically quarantine its owned directory");
    await mkdir(runtime.directory, { mode: 0o700 });
    await writeFile(victimSentinel, "victim");
    await cleanup;
    assert.equal(await readFile(victimSentinel, "utf8"), "victim");
  } finally {
    await cleanup.catch(() => {});
    await rm(runtime.directory, { recursive: true, force: true });
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

test("removePrivateRuntime rejects forged handles without touching their targets", async () => {
  const runtime = await createPrivateRuntime();
  const sentinel = `${runtime.directory}/owned-sentinel`;
  await writeFile(sentinel, "owned");
  const forged = { ...runtime };

  try {
    assert.equal(Object.isFrozen(runtime), true);
    await assert.rejects(removePrivateRuntime(forged), /identity changed/);
    assert.equal(await readFile(sentinel, "utf8"), "owned");
  } finally {
    await removePrivateRuntime(runtime);
  }
});

test("waitUntil enforces its deadline when a probe never settles", { timeout: 500 }, async () => {
  await assert.rejects(
    waitUntil(() => new Promise(() => {}), {
      label: "stalled probe",
      timeoutMs: 30,
      intervalMs: 5,
    }),
    /stalled probe timed out/,
  );
});

test("waitUntil fails when a child exits while a probe later reports ready", async () => {
  const worker = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => process.exit(7), 5)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "worker",
  });

  try {
    await assert.rejects(
      waitUntil(async () => {
        await new Promise((resolve) => setTimeout(resolve, 200));
        return true;
      }, {
        label: "ready",
        timeoutMs: 1_000,
        processes: [worker],
      }),
      /worker exited with 7/,
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("supervision diagnostics bound untrusted labels, names, and probe errors", async () => {
  const huge = `line\n${"x".repeat(20_000)}`;
  await assert.rejects(
    waitUntil(() => {
      throw new Error(huge);
    }, {
      label: huge,
      timeoutMs: 5,
      intervalMs: 1,
    }),
    (error) => {
      assert.ok(error.message.length <= 16_384);
      assert.equal(error.message.includes("\n"), false);
      return true;
    },
  );

  const worker = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: huge,
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [worker],
      }),
      (error) => {
        assert.ok(error.message.length <= 16_384);
        assert.equal(error.message.includes("\n"), false);
        return true;
      },
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("stopOwnedProcess rejects when its exact live child refuses termination", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "sleeper",
  });
  const kill = sleeper.child.kill.bind(sleeper.child);
  sleeper.child.kill = () => false;
  const forcedRelease = setTimeout(() => kill("SIGKILL"), 50);

  try {
    await assert.rejects(stopOwnedProcess(sleeper), /refused SIGTERM/);
  } finally {
    clearTimeout(forcedRelease);
    sleeper.child.kill = kill;
    if (!sleeper.exited) kill("SIGKILL");
    await sleeper.exitPromise;
  }
  assert.doesNotThrow(() => assertProcessesRunning([sleeper]));
});

test("supervision fails closed for spawn errors and signal-only exits", async () => {
  const missing = startOwnedProcess("/definitely-not-a-live-company-research-command", [], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "missing",
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [missing],
      }),
      /missing exited with ENOENT/,
    );
  } finally {
    await stopOwnedProcess(missing);
  }

  const signaled = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "signaled",
  });
  await new Promise((resolve) => signaled.child.once("spawn", resolve));
  signaled.child.kill("SIGTERM");
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [signaled],
      }),
      /signaled exited with SIGTERM/,
    );
  } finally {
    await stopOwnedProcess(signaled);
  }
});
