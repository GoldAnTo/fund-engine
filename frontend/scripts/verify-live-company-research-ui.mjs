#!/usr/bin/env node
/** Start the isolated live Company Research stack and create Alphabet through the UI. */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, lstat, readFile, realpath } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

import {
  assertLoopbackUrl,
  assertExactReviewSuccessor,
  assertModelWorkspace,
  assertProcessRunningBeforeIntentionalStop,
  assertProcessesRunning,
  assertSameArtifactHead,
  assertAlphabetIdentityBinding,
  assertWorkspace,
  buildVerifierEnvironment,
  chooseRunError,
  closeOwnedBrowser,
  createBrowserFailureCollector,
  createPendingObserverTracker,
  createPrivateRuntime,
  createSequencedResponseQueue,
  createTrafficAudit,
  newOwnedBrowserContext,
  parseVerifierArgs,
  repositoryPythonVenvRoots,
  removePrivateRuntime,
  startOwnedBrowser,
  startOwnedProcess,
  stopOwnedProcess,
  waitUntil,
} from "./live-company-research-support.mjs";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");
const backend = path.join(root, "backend");
const cleanupHelperPath = path.join(backend, "app", "scripts", "remove_private_runtime_contents.py");
const alphabetManifestPath = path.join(
  backend, "app", "underwriting", "fixtures", "alphabet_golden_case", "manifest.json",
);
const foundationManifestPath = path.join(
  backend, "app", "underwriting", "fixtures", "product_foundation", "manifest.json",
);
const token = "live-company-research-verifier-token";
const alphabetExternalKey = "US:ALPHABET:COMPANY";
const initializationPath = "/api/underwriting/v1/product/company-research/initializations";
const previewPath = "/api/underwriting/v1/product/company-research/preview";
const searchPath = "/api/underwriting/v1/product/objects";
const CLEANUP_TIMEOUT_MS = 10_000;
const CLOSED_ANSWERABILITY_MESSAGE = "当前正式证据不足，不形成投资方向、置信度、目标价或预期回报。";
const HUMAN_MEMO = "Current formal evidence is insufficient.";
const PASS_LINE = "PASS: default frontend completed live Alphabet company research through reviewed evidence, frozen revision replay, and verified Markdown export";
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const FROZEN_ARTIFACT_KINDS = Object.freeze([
  "evidence_index",
  "research_gaps",
  "business_map",
  "driver_map",
  "financial_bridge",
  "scenario_set",
  "judgment_context",
  "memo",
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function jsonValuesEqual(left, right) {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((item, index) => jsonValuesEqual(item, right[index]));
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) =>
      key === rightKeys[index] && jsonValuesEqual(left[key], right[key]));
}

function canonicalHash(value) {
  const canonicalize = (item) => Array.isArray(item)
    ? item.map(canonicalize)
    : isRecord(item)
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonicalize(item[key])]))
      : item;
  return createHash("sha256").update(JSON.stringify(canonicalize(value))).digest("hex");
}

function artifactDescriptor(artifact) {
  return {
    schema_version: artifact.schema_version,
    kind: artifact.kind,
    id: artifact.id,
    version: artifact.version,
    input_hash: artifact.input_hash,
    content_hash: artifact.content_hash,
  };
}

function exactArtifactDescriptors(workspace) {
  if (!Array.isArray(workspace?.artifacts) || workspace.artifacts.length !== FROZEN_ARTIFACT_KINDS.length) {
    throw new Error("prepublication workspace artifact cardinality mismatch");
  }
  const byKind = new Map(workspace.artifacts.map((artifact) => [artifact?.kind, artifact]));
  if (byKind.size !== FROZEN_ARTIFACT_KINDS.length
    || FROZEN_ARTIFACT_KINDS.some((kind) => !byKind.has(kind))) {
    throw new Error("prepublication workspace artifact identities mismatch");
  }
  return FROZEN_ARTIFACT_KINDS.map((kind) => artifactDescriptor(byKind.get(kind)));
}

function assertExactFrozenDescriptors(actual, expected, label) {
  if (!jsonValuesEqual(actual, expected)) throw new Error(`${label} frozen artifact descriptors mismatch`);
  return actual;
}

function savedEvidenceDecisions(evidence) {
  const facts = evidence?.payload?.facts;
  if (!Array.isArray(facts) || facts.length === 0) throw new Error("saved evidence decisions are absent");
  const decisions = facts.map((fact) => ({ fact_key: fact?.fact_key, review_decision: fact?.review_decision }));
  if (decisions.some((decision) => typeof decision.fact_key !== "string"
    || decision.review_decision !== "confirmed")
    || new Set(decisions.map((decision) => decision.fact_key)).size !== decisions.length) {
    throw new Error("saved evidence decisions are invalid");
  }
  return decisions;
}

function assertSavedEvidenceDecisions(workspace, expected, label) {
  const evidence = workspace?.artifacts?.find((artifact) => artifact?.kind === "evidence_index");
  if (!jsonValuesEqual(savedEvidenceDecisions(evidence), expected)) {
    throw new Error(`${label} evidence decisions changed`);
  }
  return evidence;
}

function storedMemoResearchGaps(workspace, memo) {
  const gaps = workspace?.artifacts?.find((artifact) => artifact?.kind === "research_gaps")?.payload?.gaps;
  const modules = workspace?.artifacts?.find((artifact) => artifact?.kind === "business_map")?.payload?.modules;
  const nextEvents = workspace?.artifacts?.find((artifact) => artifact?.kind === "judgment_context")
    ?.payload?.next_verification_events;
  const gapKeys = memo?.payload?.gap_keys;
  if (!Array.isArray(gaps) || !Array.isArray(modules) || !Array.isArray(nextEvents)
    || !Array.isArray(gapKeys) || gapKeys.length !== nextEvents.length) {
    throw new Error("confirmed memo stored gap codec mismatch");
  }
  const sourceGaps = new Map(gaps.map((gap) => [gap?.gap_key, gap]));
  const moduleByGap = new Map();
  for (const module of modules) {
    if (!Array.isArray(module?.gap_refs)) throw new Error("confirmed memo stored gap codec mismatch");
    for (const gapKey of module.gap_refs) {
      if (moduleByGap.has(gapKey)) throw new Error("confirmed memo stored gap codec mismatch");
      moduleByGap.set(gapKey, module.module_key);
    }
  }
  const generatedMessage = (gapKey) => {
    for (const [prefix, text] of [
      ["builder_generated_operating_baseline_missing_", "Reviewed operating baseline is missing: "],
      ["builder_generated_missing_module_evidence_", "No confirmed evidence or governed gap covers "],
      ["builder_generated_operating_driver_missing_", "Confirmed numeric input is missing for "],
    ]) if (gapKey.startsWith(prefix)) return `${text}${gapKey.slice(prefix.length)}`;
    return null;
  };
  return gapKeys.map((gapKey, index) => {
    const source = sourceGaps.get(gapKey);
    const moduleKey = moduleByGap.get(gapKey);
    const message = nextEvents[index];
    if (typeof moduleKey !== "string" || typeof message !== "string") {
      throw new Error("confirmed memo stored gap codec mismatch");
    }
    if (source) {
      if (source.business_module !== moduleKey || source.reason !== message) {
        throw new Error("confirmed memo stored gap codec mismatch");
      }
      return { code: gapKey, module_key: moduleKey, severity: "high", message };
    }
    if (generatedMessage(gapKey) !== message) throw new Error("confirmed memo stored gap codec mismatch");
    return { code: gapKey, module_key: moduleKey, severity: "critical", message };
  });
}

function assertExactConfirmedMemo(machineMemo, confirmedMemo, markdown, workspace) {
  if (!isRecord(machineMemo) || !isRecord(confirmedMemo)
    || machineMemo.kind !== "memo" || confirmedMemo.kind !== "memo"
    || confirmedMemo.schema_version !== machineMemo.schema_version
    || confirmedMemo.project_id !== machineMemo.project_id
    || confirmedMemo.id === machineMemo.id
    || confirmedMemo.version !== machineMemo.version + 1
    || confirmedMemo.input_hash !== machineMemo.input_hash
    || !jsonValuesEqual(confirmedMemo.source_refs, machineMemo.source_refs)) {
    throw new Error("confirmed memo successor identity mismatch");
  }
  const expectedPayload = {
    ...structuredClone(machineMemo.payload),
    candidate_status: "human_confirmed",
    reviewer: "human:local-user",
    markdown,
  };
  if (!jsonValuesEqual(confirmedMemo.payload, expectedPayload)
    || confirmedMemo.payload.assessment_status !== "not_answerable"
    || Object.hasOwn(confirmedMemo.payload, "direction")
    || Object.hasOwn(confirmedMemo.payload, "confidence")
    || Object.hasOwn(confirmedMemo.payload, "target_value")
    || Object.hasOwn(confirmedMemo.payload, "value_range")
    || Object.hasOwn(confirmedMemo.payload, "return_range")) {
    throw new Error("confirmed memo full codec successor mismatch");
  }
  const storedPayload = {
    ...expectedPayload,
    research_gaps: storedMemoResearchGaps(workspace, machineMemo),
  };
  const expectedHash = canonicalHash({
    schema_version: "company-research-artifact.v1",
    project_id: confirmedMemo.project_id,
    kind: "memo",
    version: confirmedMemo.version,
    supersedes_id: machineMemo.id,
    parent_content_hash: machineMemo.content_hash,
    input_hash: confirmedMemo.input_hash,
    payload: storedPayload,
    source_refs: confirmedMemo.source_refs,
  });
  if (confirmedMemo.content_hash !== expectedHash) throw new Error("confirmed memo content hash mismatch");
  return confirmedMemo;
}

function assertExactPublicationProjection(actual, preview, label) {
  const fields = [
    "company", "securities", "cutoff_at", "historical_basis_id", "historical_basis_content_hash",
    "strategy_version", "model_version", "assessment", "value_range", "return_range", "blockers",
    "strongest_counterevidence", "next_verification_events", "memo_markdown", "artifacts",
  ];
  if (fields.some((field) => !jsonValuesEqual(actual?.[field], preview?.[field]))) {
    throw new Error(`${label} publication projection mismatch`);
  }
  return actual;
}

function retainPrimaryFailure(promise) {
  void promise.catch(() => {});
  return promise;
}

async function waitForProcessAwareOutcome(promise, { label, deadline, processes }) {
  let outcome = null;
  void promise.then(
    (value) => { outcome = { kind: "fulfilled", value }; },
    (error) => { outcome = { kind: "rejected", error }; },
  );
  const settled = await waitUntil(() => outcome, {
    label,
    timeoutMs: remaining(deadline, label),
    processes,
  });
  if (settled.kind === "rejected") throw settled.error;
  return settled.value;
}

async function waitForProcessAwareVisible(locator, { label, deadline, processes }) {
  return waitUntil(() => locator.isVisible(), {
    label,
    timeoutMs: remaining(deadline, label),
    processes,
  });
}

function componentTail(owned) {
  if (!owned) return "";
  const raw = owned.stderr.length > 0 ? owned.stderr.toString("utf8") : owned.stdout.toString("utf8");
  const sanitized = raw
    .replaceAll(token, "[redacted]")
    .replace(/authorization\s*[:=]\s*[^\s,;]+/giu, "authorization=[redacted]")
    .replace(/[\u0000-\u001F\u007F]/gu, " ");
  const tail = Array.from(sanitized).slice(-180).join("").trim();
  return `\n${owned.name} output tail: ${tail || "[empty]"}`;
}

function cleanupFailure(label, error) {
  const raw = error instanceof Error ? error.message : "cleanup failed";
  const sanitized = raw
    .replaceAll(token, "[redacted]")
    .replace(/[^\s]*fund-engine-live-company-research-[^\s:]*/gu, "[private-runtime]")
    .replace(/[^\s]*company-research\.sqlite/gu, "[private-database]")
    .replace(/authorization\s*[:=]\s*[^\s,;]+/giu, "authorization=[redacted]")
    .replace(/\b(env(?:ironment)?|headers?|(?:request|response)?\s*body)\s*[:=]\s*(?:\{[^}]*\}|\[[^\]]*\]|"[^"]*"|'[^']*'|[^\s,;]+)/giu, "$1=[redacted]")
    .replace(/[\u0000-\u001F\u007F]/gu, " ");
  const bounded = Array.from(sanitized).slice(0, 180).join("");
  return new Error(`${label}: ${bounded}`, { cause: error });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("failed to allocate a loopback port")));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function trustedMode(stat) {
  return (stat.mode & 0o022) === 0
    && (typeof process.getuid !== "function" || stat.uid === process.getuid());
}

function fileIdentity(stat) {
  return Object.freeze({
    dev: stat.dev, ino: stat.ino, uid: stat.uid, mode: stat.mode & 0o777,
  });
}

function sameFileIdentity(stat, expected) {
  return stat.dev === expected.dev && stat.ino === expected.ino
    && stat.uid === expected.uid && (stat.mode & 0o777) === expected.mode;
}

async function validatePythonLauncher(candidate, venvRoot, label) {
  const absolute = path.resolve(process.cwd(), candidate);
  const absoluteRoot = path.resolve(venvRoot);
  try {
    const [rootStat, binStat, launcherStat, canonicalRoot, canonical] = await Promise.all([
      lstat(absoluteRoot),
      lstat(path.join(absoluteRoot, "bin")),
      lstat(absolute),
      realpath(absoluteRoot),
      realpath(absolute),
    ]);
    const canonicalStat = await lstat(canonical);
    await access(absolute, fsConstants.X_OK);
    if (canonicalRoot !== absoluteRoot
      || absolute !== path.join(absoluteRoot, "bin", "python")
      || !rootStat.isDirectory() || rootStat.isSymbolicLink() || !trustedMode(rootStat)
      || !binStat.isDirectory() || binStat.isSymbolicLink() || !trustedMode(binStat)
      || (!launcherStat.isFile() && !launcherStat.isSymbolicLink())
      || !canonicalStat.isFile() || canonicalStat.isSymbolicLink()
      || (canonicalStat.mode & 0o111) === 0 || !trustedMode(canonicalStat)) {
      throw new Error("invalid executable identity");
    }
    return Object.freeze({
      launcher: absolute,
      canonical,
      venvRoot: absoluteRoot,
      launcherIdentity: fileIdentity(launcherStat),
      canonicalIdentity: fileIdentity(canonicalStat),
    });
  } catch {
    throw new Error(`${label} is not a validated Python executable`);
  }
}

async function resolvePython() {
  const allowedRoots = repositoryPythonVenvRoots(root);
  if (typeof process.env.PYTHON === "string" && process.env.PYTHON.length > 0) {
    const explicit = path.resolve(process.cwd(), process.env.PYTHON);
    const allowedRoot = allowedRoots.find((candidate) =>
      explicit === path.join(path.resolve(candidate), "bin", "python"));
    if (!allowedRoot) throw new Error("configured PYTHON is outside the trusted repository venv");
    return validatePythonLauncher(explicit, allowedRoot, "configured PYTHON");
  }
  for (const venvRoot of allowedRoots) {
    try {
      return await validatePythonLauncher(
        path.join(venvRoot, "bin", "python"), venvRoot, "repository backend Python",
      );
    } catch {
      // Try only the repository-owned fallback locations listed above.
    }
  }
  throw new Error("repository backend Python is not a validated executable");
}

async function assertPythonIdentity(python) {
  const [launcherStat, canonicalStat, canonical] = await Promise.all([
    lstat(python.launcher), lstat(python.canonical), realpath(python.launcher),
  ]);
  if (canonical !== python.canonical
    || !sameFileIdentity(launcherStat, python.launcherIdentity)
    || !sameFileIdentity(canonicalStat, python.canonicalIdentity)) {
    throw new Error("trusted Python identity changed");
  }
}

function probePython(python) {
  const probe = [
    "import importlib, pathlib, sys",
    "expected = pathlib.Path(sys.argv[1]).resolve()",
    "assert pathlib.Path(sys.prefix).resolve() == expected",
    "[importlib.import_module(name) for name in ('sqlalchemy', 'uvicorn', 'fastapi')]",
    "backend = pathlib.Path(sys.argv[2]).resolve()",
    "sys.path.insert(0, str(backend))",
    "importlib.import_module('app.main')",
  ].join("; ");
  try {
    execFileSync(python.launcher, ["-I", "-c", probe, python.venvRoot, backend], {
      cwd: backend,
      env: {
        APP_ENV: "test",
        DATABASE_URL: "sqlite:///:memory:",
        RESEARCH_TENANT_TOKENS: "{}",
        NO_PROXY: "127.0.0.1,localhost",
        no_proxy: "127.0.0.1,localhost",
      },
      stdio: ["ignore", "ignore", "ignore"],
      timeout: 10_000,
    });
  } catch {
    throw new Error("trusted Python dependency probe failed");
  }
}

async function alphabetFixtureCutoff() {
  let manifest;
  try {
    manifest = JSON.parse(await readFile(alphabetManifestPath, "utf8"));
  } catch {
    throw new Error("authenticated Alphabet fixture manifest is unreadable");
  }
  if (manifest?.schema_version !== "alphabet.golden-case.manifest.v1"
    || typeof manifest.cutoff !== "string" || !Number.isFinite(Date.parse(manifest.cutoff))) {
    throw new Error("authenticated Alphabet fixture cutoff is invalid");
  }
  return manifest.cutoff;
}

async function alphabetFoundation() {
  try {
    return JSON.parse(await readFile(foundationManifestPath, "utf8"));
  } catch {
    throw new Error("authenticated product foundation manifest is unreadable");
  }
}

async function responseJson(response) {
  const method = response.request().method();
  const pathname = new URL(response.url()).pathname;
  if (!response.ok()) throw new Error(`browser API ${response.status()} ${method} ${pathname}`);
  try {
    return await response.json();
  } catch {
    throw new Error(`browser API returned invalid JSON: ${method} ${pathname}`);
  }
}

function remaining(deadline, label) {
  const milliseconds = deadline - Date.now();
  if (milliseconds <= 0) throw new Error(`${label} timed out`);
  return milliseconds;
}

function throwBrowserFailures(failures) {
  failures.throwIfAny();
}

function createWorkspaceResponseCollector(page, failures, observers) {
  const queue = createSequencedResponseQueue();
  const requests = new WeakMap();
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== "GET" || !pathname.endsWith("/workspace")
      || !pathname.includes("/company-research/projects/")) return;
    requests.set(request, queue.begin({ pathname }));
  });
  page.on("response", (response) => {
    const request = response.request();
    const pathname = new URL(response.url()).pathname;
    if (request.method() !== "GET" || !pathname.endsWith("/workspace")
      || !pathname.includes("/company-research/projects/")) return;
    const token = requests.get(request);
    observers.track(async () => {
      try {
        if (!token) throw new Error("browser workspace response lacks request initiation");
        const workspace = await responseJson(response);
        queue.complete(token, { pathname, workspace });
      } catch (error) {
        failures.record(error);
      }
    });
  });
  return Object.freeze({
    cursor() { return queue.cursor(); },
    takeAfter(after, predicate) {
      return queue.takeAfter(after, ({ value }) => predicate(value))?.workspace ?? null;
    },
  });
}

async function waitForBrowserWorkspace({
  collector, after, projectId, status, progress, reviewedCount, evidenceArtifact, deadline, processes,
}) {
  const workspacePath = projectId
    ? `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/workspace`
    : null;
  return waitUntil(async () => collector.takeAfter(after, ({ pathname, workspace }) => {
    const evidenceHeads = workspace?.artifacts?.filter((artifact) => artifact?.kind === "evidence_index");
    let exactEvidence = evidenceArtifact === undefined;
    if (!exactEvidence && evidenceHeads?.length === 1) {
      try {
        assertSameArtifactHead(evidenceArtifact, evidenceHeads[0]);
        exactEvidence = true;
      } catch {
        exactEvidence = false;
      }
    }
    return (workspacePath === null || pathname === workspacePath)
      && workspace?.preparation?.status === status
      && workspace?.preparation?.progress === progress
      && (reviewedCount === undefined
        || workspace?.change_summary?.reviewed_fact_count === reviewedCount)
      && exactEvidence;
  }), {
    label: `browser workspace ${status}/${progress}`,
    timeoutMs: remaining(deadline, `browser workspace ${status}/${progress}`),
    processes,
  });
}

function governedEvidenceArtifact(workspace) {
  const evidenceArtifacts = workspace.artifacts?.filter((artifact) => artifact?.kind === "evidence_index");
  if (evidenceArtifacts?.length !== 1) throw new Error("evidence workspace must expose exactly one evidence artifact");
  const evidence = evidenceArtifacts[0];
  const facts = evidence?.payload?.facts;
  if (!Array.isArray(facts) || facts.length === 0) throw new Error("authenticated evidence artifact has no governed facts");
  const factKeys = facts.map((fact) => fact?.fact_key);
  if (factKeys.some((factKey) => typeof factKey !== "string" || factKey.length === 0)
    || new Set(factKeys).size !== factKeys.length) {
    throw new Error("authenticated evidence artifact fact identities are invalid");
  }
  if (facts.some((fact) => (fact.review_decision ?? null) !== null)) {
    throw new Error("authenticated evidence artifact contains a pre-reviewed fact");
  }
  return { evidence, factKeys };
}

function governedResearchGapsArtifact(workspace) {
  const artifacts = workspace.artifacts?.filter((artifact) => artifact?.kind === "research_gaps");
  if (artifacts?.length !== 1) {
    throw new Error("evidence workspace must expose exactly one research gaps artifact");
  }
  return artifacts[0];
}

async function finalizeOwnedRuntime({
  browser, browserFailures, pendingObservers, assertTraffic, worker, vite, api, runtime,
}) {
  const cleanupErrors = [];
  const record = (label, error) => cleanupErrors.push(cleanupFailure(label, error));
  const processes = [worker, vite, api].filter((owned) => owned !== null && owned !== undefined);
  try {
    assertProcessesRunning(processes);
  } catch (error) {
    record("pre-cleanup component health failure", error);
  }
  if (browser) {
    try {
      await closeOwnedBrowser(browser, { timeoutMs: CLEANUP_TIMEOUT_MS });
    } catch (error) {
      record("browser cleanup failure", error);
    }
  }
  if (pendingObservers) {
    try {
      await pendingObservers.drain({ timeoutMs: CLEANUP_TIMEOUT_MS });
    } catch (error) {
      record("browser observer cleanup failure", error);
    }
  }
  if (assertTraffic) {
    try {
      assertTraffic();
    } catch (error) {
      record("traffic cleanup assertion failure", error);
    }
  }
  if (browserFailures) {
    try {
      browserFailures.throwIfAny();
    } catch (error) {
      record("browser failure cleanup assertion", error);
    }
  }
  for (const [index, owned] of processes.entries()) {
    if (!owned) continue;
    try {
      assertProcessesRunning(processes.slice(index));
    } catch (error) {
      record(`${owned.name} pre-stop component health failure`, error);
    }
    try {
      await stopOwnedProcess(owned);
    } catch (error) {
      record(`${owned.name} cleanup stop failure`, error);
    }
  }
  if (runtime) {
    try {
      await removePrivateRuntime(runtime);
    } catch (error) {
      record("private runtime cleanup failure", error);
    }
  }
  return cleanupErrors;
}

async function runBootstrap(python, args, { env, deadline, label }) {
  await assertPythonIdentity(python);
  try {
    execFileSync(python.launcher, args, {
      cwd: backend,
      env,
      stdio: ["ignore", "ignore", "pipe"],
      timeout: Math.min(30_000, remaining(deadline, label)),
    });
  } catch {
    throw new Error(`${label} failed`);
  }
}

async function runBrowserWorkflow({
  page, uiBase, audit, failures, deadline, runningProcesses, foundation,
  pendingObservers, browserRuntimeDirectory, stopWorkerForReview, restartWorker,
}) {
  const workspaceResponses = createWorkspaceResponseCollector(page, failures, pendingObservers);
  await page.goto(`${uiBase}/research/new`, {
    waitUntil: "networkidle",
    timeout: remaining(deadline, "new research navigation"),
  });
  throwBrowserFailures(failures);

  await page.getByLabel("搜索公司、证券或行业").fill("Alphabet");
  const searchResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === searchPath,
  { timeout: remaining(deadline, "Alphabet search") });
  await page.getByRole("button", { name: "搜索对象" }).click();
  const search = await responseJson(await searchResponsePromise);
  throwBrowserFailures(failures);
  if (!Array.isArray(search?.items)) throw new Error("live company search returned an invalid result set");
  const alphabetMatches = search.items.filter((item) => item?.external_key === alphabetExternalKey);
  if (alphabetMatches.length !== 1) throw new Error("live company search did not return exactly one Alphabet company");
  const alphabet = alphabetMatches[0];
  if (alphabet.kind !== "company" || typeof alphabet.object_id !== "string"
    || alphabet.canonical_name !== "Alphabet Inc.") {
    throw new Error("Alphabet company identity mismatch");
  }

  const previewResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === previewPath,
  { timeout: remaining(deadline, "Alphabet preview") });
  await page.getByRole("button", { name: "研究 Alphabet" }).click();
  const preview = await responseJson(await previewResponsePromise);
  const boundAlphabet = assertAlphabetIdentityBinding({ foundation, search, preview });
  if (boundAlphabet.object_id !== alphabet.object_id) throw new Error("Alphabet company binding changed");
  await page.getByRole("heading", { name: "确认默认研究方案" }).waitFor();
  await page.getByText("关联证券：GOOG Class C；GOOGL Class A", { exact: false }).waitFor();
  throwBrowserFailures(failures);

  const initializeResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === initializationPath,
  { timeout: remaining(deadline, "company research initialization") });
  const evidenceWorkspaceCursor = workspaceResponses.cursor();
  await page.getByRole("button", { name: "开始研究" }).click();
  const initialized = await responseJson(await initializeResponsePromise);
  if (initialized?.company_id !== alphabet.object_id
    || initialized?.preparation?.project_id !== initialized?.project_id
    || typeof initialized?.project_id !== "string") {
    throw new Error("initialization company or project binding mismatch");
  }
  const expectedProjectUrl = `${uiBase}/research/projects/${encodeURIComponent(initialized.project_id)}`;
  await page.waitForURL((url) => url.href === expectedProjectUrl, {
    timeout: remaining(deadline, "created project navigation"),
  });
  if (page.url() !== expectedProjectUrl) throw new Error("created project route mismatch");
  await page.getByRole("heading", { name: "研究工作台" }).waitFor({
    timeout: remaining(deadline, "created project workbench"),
  });
  const projectId = initialized.project_id;
  const evidenceWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: evidenceWorkspaceCursor,
    projectId,
    status: "awaiting_evidence_review",
    progress: 25,
    reviewedCount: 0,
    deadline,
    processes: runningProcesses(),
  });
  assertWorkspace(evidenceWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "awaiting_evidence_review",
    progress: 25,
  });
  if (evidenceWorkspace.change_summary?.reviewed_fact_count !== 0) {
    throw new Error("evidence workspace must begin with zero reviewed facts");
  }
  let { evidence, factKeys } = governedEvidenceArtifact(evidenceWorkspace);
  let researchGaps = governedResearchGapsArtifact(evidenceWorkspace);

  await stopWorkerForReview();
  assertProcessesRunning(runningProcesses());
  const stableWorkspaceCursor = workspaceResponses.cursor();
  await page.reload({
    waitUntil: "networkidle",
    timeout: remaining(deadline, "stopped-worker workspace reload"),
  });
  const stableEvidenceWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: stableWorkspaceCursor,
    projectId,
    status: "awaiting_evidence_review",
    progress: 25,
    reviewedCount: 0,
    evidenceArtifact: evidence,
    deadline,
    processes: runningProcesses(),
  });
  assertWorkspace(stableEvidenceWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "awaiting_evidence_review",
    progress: 25,
  });
  const stableEvidence = governedEvidenceArtifact(stableEvidenceWorkspace).evidence;
  if (stableEvidenceWorkspace.change_summary?.reviewed_fact_count !== 0) {
    throw new Error("stopped worker did not preserve the exact evidence-review boundary");
  }
  assertSameArtifactHead(evidence, stableEvidence);
  const stableResearchGaps = governedResearchGapsArtifact(stableEvidenceWorkspace);
  assertSameArtifactHead(researchGaps, stableResearchGaps);
  evidence = stableEvidence;
  researchGaps = stableResearchGaps;
  await page.getByRole("button", { name: "来源、事实与缺口" }).click();

  const reviewPath = `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/evidence-reviews`;
  const reviewResponses = [];
  let finalReviewWorkspace = null;
  for (const [index, factKey] of factKeys.entries()) {
    const previousEvidence = evidence;
    const reviewResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === reviewPath,
    { timeout: remaining(deadline, `evidence review ${factKey}`) });
    const reviewWorkspaceCursor = workspaceResponses.cursor();
    await page.getByRole("button", { name: `确认事实 ${factKey}`, exact: true }).click();
    const reviewed = await responseJson(await reviewResponsePromise);
    reviewResponses.push(reviewed);
    evidence = assertExactReviewSuccessor(previousEvidence, reviewed?.evidence_artifact, factKey);
    const isFinalFact = index === factKeys.length - 1;
    const nextWorkspace = await waitForBrowserWorkspace({
      collector: workspaceResponses,
      after: reviewWorkspaceCursor,
      projectId,
      status: isFinalFact ? "building_model" : "awaiting_evidence_review",
      progress: 25,
      reviewedCount: index + 1,
      evidenceArtifact: evidence,
      deadline,
      processes: runningProcesses(),
    });
    assertWorkspace(nextWorkspace, {
      projectId,
      companyId: alphabet.object_id,
      status: isFinalFact ? "building_model" : "awaiting_evidence_review",
      progress: 25,
    });
    if (nextWorkspace.change_summary?.reviewed_fact_count !== index + 1) {
      throw new Error("reviewed fact count did not advance exactly once");
    }
    const workspaceEvidence = nextWorkspace.artifacts?.find((artifact) => artifact?.kind === "evidence_index");
    assertExactReviewSuccessor(previousEvidence, workspaceEvidence, factKey);
    assertSameArtifactHead(evidence, workspaceEvidence);
    await page.getByText(`事实 ${factKey} 已确认，证据版本 ${evidence.version}`, { exact: true }).waitFor({
      timeout: remaining(deadline, `visible evidence review ${factKey}`),
    });
    if (isFinalFact) finalReviewWorkspace = nextWorkspace;
    throwBrowserFailures(failures);
    assertProcessesRunning(runningProcesses());
  }
  if (reviewResponses.length !== factKeys.length || finalReviewWorkspace === null
    || finalReviewWorkspace.preparation.status !== "building_model"
    || finalReviewWorkspace.change_summary.reviewed_fact_count !== factKeys.length) {
    throw new Error("final evidence review did not enter the exact model-build boundary");
  }

  const modelWorkspaceCursor = workspaceResponses.cursor();
  await restartWorker();
  const modelWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: modelWorkspaceCursor,
    projectId,
    status: "awaiting_judgment_review",
    progress: 85,
    reviewedCount: factKeys.length,
    evidenceArtifact: evidence,
    deadline,
    processes: runningProcesses(),
  });
  assertWorkspace(modelWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "awaiting_judgment_review",
    progress: 85,
  });
  assertModelWorkspace(modelWorkspace, { evidenceArtifact: evidence, researchGapsArtifact: researchGaps });
  if (modelWorkspace.change_summary?.reviewed_fact_count !== factKeys.length) {
    throw new Error("model workspace reviewed fact count differs from authenticated evidence");
  }
  await page.getByRole("button", { name: "概览与当前判断" }).click();
  await page.getByText(CLOSED_ANSWERABILITY_MESSAGE, { exact: true }).waitFor({
    timeout: remaining(deadline, "closed answerability message"),
  });
  throwBrowserFailures(failures);
  assertProcessesRunning(runningProcesses());

  const task5Processes = () => runningProcesses();
  const assertTask5Health = () => assertProcessesRunning(task5Processes());
  const task5Outcome = (promise, label) => waitForProcessAwareOutcome(promise, {
    label,
    deadline,
    processes: task5Processes(),
  });
  const task5Visible = (locator, label) => waitForProcessAwareVisible(locator, {
    label,
    deadline,
    processes: task5Processes(),
  });
  const task5Action = async (operation, label) => {
    assertTask5Health();
    const value = await task5Outcome(operation(), label);
    assertTask5Health();
    return value;
  };

  const machineMemos = modelWorkspace.artifacts.filter((artifact) => artifact?.kind === "memo");
  if (machineMemos.length !== 1) throw new Error("model workspace did not expose exactly one machine memo");
  const machineMemo = machineMemos[0];
  if (machineMemo.payload?.candidate_status !== "machine_draft"
    || machineMemo.payload?.assessment_status !== "not_answerable") {
    throw new Error("pre-confirmation machine memo boundary mismatch");
  }
  const prepublicationDecisions = savedEvidenceDecisions(evidence);
  const confirmationPath = `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/judgment-confirmations`;
  const readyWorkspaceCursor = workspaceResponses.cursor();
  const confirmationResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === confirmationPath,
  { timeout: remaining(deadline, "judgment confirmation") }));
  await task5Action(() => page.getByLabel("研究备忘录 Markdown").fill(HUMAN_MEMO), "judgment memo fill");
  await task5Action(
    () => page.getByRole("button", { name: "确认当前判断", exact: true }).click(),
    "judgment confirmation click",
  );
  const confirmationResponse = await task5Outcome(
    confirmationResponsePromise, "judgment confirmation response",
  );
  const confirmation = await task5Outcome(
    responseJson(confirmationResponse), "judgment confirmation JSON",
  );
  if (confirmation?.project_id !== projectId
    || confirmation?.preparation?.id !== modelWorkspace.preparation.id
    || confirmation.preparation.status !== "ready_to_freeze"
    || confirmation.preparation.current_step !== "memo"
    || confirmation.preparation.progress !== 95
    || confirmation?.draft?.id !== modelWorkspace.draft.id
    || confirmation.draft.lock_version !== modelWorkspace.draft.lock_version + 1
    || confirmation.markdown !== HUMAN_MEMO
    || confirmation.assessment_status !== "not_answerable"
    || confirmation.reviewer !== "human:local-user"
    || confirmation?.machine_memo?.id !== machineMemo.id
    || confirmation.machine_memo.content_hash !== machineMemo.content_hash
    || confirmation?.confirmed_memo?.id === machineMemo.id
    || !SHA256_PATTERN.test(confirmation?.confirmed_memo?.content_hash ?? "")) {
    throw new Error("judgment confirmation successor mismatch");
  }
  await task5Visible(
    page.getByText("判断已确认，可以冻结版本。", { exact: true }),
    "visible judgment confirmation",
  );
  const readyWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: readyWorkspaceCursor,
    projectId,
    status: "ready_to_freeze",
    progress: 95,
    reviewedCount: factKeys.length,
    evidenceArtifact: evidence,
    deadline,
    processes: runningProcesses(),
  });
  assertWorkspace(readyWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "ready_to_freeze",
    progress: 95,
  });
  if (readyWorkspace.preparation.current_step !== "memo"
    || readyWorkspace.selected_revision !== null
    || readyWorkspace.draft.id !== confirmation.draft.id
    || readyWorkspace.draft.lock_version !== confirmation.draft.lock_version) {
    throw new Error("ready-to-freeze workspace identity mismatch");
  }
  const confirmedMemos = readyWorkspace.artifacts.filter((artifact) => artifact?.kind === "memo");
  if (confirmedMemos.length !== 1) throw new Error("ready workspace did not expose exactly one confirmed memo");
  const confirmedMemo = assertExactConfirmedMemo(
    machineMemo, confirmedMemos[0], HUMAN_MEMO, modelWorkspace,
  );
  if (confirmation.confirmed_memo.id !== confirmedMemo.id
    || confirmation.confirmed_memo.content_hash !== confirmedMemo.content_hash) {
    throw new Error("judgment response confirmed memo binding mismatch");
  }
  for (const modelArtifact of modelWorkspace.artifacts) {
    if (modelArtifact.kind === "memo") continue;
    const readyArtifact = readyWorkspace.artifacts.find((artifact) => artifact?.kind === modelArtifact.kind);
    assertSameArtifactHead(modelArtifact, readyArtifact);
  }
  assertSavedEvidenceDecisions(readyWorkspace, prepublicationDecisions, "confirmed workspace");
  const prepublicationDescriptors = exactArtifactDescriptors(readyWorkspace);

  const publicationPreviewPath = `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/publication-preview`;
  const publicationPreviewResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === publicationPreviewPath,
  { timeout: remaining(deadline, "publication preview") }));
  await task5Action(
    () => page.getByRole("button", { name: "预览冻结版本", exact: true }).click(),
    "publication preview click",
  );
  const publicationPreviewResponse = await task5Outcome(
    publicationPreviewResponsePromise, "publication preview response",
  );
  const publicationPreview = await task5Outcome(
    responseJson(publicationPreviewResponse), "publication preview JSON",
  );
  const expectedSecurityByKey = new Map(preview.securities.map((security) => [security.external_key, security]));
  const previewSecuritiesAreExact = Array.isArray(publicationPreview?.securities)
    && publicationPreview.securities.length === expectedSecurityByKey.size
    && publicationPreview.securities.every((security) => {
      const expected = expectedSecurityByKey.get(security?.external_key);
      return expected !== undefined
        && security.object_id === expected.object_id
        && security.canonical_name === expected.canonical_name
        && security.symbol === expected.symbol
        && security.exchange === expected.exchange
        && security.share_class === expected.share_class
        && security.trading_currency === expected.trading_currency
        && security.company_id === alphabet.object_id;
    });
  if (publicationPreview?.project_id !== projectId
    || publicationPreview.expected_lock_version !== readyWorkspace.draft.lock_version
    || publicationPreview?.company?.object_id !== alphabet.object_id
    || publicationPreview.company.external_key !== alphabetExternalKey
    || publicationPreview.company.canonical_name !== alphabet.canonical_name
    || !previewSecuritiesAreExact
    || publicationPreview.cutoff_at !== preview.cutoff_at
    || publicationPreview.cutoff_at !== evidence.payload.cutoff
    || publicationPreview.assessment?.answerability !== "not_answerable"
    || publicationPreview.assessment.direction !== null
    || publicationPreview.assessment.confidence !== null
    || !SHA256_PATTERN.test(publicationPreview.assessment.content_hash ?? "")
    || publicationPreview.value_range !== null
    || publicationPreview.return_range !== null
    || publicationPreview.memo_markdown !== HUMAN_MEMO
    || !SHA256_PATTERN.test(publicationPreview.manifest_hash ?? "")) {
    throw new Error("publication preview contract mismatch");
  }
  assertExactFrozenDescriptors(publicationPreview.artifacts, prepublicationDescriptors, "preview");
  const previewDialog = page.getByRole("dialog", { name: "确认冻结版本" });
  await task5Visible(previewDialog, "publication preview dialog");
  for (const value of [
    projectId,
    publicationPreview.company.object_id,
    publicationPreview.company.external_key,
    publicationPreview.company.canonical_name,
    publicationPreview.cutoff_at,
    publicationPreview.assessment.answerability,
    publicationPreview.assessment.content_hash,
    publicationPreview.manifest_hash,
  ]) await task5Visible(
    previewDialog.getByText(value, { exact: false }).first(), `visible publication value ${value}`,
  );
  await task5Visible(previewDialog.getByText("未建立方向", { exact: true }), "visible closed direction");
  await task5Visible(previewDialog.getByText("未建立置信度", { exact: true }), "visible closed confidence");
  await task5Visible(previewDialog.getByText("未建立价值范围", { exact: true }), "visible closed value range");
  await task5Visible(previewDialog.getByText("未建立回报范围", { exact: true }), "visible closed return range");
  for (const security of publicationPreview.securities) {
    const visible = `${security.schema_version} · ${security.canonical_name} · ${security.external_key} · ${security.symbol} · ${security.exchange} · ${security.share_class} · ${security.trading_currency} · ${security.company_id} · ${security.object_id}`;
    await task5Visible(previewDialog.getByText(visible, { exact: true }), `visible frozen security ${security.external_key}`);
  }
  for (const artifact of publicationPreview.artifacts) {
    const visible = `${artifact.schema_version} ${artifact.kind} ${artifact.id} v${artifact.version} input ${artifact.input_hash} content ${artifact.content_hash}`;
    await task5Visible(previewDialog.getByText(visible, { exact: true }), `visible frozen artifact ${artifact.kind}`);
  }

  const publishPath = `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/publish`;
  const publishResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === publishPath,
  { timeout: remaining(deadline, "publication") }));
  const publishedWorkspaceCursor = workspaceResponses.cursor();
  const automaticReplayResponsePromise = retainPrimaryFailure(page.waitForResponse((response) => {
    const pathname = new URL(response.url()).pathname;
    return response.request().method() === "GET"
      && pathname.includes(`/projects/${encodeURIComponent(projectId)}/revisions/`)
      && !pathname.endsWith("/export");
  }, { timeout: remaining(deadline, "automatic frozen revision replay") }));
  await task5Action(
    () => page.getByRole("button", { name: "冻结并发布", exact: true }).click(),
    "publication click",
  );
  const publishResponse = await task5Outcome(publishResponsePromise, "publication response");
  const frozen = await task5Outcome(responseJson(publishResponse), "publication JSON");
  if (frozen?.project_id !== projectId || frozen.sequence !== 1
    || frozen.preparation_status !== "completed" || frozen.current_step !== null
    || frozen.progress !== 100 || frozen.manifest_hash === publicationPreview.manifest_hash
    || !UUID_PATTERN.test(frozen.id ?? "") || !UUID_PATTERN.test(frozen.boundary_id ?? "")
    || !UUID_PATTERN.test(frozen.manifest_id ?? "")
    || !SHA256_PATTERN.test(frozen.manifest_hash ?? "")) {
    throw new Error("published revision contract mismatch");
  }
  assertExactPublicationProjection(frozen, publicationPreview, "published revision");
  const automaticReplayResponse = await task5Outcome(
    automaticReplayResponsePromise, "automatic frozen revision replay response",
  );
  const automaticReplay = await task5Outcome(
    responseJson(automaticReplayResponse), "automatic frozen revision replay JSON",
  );
  if (!jsonValuesEqual(automaticReplay, frozen)) throw new Error("automatic frozen revision replay mismatch");
  const publishedWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: publishedWorkspaceCursor,
    projectId,
    status: "completed",
    progress: 100,
    reviewedCount: factKeys.length,
    evidenceArtifact: evidence,
    deadline,
    processes: runningProcesses(),
  });
  assertWorkspace(publishedWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "completed",
    progress: 100,
  });
  if (publishedWorkspace.preparation.current_step !== null
    || publishedWorkspace.selected_revision !== frozen.id
    || publishedWorkspace.draft.id !== readyWorkspace.draft.id
    || publishedWorkspace.draft.lock_version !== readyWorkspace.draft.lock_version + 1) {
    throw new Error("published workspace revision identity mismatch");
  }
  assertSavedEvidenceDecisions(publishedWorkspace, prepublicationDecisions, "published workspace");
  assertExactFrozenDescriptors(exactArtifactDescriptors(publishedWorkspace), prepublicationDescriptors, "published workspace");
  await task5Visible(
    page.getByText(`冻结版本 ${frozen.id}`, { exact: true }).first(),
    "visible automatic frozen revision replay",
  );

  const revisionPath = `/api/underwriting/v1/product/company-research/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(frozen.id)}`;
  const explicitReplayResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === revisionPath,
  { timeout: remaining(deadline, "explicit frozen revision replay") }));
  await task5Action(
    () => page.getByRole("button", { name: "查看冻结版本", exact: true }).click(),
    "explicit frozen revision replay click",
  );
  const explicitReplayResponse = await task5Outcome(
    explicitReplayResponsePromise, "explicit frozen revision replay response",
  );
  const explicitReplay = await task5Outcome(
    responseJson(explicitReplayResponse), "explicit frozen revision replay JSON",
  );
  if (!jsonValuesEqual(explicitReplay, frozen)) throw new Error("explicit frozen revision replay mismatch");
  assertExactFrozenDescriptors(explicitReplay.artifacts, prepublicationDescriptors, "explicit replay");
  assertSavedEvidenceDecisions(publishedWorkspace, prepublicationDecisions, "explicit replay workspace");
  await task5Visible(
    page.getByText("冻结版本已载入。", { exact: true }),
    "visible explicit frozen revision replay",
  );

  const exportPath = `${revisionPath}/export`;
  let downloadCount = 0;
  page.on("download", () => { downloadCount += 1; });
  const exportResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === exportPath,
  { timeout: remaining(deadline, "Markdown export response") }));
  const downloadPromise = retainPrimaryFailure(page.waitForEvent("download", {
    timeout: remaining(deadline, "Markdown download"),
  }));
  await task5Action(
    () => page.getByRole("button", { name: "导出 Markdown", exact: true }).click(),
    "Markdown export click",
  );
  const [download, exportHttpResponse] = await task5Outcome(
    Promise.all([downloadPromise, exportResponsePromise]), "Markdown download and export response",
  );
  const envelope = await task5Outcome(responseJson(exportHttpResponse), "Markdown export JSON");
  const downloadFailure = await task5Outcome(download.failure(), "Markdown download completion");
  if (downloadFailure !== null) throw new Error("Markdown download did not produce a file");
  const downloadPath = path.join(browserRuntimeDirectory, "downloads", `verified-${frozen.id}.md`);
  await task5Outcome(download.saveAs(downloadPath), "Markdown download save");
  assertTask5Health();
  const [canonicalBrowserRuntime, canonicalDownload, downloadStat] = await Promise.all([
    realpath(browserRuntimeDirectory),
    realpath(downloadPath),
    lstat(downloadPath),
  ]);
  assertTask5Health();
  if (!(canonicalDownload.startsWith(`${canonicalBrowserRuntime}${path.sep}`))
    || !downloadStat.isFile() || downloadStat.isSymbolicLink()
    || (typeof process.getuid === "function" && downloadStat.uid !== process.getuid())) {
    throw new Error("Markdown download escaped the authenticated browser runtime");
  }
  const bytes = await readFile(canonicalDownload);
  assertTask5Health();
  const contentHash = createHash("sha256").update(bytes).digest("hex");
  if (download.suggestedFilename() !== envelope.filename
    || envelope.filename !== `alphabet-company-research-${frozen.id}.md`
    || envelope.media_type !== "text/markdown"
    || bytes.length === 0
    || contentHash !== envelope.content_hash
    || bytes.toString("utf8") !== envelope.content) {
    throw new Error("Markdown download contract mismatch");
  }
  await task5Visible(
    page.getByText("Markdown 已验证并下载。", { exact: true }),
    "visible Markdown download",
  );
  if (downloadCount !== 1) throw new Error(`expected one Markdown download; saw ${downloadCount}`);

  const reloadWorkspaceCursor = workspaceResponses.cursor();
  const reloadReplayResponsePromise = retainPrimaryFailure(page.waitForResponse((response) =>
    response.request().method() === "GET" && new URL(response.url()).pathname === revisionPath,
  { timeout: remaining(deadline, "reloaded frozen revision replay") }));
  await task5Outcome(page.reload({
    waitUntil: "networkidle",
    timeout: remaining(deadline, "completed project reload"),
  }), "completed project reload");
  const finalWorkspace = await waitForBrowserWorkspace({
    collector: workspaceResponses,
    after: reloadWorkspaceCursor,
    projectId,
    status: "completed",
    progress: 100,
    reviewedCount: factKeys.length,
    evidenceArtifact: evidence,
    deadline,
    processes: runningProcesses(),
  });
  const reloadReplayResponse = await task5Outcome(
    reloadReplayResponsePromise, "reloaded frozen revision response",
  );
  const reloadedRevision = await task5Outcome(
    responseJson(reloadReplayResponse), "reloaded frozen revision JSON",
  );
  assertWorkspace(finalWorkspace, {
    projectId,
    companyId: alphabet.object_id,
    status: "completed",
    progress: 100,
  });
  if (finalWorkspace.preparation.current_step !== null
    || finalWorkspace.selected_revision !== frozen.id
    || finalWorkspace.draft.id !== publishedWorkspace.draft.id
    || finalWorkspace.draft.lock_version !== publishedWorkspace.draft.lock_version
    || !jsonValuesEqual(reloadedRevision, frozen)) {
    throw new Error("reloaded completed workspace revision mismatch");
  }
  assertSavedEvidenceDecisions(finalWorkspace, prepublicationDecisions, "reloaded workspace");
  assertExactFrozenDescriptors(exactArtifactDescriptors(finalWorkspace), prepublicationDescriptors, "reloaded workspace");
  assertExactFrozenDescriptors(reloadedRevision.artifacts, prepublicationDescriptors, "reloaded replay");
  await task5Visible(
    page.getByText(`冻结版本 ${frozen.id}`, { exact: true }).first(),
    "visible reloaded frozen revision",
  );
  if (downloadCount !== 1 || contentHash !== envelope.content_hash) {
    throw new Error("final completed workspace or download hash changed");
  }

  const assertTraffic = () => {
    audit.assertSingleton("GET", searchPath);
    audit.assertSingleton("POST", previewPath);
    audit.assertSingleton("POST", initializationPath);
    audit.assertSingleton("POST", confirmationPath);
    audit.assertSingleton("POST", publicationPreviewPath);
    audit.assertSingleton("POST", publishPath);
    audit.assertSingleton("GET", exportPath);
    if (audit.requestCount("POST", reviewPath) !== factKeys.length) {
      throw new Error(`expected one evidence review request per governed fact; saw ${audit.requestCount("POST", reviewPath)}`);
    }
    if (audit.requestCount("GET", revisionPath) !== 3) {
      throw new Error(`expected three authenticated revision replays; saw ${audit.requestCount("GET", revisionPath)}`);
    }
    const allowedWrites = new Set([
      previewPath, initializationPath, reviewPath, confirmationPath, publicationPreviewPath, publishPath,
    ]);
    const unexpectedWrites = audit.snapshot().requests.filter(([method, pathname]) =>
      method !== "GET" && !(method === "POST" && allowedWrites.has(pathname)));
    if (unexpectedWrites.length > 0) throw new Error("unexpected Company Research browser write");
    if (downloadCount !== 1) throw new Error(`expected one Markdown download; saw ${downloadCount}`);
  };
  await task5Outcome(pendingObservers.drain({
    timeoutMs: Math.min(CLEANUP_TIMEOUT_MS, remaining(deadline, "browser observer drain")),
  }), "browser observer drain");
  throwBrowserFailures(failures);
  assertTraffic();
  assertTask5Health();
  return { assertTraffic, finalWorkspace, frozen, contentHash };
}

async function main() {
  const { timeoutMs } = parseVerifierArgs(process.argv.slice(2));
  const deadline = Date.now() + timeoutMs;
  const python = await resolvePython();
  probePython(python);
  await assertPythonIdentity(python);
  const canonicalCleanupHelper = await realpath(cleanupHelperPath);
  let runtime;
  let api;
  let worker;
  let vite;
  let browser;
  let browserFailures;
  let pendingObservers;
  let assertTraffic;
  let finalProof;
  let primaryError = null;
  let cleanupErrors = [];

  try {
    runtime = await createPrivateRuntime({
      cleanupHelper: {
        pythonExecutable: python.canonical,
        helperPath: canonicalCleanupHelper,
      },
    });
    const [apiPort, uiPort] = await Promise.all([freePort(), freePort()]);
    const apiOrigin = `http://127.0.0.1:${apiPort}`;
    const uiBase = `http://127.0.0.1:${uiPort}`;
    assertLoopbackUrl(apiOrigin);
    assertLoopbackUrl(uiBase);
    const env = {
      ...buildVerifierEnvironment({
        host: process.env,
        databaseUrl: `sqlite:///${path.join(runtime.directory, "company-research.sqlite")}`,
        token,
        backendUrl: apiOrigin,
      }),
      TMPDIR: runtime.directory,
      TEMP: runtime.directory,
      TMP: runtime.directory,
    };

    await runBootstrap(
      python,
      ["-c", "from app.models.ledger import Base; from app.db import engine; Base.metadata.create_all(engine)"],
      { env, deadline, label: "database schema creation" },
    );
    await runBootstrap(
      python,
      ["-m", "app.scripts.load_product_foundation_fixture"],
      { env, deadline, label: "product foundation load" },
    );

    await assertPythonIdentity(python);
    api = startOwnedProcess(
      python.launcher,
      ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(apiPort)],
      { cwd: backend, env, name: "api" },
    );
    const launchWorker = async () => {
      await assertPythonIdentity(python);
      return startOwnedProcess(
        python.launcher,
        ["-m", "app.scripts.run_company_research_worker", "--loop", "--poll-seconds", "0.1"],
        { cwd: backend, env, name: "company-research-worker" },
      );
    };
    worker = await launchWorker();
    const backendProcesses = [api, worker];
    await waitUntil(async ({ signal }) => {
      const response = await fetch(`${apiOrigin}${searchPath}?query=Alphabet&limit=20`, {
        headers: { Authorization: `Bearer ${token}` },
        signal,
      });
      return response.ok;
    }, {
      label: "authenticated live API",
      timeoutMs: remaining(deadline, "authenticated live API"),
      processes: backendProcesses,
    });

    vite = startOwnedProcess(
      process.execPath,
      ["./node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(uiPort), "--strictPort"],
      { cwd: frontend, env, name: "vite" },
    );
    const runningProcesses = () => [api, worker, vite].filter((owned) => owned !== null && owned !== undefined);
    const processes = runningProcesses();
    await waitUntil(async ({ signal }) => {
      const response = await fetch(`${uiBase}/research/new`, { signal });
      return response.ok;
    }, {
      label: "non-mock frontend",
      timeoutMs: remaining(deadline, "non-mock frontend"),
      processes,
    });

    browser = await startOwnedBrowser(chromium, runtime, {
      channel: env.PW_BROWSER_CHANNEL || undefined,
      env,
      timeoutMs: remaining(deadline, "browser launch"),
    });
    const audit = createTrafficAudit(uiBase);
    browserFailures = createBrowserFailureCollector();
    pendingObservers = createPendingObserverTracker();
    const context = await newOwnedBrowserContext(browser, { acceptDownloads: true });
    await context.route("**/*", async (route) => {
      try {
        audit.assertAllowedRequest(route.request().url());
      } catch (error) {
        browserFailures.record(error);
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });
    await context.routeWebSocket(/.*/u, (websocket) => {
      try {
        audit.assertAllowedWebSocket(websocket.url());
        websocket.connectToServer();
      } catch (error) {
        browserFailures.record(error);
        void websocket.close({ code: 1008, reason: "blocked by verifier" });
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(Math.min(30_000, remaining(deadline, "browser workflow")));
    page.setDefaultNavigationTimeout(Math.min(30_000, remaining(deadline, "browser workflow")));

    page.on("pageerror", () => {
      browserFailures.record(new Error("unhandled page exception"));
    });
    page.on("console", (message) => {
      if (message.type() === "error") browserFailures.record(new Error("browser console error"));
    });
    page.on("requestfailed", (request) => pendingObservers.track(() => browserFailures.capture(() =>
      audit.recordRequestFailure(request.method(), request.url()))));
    page.on("request", (request) => pendingObservers.track(() => browserFailures.capture(() =>
      audit.recordRequest(request.method(), request.url()))));
    page.on("response", (response) => pendingObservers.track(() => browserFailures.capture(() =>
      audit.recordResponse(response.status(), response.request().method(), response.url()))));

    await page.clock.setFixedTime(await alphabetFixtureCutoff());

    const workflow = await runBrowserWorkflow({
      page,
      uiBase,
      audit,
      failures: browserFailures,
      deadline,
      runningProcesses,
      foundation: await alphabetFoundation(),
      pendingObservers,
      browserRuntimeDirectory: browser.directories.root,
      async stopWorkerForReview() {
        if (!worker) throw new Error("company research worker is absent before evidence review");
        const stoppedWorker = worker;
        assertProcessRunningBeforeIntentionalStop(stoppedWorker);
        await stopOwnedProcess(stoppedWorker);
        if (worker !== stoppedWorker) throw new Error("company research worker identity changed during stop");
        worker = null;
      },
      async restartWorker() {
        if (worker) throw new Error("company research worker was not stopped before restart");
        worker = await launchWorker();
        return worker;
      },
    });
    assertTraffic = workflow.assertTraffic;
    finalProof = workflow;
  } catch (error) {
    primaryError = error instanceof Error ? error : new Error("live company research verifier failed");
  } finally {
    cleanupErrors = await finalizeOwnedRuntime({
      browser, browserFailures, pendingObservers, assertTraffic, worker, vite, api, runtime,
    });
  }

  const runError = chooseRunError(primaryError, cleanupErrors);
  if (runError) {
    const cleanupSummaries = primaryError === null ? "" : cleanupErrors
      .map((error) => `\ncleanup failure: ${error.message}`)
      .join("");
    const diagnostic = new Error(
      `${runError.message}${cleanupSummaries}${[api, worker, vite].map(componentTail).join("")}`,
      { cause: runError },
    );
    if (isRecord(runError) && Object.hasOwn(runError, "code")) diagnostic.code = runError.code;
    throw diagnostic;
  }
  if (finalProof?.finalWorkspace?.preparation?.status !== "completed"
    || finalProof.finalWorkspace.preparation.current_step !== null
    || finalProof.finalWorkspace.preparation.progress !== 100
    || finalProof.finalWorkspace.selected_revision !== finalProof?.frozen?.id
    || !SHA256_PATTERN.test(finalProof?.contentHash ?? "")) {
    throw new Error("final completed workspace or download hash proof is absent");
  }
  console.log(PASS_LINE);
}

function safeOuterDiagnostic(error) {
  const raw = error instanceof Error ? error.message : "live company research verifier failed";
  const sanitized = raw
    .replaceAll(token, "[redacted]")
    .replace(/[^\s]*fund-engine-live-company-research-[^\s:]*/gu, "[private-runtime]")
    .replace(/[^\s]*company-research\.sqlite/gu, "[private-database]")
    .replace(/[\u0000-\u001F\u007F]/gu, " ");
  return Array.from(sanitized).slice(0, 1_000).join("");
}

main().catch((error) => {
  console.error(safeOuterDiagnostic(error));
  process.exitCode = 1;
});
