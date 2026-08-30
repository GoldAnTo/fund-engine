#!/usr/bin/env node
/** Start the isolated live Company Research stack and create Alphabet through the UI. */
import { execFileSync } from "node:child_process";
import { constants as fsConstants } from "node:fs";
import { access, lstat, readFile, realpath } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

import {
  assertLoopbackUrl,
  assertProcessesRunning,
  assertAlphabetIdentityBinding,
  buildVerifierEnvironment,
  chooseRunError,
  closeOwnedBrowser,
  createBrowserFailureCollector,
  createPrivateRuntime,
  createTrafficAudit,
  newOwnedBrowserContext,
  parseVerifierArgs,
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

function repositoryVenvRoots() {
  const roots = [path.join(backend, ".venv")];
  const worktreesParent = path.dirname(root);
  if (path.basename(worktreesParent) === ".worktrees") {
    roots.push(path.join(path.dirname(worktreesParent), "backend", ".venv"));
  }
  return roots;
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
  const allowedRoots = repositoryVenvRoots();
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

async function finalizeOwnedRuntime({ browser, browserFailures, worker, vite, api, runtime }) {
  const cleanupErrors = [];
  if (browser) {
    try {
      await closeOwnedBrowser(browser, { timeoutMs: CLEANUP_TIMEOUT_MS });
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (browserFailures) {
    try {
      browserFailures.throwIfAny();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  for (const owned of [worker, vite, api]) {
    if (!owned) continue;
    try {
      await stopOwnedProcess(owned);
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (runtime) {
    try {
      await removePrivateRuntime(runtime);
    } catch (error) {
      cleanupErrors.push(error);
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

async function runBrowserWorkflow({ page, uiBase, audit, failures, deadline, processes, foundation }) {
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
  throwBrowserFailures(failures);
  assertProcessesRunning(processes);
  audit.assertSingleton("GET", searchPath);
  audit.assertSingleton("POST", previewPath);
  audit.assertSingleton("POST", initializationPath);
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
    await assertPythonIdentity(python);
    worker = startOwnedProcess(
      python.launcher,
      ["-m", "app.scripts.run_company_research_worker", "--loop", "--poll-seconds", "0.1"],
      { cwd: backend, env, name: "company-research-worker" },
    );
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
    const processes = [api, worker, vite];
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
    const context = await newOwnedBrowserContext(browser);
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
    page.on("requestfailed", (request) => browserFailures.capture(() =>
      audit.recordRequestFailure(request.method(), request.url())));
    page.on("request", (request) => browserFailures.capture(() =>
      audit.recordRequest(request.method(), request.url())));
    page.on("response", (response) => browserFailures.capture(() =>
      audit.recordResponse(response.status(), response.request().method(), response.url())));

    await page.clock.setFixedTime(await alphabetFixtureCutoff());

    await runBrowserWorkflow({
      page,
      uiBase,
      audit,
      failures: browserFailures,
      deadline,
      processes,
      foundation: await alphabetFoundation(),
    });
  } catch (error) {
    primaryError = error instanceof Error ? error : new Error("live company research verifier failed");
  } finally {
    cleanupErrors = await finalizeOwnedRuntime({
      browser, browserFailures, worker, vite, api, runtime,
    });
  }

  const runError = chooseRunError(primaryError, cleanupErrors);
  if (runError) throw runError;
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
