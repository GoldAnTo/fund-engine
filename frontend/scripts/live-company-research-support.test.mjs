import assert from "node:assert/strict";
import test from "node:test";

import {
  assertLoopbackUrl,
  assertWorkspace,
  buildVerifierEnvironment,
  parseVerifierArgs,
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
  };

  const environment = buildVerifierEnvironment({
    databaseUrl: "sqlite:////private/live.sqlite",
    token: "test-only-token",
    backendUrl: "http://127.0.0.1:41001",
  }, hostEnvironment);

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
});

test("assertLoopbackUrl permits only credential-free HTTP on 127.0.0.1", () => {
  const accepted = "http://127.0.0.1:41001/api/underwriting/v1/product/objects";
  assert.equal(assertLoopbackUrl(accepted), accepted);

  for (const url of [
    "https://example.com/a",
    "http://localhost.evil.test/a",
    "http://user:pass@127.0.0.1/a",
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
      schema_version: "underwriting.v1",
      id: "company-1",
      object_id: "company-1",
    },
    preparation: {
      schema_version: "underwriting.v1",
      status: "awaiting_evidence_review",
      progress: 25,
      current_step: "research_gaps",
    },
    draft: {
      schema_version: "underwriting.v1",
      lock_version: 2,
    },
    selected_revision: null,
    change_summary: {
      artifact_versions: {
        evidence_index: 1,
        research_gaps: 1,
      },
      reviewed_fact_count: 0,
    },
    artifacts: [],
    modules: [],
  };

  assert.strictEqual(assertWorkspace(workspace, expected), workspace);

  const projectDrift = structuredClone(workspace);
  projectDrift.project_id = "project-2";
  assert.throws(() => assertWorkspace(projectDrift, expected), /project identity/);

  const progressDrift = structuredClone(workspace);
  progressDrift.preparation.progress = 24;
  assert.throws(() => assertWorkspace(progressDrift, expected), /progress/);
});
