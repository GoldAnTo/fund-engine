#!/usr/bin/env node
/** Verify CATL answerability and Alphabet refusal through the real browser mainline. */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, lstat, readFile, realpath } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import {
  LIVE_COMPANY_RESEARCH_CASES, assertCriticalInputFinalBinding,
  assertCriticalInputSuccessor,
  assertLiveCaseDefinitions, assertLiveCaseOutcome,
  assertLoopbackUrl, assertProcessRunningBeforeIntentionalStop, buildVerifierEnvironment,
  chooseRunError, closeOwnedBrowser, createBrowserFailureCollector,
  createPendingObserverTracker, createPrivateRuntime, createTrafficAudit,
  newOwnedBrowserContext, parseVerifierArgs, removePrivateRuntime,
  repositoryPythonVenvRoots, retainPrimaryFailure, startOwnedBrowser,
  startOwnedProcess, stopOwnedProcess, waitUntil,
} from "./live-company-research-support.mjs";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(frontend, "..");
const backend = path.join(root, "backend");
const cleanupHelperPath = path.join(backend, "app", "scripts", "remove_private_runtime_contents.py");
const foundationPath = path.join(backend, "app", "underwriting", "fixtures", "product_foundation", "manifest.json");
const token = "live-company-research-verifier-token";
const searchPath = "/api/underwriting/v1/product/objects";
const previewPath = "/api/underwriting/v1/product/company-research/preview";
const initializationPath = "/api/underwriting/v1/product/company-research/initializations";
const CLEANUP_TIMEOUT_MS = 10_000;
const BROWSER_STARTUP_TIMEOUT_MS = 30_000;
const COOPERATIVE_SHUTDOWN_TIMEOUT_MS = 60_000;
const PYTHON_PROBE_TIMEOUT_MS = 60_000;
const PASS_LINE = "PASS: default frontend completed live CATL answerable and Alphabet not-answerable company research through frozen revision replay and verified Markdown export";
const SHA256 = /^[0-9a-f]{64}$/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;

const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
function remaining(deadline, label) {
  const value = deadline - Date.now();
  if (value <= 0) throw new Error(`${label} timed out`);
  return value;
}
function freePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer(); server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") return server.close(() => reject(new Error("failed to allocate loopback port")));
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}
const trustedMode = (stat) => (stat.mode & 0o022) === 0
  && (typeof process.getuid !== "function" || stat.uid === process.getuid());
const identity = (stat) => Object.freeze({ dev: stat.dev, ino: stat.ino, uid: stat.uid, mode: stat.mode & 0o777 });
const sameIdentity = (stat, expected) => stat.dev === expected.dev && stat.ino === expected.ino
  && stat.uid === expected.uid && (stat.mode & 0o777) === expected.mode;

async function validatePython(candidate, venvRoot, label) {
  const launcher = path.resolve(candidate); const rootPath = path.resolve(venvRoot);
  try {
    const [rootStat, binStat, launcherStat, canonicalRoot, canonical] = await Promise.all([
      lstat(rootPath), lstat(path.join(rootPath, "bin")), lstat(launcher), realpath(rootPath), realpath(launcher),
    ]);
    const canonicalStat = await lstat(canonical); await access(launcher, fsConstants.X_OK);
    if (canonicalRoot !== rootPath || launcher !== path.join(rootPath, "bin", "python")
      || !rootStat.isDirectory() || rootStat.isSymbolicLink() || !trustedMode(rootStat)
      || !binStat.isDirectory() || binStat.isSymbolicLink() || !trustedMode(binStat)
      || (!launcherStat.isFile() && !launcherStat.isSymbolicLink())
      || !canonicalStat.isFile() || canonicalStat.isSymbolicLink()
      || (canonicalStat.mode & 0o111) === 0 || !trustedMode(canonicalStat)) throw new Error("invalid executable identity");
    return Object.freeze({ launcher, canonical, venvRoot: rootPath, launcherIdentity: identity(launcherStat), canonicalIdentity: identity(canonicalStat) });
  } catch { throw new Error(`${label} is not a validated Python executable`); }
}
async function resolvePython() {
  const roots = repositoryPythonVenvRoots(root);
  if (process.env.PYTHON) {
    const configured = path.resolve(process.cwd(), process.env.PYTHON);
    const allowed = roots.find((item) => configured === path.join(path.resolve(item), "bin", "python"));
    if (!allowed) throw new Error("configured PYTHON is outside the trusted repository venv");
    return validatePython(configured, allowed, "configured PYTHON");
  }
  for (const item of roots) {
    try { return await validatePython(path.join(item, "bin", "python"), item, "repository backend Python"); }
    catch { /* Continue only through the closed repository venv list. */ }
  }
  throw new Error("repository backend Python is not a validated executable");
}
async function assertPythonIdentity(python) {
  const [launcher, canonical, target] = await Promise.all([lstat(python.launcher), lstat(python.canonical), realpath(python.launcher)]);
  if (target !== python.canonical || !sameIdentity(launcher, python.launcherIdentity)
    || !sameIdentity(canonical, python.canonicalIdentity)) throw new Error("trusted Python identity changed");
}
function probePython(python) {
  const code = "import importlib,pathlib,sys;assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve();[importlib.import_module(x) for x in ('sqlalchemy','uvicorn','fastapi')];sys.path.insert(0,sys.argv[2]);importlib.import_module('app.main')";
  try {
    execFileSync(python.launcher, ["-I", "-c", code, python.venvRoot, backend], {
      cwd: backend, env: { APP_ENV: "test", DATABASE_URL: "sqlite:///:memory:", RESEARCH_TENANT_TOKENS: "{}", NO_PROXY: "127.0.0.1,localhost", no_proxy: "127.0.0.1,localhost" },
      stdio: ["ignore", "ignore", "ignore"], timeout: PYTHON_PROBE_TIMEOUT_MS,
    });
  } catch { throw new Error("trusted Python dependency probe failed"); }
}
async function jsonFile(filename, label) {
  try { return JSON.parse(await readFile(filename, "utf8")); }
  catch { throw new Error(`${label} is unreadable`); }
}
async function caseCutoff(definition) {
  const manifest = await jsonFile(path.join(backend, definition.manifestRelativePath), `${definition.id} manifest`);
  if (manifest?.schema_version !== definition.manifestSchemaVersion || !Number.isFinite(Date.parse(manifest?.cutoff))) throw new Error(`${definition.id} cutoff is invalid`);
  return manifest.cutoff;
}
function assertBoundIdentity(definition, foundation, search, preview) {
  const company = search?.items?.filter((item) => item?.kind === "company" && item.external_key === definition.companyExternalKey) ?? [];
  const expectedCompany = foundation?.companies?.filter((item) => item.external_key === definition.companyExternalKey && item.canonical_name === definition.companyName) ?? [];
  const expectedSecurities = foundation?.securities?.filter((item) => item.company_key === definition.companyExternalKey) ?? [];
  const securityKeys = expectedSecurities.map((item) => item.external_key).sort();
  if (foundation?.schema_version !== "product.foundation-identities.v1" || !SHA256.test(foundation?.content_hash ?? "")
    || company.length !== 1 || expectedCompany.length !== 1 || company[0].canonical_name !== definition.companyName
    || !UUID.test(company[0].object_id ?? "") || preview?.company?.object_id !== company[0].object_id
    || preview.company.external_key !== definition.companyExternalKey
    || securityKeys.join(",") !== [...definition.securityExternalKeys].sort().join(",")
    || preview.securities?.length !== expectedSecurities.length
    || preview.securities.some((item) => !securityKeys.includes(item.external_key))) throw new Error(`${definition.id} identity mismatch`);
  return company[0];
}
async function responseJson(response) {
  const request = response.request(); const pathname = new URL(response.url()).pathname;
  if (!response.ok()) throw new Error(`browser API ${response.status()} ${request.method()} ${pathname}`);
  try { return await response.json(); }
  catch { throw new Error(`browser API returned invalid JSON: ${request.method()} ${pathname}`); }
}
async function authenticatedJson(apiOrigin, pathname, deadline, processes) {
  return waitUntil(async ({ signal }) => {
    const response = await fetch(`${apiOrigin}${pathname}`, { headers: { Authorization: `Bearer ${token}` }, signal });
    return response.ok ? response.json() : null;
  }, { label: `authenticated API ${pathname}`, timeoutMs: remaining(deadline, pathname), processes });
}
async function bootstrap(python, args, env, deadline, label) {
  await assertPythonIdentity(python);
  try { execFileSync(python.launcher, args, { cwd: backend, env, stdio: ["ignore", "ignore", "pipe"], timeout: Math.min(30_000, remaining(deadline, label)) }); }
  catch { throw new Error(`${label} failed`); }
}
function cleanupFailure(label, error) {
  const raw = error instanceof Error ? error.message : "cleanup failed";
  const safe = raw.replaceAll(token, "[redacted]").replace(/[^\s]*fund-engine-live-company-research-[^\s:]*/gu, "[private-runtime]").replace(/[^\s]*company-research\.sqlite/gu, "[private-database]").replace(/[\u0000-\u001F\u007F]/gu, " ");
  return new Error(`${label}: ${Array.from(safe).slice(0, 180).join("")}`, { cause: error });
}
async function finalize(state) {
  const errors = []; const record = (label, error) => errors.push(cleanupFailure(label, error));
  if (state.browser) try { await closeOwnedBrowser(state.browser, { timeoutMs: CLEANUP_TIMEOUT_MS }); } catch (error) { record("browser cleanup", error); }
  if (state.observers) try { await state.observers.drain({ timeoutMs: CLEANUP_TIMEOUT_MS }); } catch (error) { record("observer cleanup", error); }
  if (state.audit) try { state.audit.assertNoMockAdapter(); } catch (error) { record("traffic cleanup", error); }
  if (state.failures) try { state.failures.throwIfAny(); } catch (error) { record("browser failure cleanup", error); }
  for (const owned of [state.worker, state.vite, state.api].filter(Boolean)) {
    try { assertProcessRunningBeforeIntentionalStop(owned); } catch (error) { record(`${owned.name} health`, error); }
    try { await stopOwnedProcess(owned); } catch (error) { record(`${owned.name} stop`, error); }
  }
  if (state.runtime) try { await removePrivateRuntime(state.runtime); } catch (error) { record("private runtime cleanup", error); }
  return errors;
}
async function visible(locator, deadline, label, processes) {
  await locator.waitFor({ state: "visible", timeout: remaining(deadline, label) });
  for (const process of processes) assertProcessRunningBeforeIntentionalStop(process);
}

function pendingCriticalInputs(value, expectedTotal, label) {
  const inputs = value?.inputs;
  if (!UUID.test(value?.artifact_id ?? "") || !Number.isSafeInteger(value?.version)
    || !Array.isArray(inputs) || inputs.length !== expectedTotal
    || new Set(inputs.map((item) => item?.key)).size !== inputs.length
    || inputs.some((item) => typeof item?.key !== "string" || !SHA256.test(item?.input_fingerprint ?? ""))) {
    throw new Error(`${label} critical input state is invalid`);
  }
  return inputs.filter((item) => item.decision === "pending");
}

function investmentRangeFromWire(valuation) {
  const item = valuation?.security_value_ranges?.[0];
  const value = item?.value_per_share;
  const returns = item?.base_currency_return;
  if (!item) return { valueRange: null, returnRange: null };
  if (typeof value?.minimum?.value !== "string" || typeof value?.maximum?.value !== "string"
    || typeof item.value_currency !== "string" || typeof returns?.minimum?.value !== "string"
    || typeof returns?.maximum?.value !== "string") throw new Error("valuation wire range is invalid");
  return {
    valueRange: { minimum: value.minimum.value, maximum: value.maximum.value, currency: item.value_currency },
    returnRange: { minimum: returns.minimum.value, maximum: returns.maximum.value },
  };
}

function frozenArtifactPayload(run, frozen, kind, { required = true } = {}) {
  const current = run.workspace?.artifacts?.filter((item) => item.kind === kind) ?? [];
  const descriptor = frozen.artifacts?.filter((item) => item.kind === kind) ?? [];
  if (!required && current.length === 0 && descriptor.length === 0) return null;
  if (current.length !== 1 || descriptor.length !== 1 || current[0].id !== descriptor[0].id
    || current[0].content_hash !== descriptor[0].content_hash) {
    throw new Error(`frozen ${kind} wire binding is invalid`);
  }
  return current[0].payload;
}

async function runBrowserWorkflow({ definition, page, apiOrigin, uiBase, audit, failures, observers, deadline, processes, foundation, browserRoot }) {
  audit.assertNoMockAdapter();
  await page.goto(`${uiBase}/research/new`, { waitUntil: "networkidle", timeout: remaining(deadline, `${definition.id} entry`) });
  await page.getByLabel("搜索公司或证券").fill(definition.query);
  const searchWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === searchPath, { timeout: remaining(deadline, `${definition.id} search`) }));
  await page.getByRole("button", { name: "搜索对象" }).click();
  const search = await responseJson(await searchWait);
  const choose = definition.id === "alphabet-not-answerable" ? "选择 Alphabet" : `选择 ${definition.companyName}`;
  const previewWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === previewPath, { timeout: remaining(deadline, `${definition.id} preview`) }));
  await page.getByRole("button", { name: choose }).click();
  const preview = await responseJson(await previewWait); const company = assertBoundIdentity(definition, foundation, search, preview);
  const initialRunWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname.endsWith("/run"), { timeout: remaining(deadline, `${definition.id} initial run`) }));
  const initializeWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === initializationPath, { timeout: remaining(deadline, `${definition.id} initialize`) }));
  await page.getByRole("button", { name: "开始 AI 研究" }).click();
  const initialized = await responseJson(await initializeWait);
  if (initialized.company_id !== company.object_id || !UUID.test(initialized.project_id ?? "")) throw new Error(`${definition.id} initialization mismatch`);
  const projectId = initialized.project_id; const runPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/run`;
  if (new URL((await initialRunWait).url()).pathname !== runPath) throw new Error(`${definition.id} initial run binding mismatch`);
  await page.waitForURL(`${uiBase}/research/projects/${projectId}`, { timeout: remaining(deadline, `${definition.id} navigation`) });
  await visible(page.getByRole("region", { name: "研究阶段" }), deadline, `${definition.id} stages`, processes());
  await visible(page.getByRole("region", { name: "研究过程" }), deadline, `${definition.id} process`, processes());
  await visible(page.locator(".ir-run-status").getByText("需要补充", { exact: true }), deadline, `${definition.id} machine draft`, processes());
  await visible(page.getByRole("heading", { name: "公司研究结果" }), deadline, `${definition.id} result`, processes());
  const answerabilityLabel = definition.expectedAnswerability === "answerable" ? "可回答" : "当前不可回答";
  await visible(page.getByText(`可回答性：${answerabilityLabel}`, { exact: true }), deadline, `${definition.id} answerability`, processes());
  const fullProcessWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === runPath && new URL(response.url()).searchParams.get("include_process") === "true", { timeout: remaining(deadline, `${definition.id} full process`) }));
  await page.getByRole("button", { name: "查看完整过程" }).click();
  const fullProcess = await responseJson(await fullProcessWait);
  if (fullProcess.recent_process?.length < 5 || fullProcess.recent_process.length > 100) throw new Error(`${definition.id} process bound mismatch`);
  await page.getByRole("button", { name: /来源、事实与缺口/ }).click();
  await visible(page.getByRole("heading", { name: "来源、事实与缺口" }), deadline, `${definition.id} sources and gaps`, processes());
  const before = await authenticatedJson(apiOrigin, runPath, deadline, processes());
  let currentCritical = before?.critical_inputs;
  const initialPending = pendingCriticalInputs(currentCritical, definition.expectedCriticalInputCount, definition.id);
  if (initialPending.length !== definition.expectedCriticalInputCount) throw new Error(`${definition.id} critical input count mismatch`);
  await page.getByRole("button", { name: "保存研究版本" }).click();
  const decisionPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/critical-input-decisions`;
  const judgmentConfirmationPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/judgment-confirmations`;
  const decidedKeys = new Set();
  for (let index = 0; index < definition.expectedCriticalInputCount; index += 1) {
    const pendingBefore = pendingCriticalInputs(currentCritical, definition.expectedCriticalInputCount, definition.id);
    const selected = pendingBefore[0];
    if (!selected || decidedKeys.has(selected.key)) throw new Error(`${definition.id} repeated or absent critical input`);
    await visible(page.getByRole("heading", { name: "确认关键输入" }), deadline, `${definition.id} decision ${index + 1}`, processes());
    await visible(page.getByRole("heading", { name: selected.key, exact: true }), deadline, `${definition.id} input ${selected.key}`, processes());
    await visible(page.getByRole("status").getByText(`剩余 ${pendingBefore.length}`, { exact: false }), deadline, `${definition.id} pending ${pendingBefore.length}`, processes());
    const confirm = page.getByRole("radio", { name: "确认当前输入" });
    const decision = await confirm.isVisible() ? "confirmed" : "accepted_gap";
    if (decision === "confirmed") await confirm.check(); else await page.getByRole("radio", { name: "接受为研究缺口" }).check();
    const decisionWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === decisionPath, { timeout: remaining(deadline, `${definition.id} decision response`) }));
    await page.getByRole("button", { name: "提交本项决定" }).click();
    const decisionResponse = await decisionWait;
    if (decisionResponse.status() === 409) throw new Error(`${definition.id} critical decision conflict`);
    const requestPayload = decisionResponse.request().postDataJSON();
    if (!isRecord(requestPayload) || requestPayload.critical_input_key !== selected.key
      || requestPayload.expected_artifact_id !== currentCritical.artifact_id
      || requestPayload.expected_input_fingerprint !== selected.input_fingerprint
      || requestPayload.decision !== decision) throw new Error(`${definition.id} critical decision request binding mismatch`);
    const decided = await responseJson(decisionResponse);
    const nextCritical = assertCriticalInputSuccessor({
      current: currentCritical,
      successor: decided,
      selected,
      expectedDecision: decision,
      expectedRequest: requestPayload,
      projectId,
      expectedTotal: definition.expectedCriticalInputCount,
    });
    const pendingAfter = pendingCriticalInputs(
      nextCritical, definition.expectedCriticalInputCount, definition.id,
    );
    decidedKeys.add(selected.key); currentCritical = nextCritical;
    if (pendingAfter[0]) {
      await visible(page.getByRole("status").getByText(`剩余 ${pendingAfter.length}`, { exact: false }), deadline, `${definition.id} advanced pending ${pendingAfter.length}`, processes());
      await visible(page.getByRole("heading", { name: pendingAfter[0].key, exact: true }), deadline, `${definition.id} next input`, processes());
    } else {
      await visible(page.getByRole("button", { name: "预览冻结版本" }), deadline, `${definition.id} all inputs decided`, processes());
    }
  }
  await visible(page.getByRole("button", { name: "预览冻结版本" }), deadline, `${definition.id} preview freeze`, processes());
  const publicationPreviewPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/publication-preview`;
  const publicationPreviewWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === publicationPreviewPath, { timeout: remaining(deadline, `${definition.id} publication preview`) }));
  await page.getByRole("button", { name: "预览冻结版本" }).click();
  const publicationPreview = await responseJson(await publicationPreviewWait);
  if (publicationPreview.assessment?.answerability !== definition.expectedAnswerability || !SHA256.test(publicationPreview.manifest_hash ?? "")) throw new Error(`${definition.id} publication preview mismatch`);
  const publishPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/publish`;
  const publishWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === publishPath, { timeout: remaining(deadline, `${definition.id} publish`) }));
  const automaticReplayWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname.includes(`/projects/${projectId}/revisions/`) && !new URL(response.url()).pathname.endsWith("/export"), { timeout: remaining(deadline, `${definition.id} automatic replay`) }));
  await page.getByRole("button", { name: "冻结并发布" }).click();
  const frozen = await responseJson(await publishWait); const automatic = await responseJson(await automaticReplayWait);
  if (!UUID.test(frozen.id ?? "") || frozen.sequence !== 1 || frozen.assessment?.answerability !== definition.expectedAnswerability || automatic.id !== frozen.id) throw new Error(`${definition.id} publication mismatch`);
  await visible(page.getByRole("button", { name: "查看冻结版本" }), deadline, `${definition.id} replay`, processes());
  const revisionPath = `/api/underwriting/v1/product/company-research/projects/${projectId}/revisions/${frozen.id}`;
  const explicitReplayWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === revisionPath, { timeout: remaining(deadline, `${definition.id} explicit replay`) }));
  await page.getByRole("button", { name: "查看冻结版本" }).click(); const replayed = await responseJson(await explicitReplayWait);
  if (replayed.id !== frozen.id || replayed.manifest_hash !== frozen.manifest_hash) throw new Error(`${definition.id} replay mismatch`);
  const exportPath = `${revisionPath}/export`; let downloadCount = 0; page.on("download", () => { downloadCount += 1; });
  const exportWait = retainPrimaryFailure(page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === exportPath, { timeout: remaining(deadline, `${definition.id} export`) }));
  const downloadWait = retainPrimaryFailure(page.waitForEvent("download", { timeout: remaining(deadline, `${definition.id} download`) }));
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const [exportResponse, download] = await Promise.all([exportWait, downloadWait]); const envelope = await responseJson(exportResponse);
  if (await download.failure() !== null) throw new Error(`${definition.id} download failed`);
  const destination = path.join(browserRoot, "downloads", `${definition.id}-${frozen.id}.md`); await download.saveAs(destination);
  const [rootReal, fileReal, stat] = await Promise.all([realpath(browserRoot), realpath(destination), lstat(destination)]);
  if (!fileReal.startsWith(`${rootReal}${path.sep}`) || !stat.isFile() || stat.isSymbolicLink()
    || download.suggestedFilename() !== envelope.filename || envelope.filename !== `${definition.exportFilenamePrefix}${frozen.id}.md`
    || envelope.media_type !== "text/markdown") throw new Error(`${definition.id} download identity mismatch`);
  const exportBytes = await readFile(fileReal); const exportHash = createHash("sha256").update(exportBytes).digest("hex");
  if (exportBytes.toString("utf8") !== envelope.content || exportHash !== envelope.content_hash) throw new Error(`${definition.id} export bytes mismatch`);
  const finalRun = await authenticatedJson(apiOrigin, runPath, deadline, processes());
  assertCriticalInputFinalBinding({
    critical: currentCritical,
    finalRunCritical: finalRun.critical_inputs,
    frozenArtifacts: frozen.artifacts,
  });
  const scenario = frozenArtifactPayload(finalRun, frozen, "scenario_set");
  const valuation = frozenArtifactPayload(finalRun, frozen, "valuation_set", { required: definition.expectedAnswerability === "answerable" });
  const wireRanges = investmentRangeFromWire(valuation);
  const valueRange = frozen.value_range ?? wireRanges.valueRange;
  const returnRange = frozen.return_range ?? wireRanges.returnRange;
  const proof = assertLiveCaseOutcome(definition, { answerability: frozen.assessment.answerability, criticalInputCount: initialPending.length, selectedRevisionId: finalRun.selected_revision, replayedRevisionId: replayed.id, exportedRevisionId: frozen.id, publishedManifestHash: frozen.manifest_hash, replayedManifestHash: replayed.manifest_hash, declaredExportHash: envelope.content_hash, exportBytes, valueRangeCount: valueRange === null ? 0 : 1, assessmentDirection: frozen.assessment.direction, assessmentConfidence: frozen.assessment.confidence, valueRange, returnRange, scenarioIds: scenario?.scenarios?.map((item) => item.scenario_id) ?? [], sensitivityVariables: valuation?.sensitivity_analyses?.map((item) => item.variable_key) ?? [], strongestCounterevidenceCount: frozen.strongest_counterevidence?.length, nextVerificationEventCount: frozen.next_verification_events?.length });
  await observers.drain({ timeoutMs: Math.min(CLEANUP_TIMEOUT_MS, remaining(deadline, `${definition.id} observers`)) }); failures.throwIfAny(); audit.assertNoMockAdapter();
  for (const [method, pathname] of [["GET", searchPath], ["POST", previewPath], ["POST", initializationPath], ["POST", publicationPreviewPath], ["POST", publishPath], ["GET", exportPath]]) audit.assertSingleton(method, pathname);
  if (audit.requestCount("POST", decisionPath) !== initialPending.length || audit.requestCount("POST", `${runPath}/evidence-reviews`) !== 0 || downloadCount !== 1) throw new Error(`${definition.id} traffic cardinality mismatch`);
  if (audit.requestCount("POST", judgmentConfirmationPath) !== 0) throw new Error(`${definition.id} unexpected duplicate judgment confirmation`);
  const writes = new Set([previewPath, initializationPath, decisionPath, publicationPreviewPath, publishPath]);
  if (audit.snapshot().requests.some(([method, pathname]) => method !== "GET" && !(method === "POST" && writes.has(pathname)))) throw new Error(`${definition.id} unexpected browser write`);
  return proof;
}

async function runLiveCase(definition, python, timeoutMs, foundation, cleanupHelper) {
  const deadline = Date.now() + timeoutMs; const state = { runtime: null, api: null, worker: null, vite: null, browser: null, failures: null, observers: null, audit: null }; let primary = null; let proof = null;
  const browserStartupController = new AbortController();
  let runtimeStartupPromise = null; let browserStartupPromise = null;
  let signalFinalizationPromise = null; let finalizationPromise = null;
  let shutdownSignal = null; let shutdownWatchdog = null;
  const finalizeOnce = () => {
    finalizationPromise ??= (async () => {
      if (!state.runtime && runtimeStartupPromise) {
        try { state.runtime = await runtimeStartupPromise; } catch { /* Startup retains its own primary failure. */ }
      }
      if (!state.browser && browserStartupPromise) {
        try { state.browser = await browserStartupPromise; } catch { /* Startup removes any late browser ownership. */ }
      }
      try { return await finalize(state); }
      catch (error) { return [cleanupFailure("finalization", error)]; }
    })();
    return finalizationPromise;
  };
  const requestCooperativeShutdown = (name) => {
    if (shutdownSignal) return;
    shutdownSignal = name; primary ??= new Error(`live verifier received ${name}`);
    shutdownWatchdog = setTimeout(() => process.exit(1), COOPERATIVE_SHUTDOWN_TIMEOUT_MS);
    browserStartupController.abort();
    signalFinalizationPromise ??= finalizeOnce();
    void signalFinalizationPromise.catch(() => process.exit(1));
  };
  const handlers = new Map(["SIGINT", "SIGHUP", "SIGTERM"].map((name) => { const handler = () => requestCooperativeShutdown(name); process.once(name, handler); return [name, handler]; }));
  const open = () => { if (shutdownSignal) throw new Error(`live verifier received ${shutdownSignal}`); };
  try {
    runtimeStartupPromise = retainPrimaryFailure(createPrivateRuntime({ cleanupHelper: { pythonExecutable: python.canonical, helperPath: cleanupHelper } }));
    state.runtime = await runtimeStartupPromise; open();
    const [apiPort, uiPort] = await Promise.all([freePort(), freePort()]); const apiOrigin = `http://127.0.0.1:${apiPort}`; const uiBase = `http://127.0.0.1:${uiPort}`; assertLoopbackUrl(apiOrigin); assertLoopbackUrl(uiBase);
    const env = { ...buildVerifierEnvironment({ host: process.env, databaseUrl: `sqlite:///${path.join(state.runtime.directory, "company-research.sqlite")}`, token, backendUrl: apiOrigin }), TMPDIR: state.runtime.directory, TEMP: state.runtime.directory, TMP: state.runtime.directory };
    await bootstrap(python, ["-c", "from app.models.ledger import Base; from app.db import engine; Base.metadata.create_all(engine)"], env, deadline, `${definition.id} schema`);
    await bootstrap(python, ["-m", "app.scripts.load_product_foundation_fixture"], env, deadline, `${definition.id} foundation`);
    state.api = startOwnedProcess(python.launcher, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(apiPort)], { cwd: backend, env, name: `api:${definition.id}` });
    state.worker = startOwnedProcess(python.launcher, ["-m", "app.scripts.run_company_research_worker", "--loop", "--poll-seconds", "1"], { cwd: backend, env, name: `company-research-worker:${definition.id}` });
    await waitUntil(async ({ signal }) => (await fetch(`${apiOrigin}${searchPath}?query=${encodeURIComponent(definition.query)}&limit=20`, { headers: { Authorization: `Bearer ${token}` }, signal })).ok, { label: `${definition.id} API`, timeoutMs: remaining(deadline, `${definition.id} API`), processes: [state.api, state.worker] });
    state.vite = startOwnedProcess(process.execPath, ["./node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(uiPort), "--strictPort"], { cwd: frontend, env, name: `vite:${definition.id}` });
    const processes = () => [state.api, state.worker, state.vite].filter(Boolean);
    await waitUntil(async ({ signal }) => (await fetch(`${uiBase}/research/new`, { signal })).ok, { label: `${definition.id} frontend`, timeoutMs: remaining(deadline, `${definition.id} frontend`), processes: processes() }); open();
    browserStartupPromise = retainPrimaryFailure(startOwnedBrowser(chromium, state.runtime, { channel: env.PW_BROWSER_CHANNEL || undefined, env, signal: browserStartupController.signal, timeoutMs: Math.min(BROWSER_STARTUP_TIMEOUT_MS, remaining(deadline, `${definition.id} browser`)) }));
    state.browser = await browserStartupPromise; open();
    state.audit = createTrafficAudit(uiBase, { clientMode: env.VITE_RESEARCH_CLIENT }); state.failures = createBrowserFailureCollector(); state.observers = createPendingObserverTracker();
    const context = await newOwnedBrowserContext(state.browser, { acceptDownloads: true });
    await context.route("**/*", async (route) => { try { state.audit.assertAllowedRequest(route.request().url()); } catch (error) { state.failures.record(error); await route.abort("blockedbyclient"); return; } await route.continue(); });
    await context.routeWebSocket(/.*/u, (socket) => { try { state.audit.assertAllowedWebSocket(socket.url()); socket.connectToServer(); } catch (error) { state.failures.record(error); void socket.close({ code: 1008, reason: "blocked" }); } });
    const page = await context.newPage(); page.setDefaultTimeout(Math.min(30_000, remaining(deadline, `${definition.id} workflow`))); page.setDefaultNavigationTimeout(Math.min(30_000, remaining(deadline, `${definition.id} workflow`)));
    page.on("pageerror", () => state.failures.record(new Error("unhandled page exception"))); page.on("console", (message) => { if (message.type() === "error") state.failures.record(new Error("browser console error")); });
    page.on("requestfailed", (request) => state.observers.track(() => state.failures.capture(() => state.audit.recordRequestFailure(request.method(), request.url()))));
    page.on("request", (request) => state.observers.track(() => state.failures.capture(() => state.audit.recordRequest(request.method(), request.url(), request.headers()))));
    page.on("response", (response) => state.observers.track(() => state.failures.capture(() => state.audit.recordResponse(response.status(), response.request().method(), response.url()))));
    await page.clock.setFixedTime(await caseCutoff(definition));
    proof = await runBrowserWorkflow({ definition, page, apiOrigin, uiBase, audit: state.audit, failures: state.failures, observers: state.observers, deadline, processes, foundation, browserRoot: state.browser.directories.root });
  } catch (error) { primary ??= error instanceof Error ? error : new Error(`${definition.id} verification failed`); }
  finally {
    const cleanupErrors = signalFinalizationPromise ? await signalFinalizationPromise : await finalizeOnce();
    for (const [name, handler] of handlers) process.off(name, handler);
    clearTimeout(shutdownWatchdog);
    const error = chooseRunError(primary, cleanupErrors);
    if (error) throw new Error(`${error.message}${cleanupErrors.map((item) => `\ncleanup failure: ${item.message}`).join("")}`, { cause: error });
  }
  if (!proof) throw new Error(`${definition.id} proof is absent`); return proof;
}

async function main() {
  const { timeoutMs } = parseVerifierArgs(process.argv.slice(2)); const python = await resolvePython(); probePython(python); await assertPythonIdentity(python);
  const cleanupHelper = await realpath(cleanupHelperPath); const foundation = await jsonFile(foundationPath, "authenticated product foundation manifest");
  const caseDefinitions = assertLiveCaseDefinitions(LIVE_COMPANY_RESEARCH_CASES); const proofs = [];
  for (const definition of caseDefinitions) proofs.push(await runLiveCase(definition, python, timeoutMs, foundation, cleanupHelper));
  if (proofs.length !== 2) throw new Error("dual-case proof is incomplete"); console.log(PASS_LINE);
}
function safeDiagnostic(error) {
  const raw = error instanceof Error ? error.message : "live verifier failed";
  return Array.from(raw.replaceAll(token, "[redacted]").replace(/[^\s]*fund-engine-live-company-research-[^\s:]*/gu, "[private-runtime]").replace(/[^\s]*company-research\.sqlite/gu, "[private-database]").replace(/[\u0000-\u001F\u007F]/gu, " ")).slice(0, 1_000).join("");
}
main().catch((error) => { console.error(safeDiagnostic(error)); process.exitCode = 1; });
