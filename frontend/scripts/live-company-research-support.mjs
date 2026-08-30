import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { chmod, lstat, mkdtemp, open, realpath, rename, rmdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const USAGE = "usage: verify-live-company-research-ui.mjs [--timeout-seconds 30..300]";
const LOOPBACK_HOST = "127.0.0.1";
const EXACT_LOOPBACK_AUTHORITY = /^http:\/\/127\.0\.0\.1(?::\d+)?(?:[/?#]|$)/;
const PREFIX = "fund-engine-live-company-research-";
const MAX_LOG_BYTES = 16_384;
const MAX_DIAGNOSTIC_FIELD_BYTES = 8_000;
const STOP_TIMEOUT_MS = 5_000;
const HELPER_PHASE_TIMEOUT_MS = 5_000;
const RUNTIME_AUTHORITIES = new WeakMap();
const PROCESS_AUTHORITIES = new WeakMap();
const PRESERVED_ENVIRONMENT_KEYS = [
  "PATH",
  "SystemRoot",
  "WINDIR",
  "TMPDIR",
  "TEMP",
  "TMP",
  "NVM_DIR",
  "PW_BROWSER_CHANNEL",
];
const WORKSPACE_STATES = new Set([
  "queued",
  "preparing_sources",
  "awaiting_evidence_review",
  "building_model",
  "awaiting_judgment_review",
  "ready_to_freeze",
  "recoverable_failure",
  "blocked",
  "completed",
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSafePositiveInteger(value) {
  return Number.isSafeInteger(value) && value >= 1;
}

function workspaceError(message) {
  throw new Error(`workspace snapshot ${message}`);
}

function privateRuntimeIdentityChanged() {
  throw new Error("private runtime identity changed; refusing cleanup");
}

function hasPrivateRuntimeIdentity(stat, authority) {
  return stat.isDirectory()
    && !stat.isSymbolicLink()
    && (stat.mode & 0o777) === authority.mode
    && stat.uid === authority.uid
    && stat.dev === authority.dev
    && stat.ino === authority.ino;
}

function assertPrivateRuntimeStat(stat, authority) {
  if (!stat.isDirectory()
    || stat.isSymbolicLink()
    || (stat.mode & 0o777) !== 0o700
    || (typeof process.getuid === "function" && stat.uid !== process.getuid())
    || (authority && !hasPrivateRuntimeIdentity(stat, authority))) {
    privateRuntimeIdentityChanged();
  }
}

async function assertPrivateRuntimeParent(authority) {
  const stat = await lstat(authority.parent);
  if (!stat.isDirectory() || stat.isSymbolicLink()
    || stat.dev !== authority.parentDev || stat.ino !== authority.parentIno) {
    privateRuntimeIdentityChanged();
  }
}

function quarantinePath(authority) {
  return path.join(authority.parent, `.${path.basename(authority.directory)}.cleanup-${randomUUID()}`);
}

function appendBoundedBuffer(buffer, chunk) {
  const combined = Buffer.concat([buffer, Buffer.from(chunk)]);
  return combined.length > MAX_LOG_BYTES ? combined.subarray(-MAX_LOG_BYTES) : combined;
}

function hasFileIdentity(current, expected) {
  return current.isFile() && !current.isSymbolicLink()
    && current.dev === expected.dev && current.ino === expected.ino
    && current.uid === expected.uid && (current.mode & 0o777) === expected.mode;
}

async function assertCleanupHelperIdentity(authority) {
  const [pythonStat, helperStat] = await Promise.all([
    lstat(authority.cleanupHelper.pythonExecutable),
    lstat(authority.cleanupHelper.helperPath),
  ]);
  if (!hasFileIdentity(pythonStat, authority.cleanupHelper.python)
    || !hasFileIdentity(helperStat, authority.cleanupHelper.helper)) {
    throw new Error("private runtime cleanup helper identity changed");
  }
}

function runBoundedExactProcess(command, args, options, failureLabel, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, options);
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let settled = false;
    let timedOut = false;
    let termTimer;
    let killTimer;
    let finalTimer;
    const clearTimers = () => {
      clearTimeout(termTimer);
      clearTimeout(killTimer);
      clearTimeout(finalTimer);
    };
    const fail = (error) => {
      if (!settled) {
        settled = true;
        clearTimers();
        reject(error);
      }
    };
    const finishTimeout = () => fail(new Error(boundedDiagnostic(
      `${failureLabel} timed out: ${stderr.toString("utf8") || stdout.toString("utf8")}`,
    )));
    child.stdout?.on("data", (chunk) => { stdout = appendBoundedBuffer(stdout, chunk); });
    child.stderr?.on("data", (chunk) => { stderr = appendBoundedBuffer(stderr, chunk); });
    child.once("error", fail);
    child.once("close", (code, signal) => {
      if (settled) return;
      settled = true;
      clearTimers();
      if (timedOut) reject(new Error(boundedDiagnostic(`${failureLabel} timed out`)));
      else if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(boundedDiagnostic(
        `${failureLabel} exited with ${code ?? signal ?? "unknown"}: ${stderr.toString("utf8") || stdout.toString("utf8")}`,
      )));
    });
    termTimer = setTimeout(() => {
      timedOut = true;
      if (!child.kill("SIGTERM")) {
        finishTimeout();
        return;
      }
      killTimer = setTimeout(() => {
        if (!child.kill("SIGKILL")) {
          finishTimeout();
          return;
        }
        finalTimer = setTimeout(finishTimeout, timeoutMs);
      }, timeoutMs);
    }, timeoutMs);
  });
}

async function preflightCleanupHelper(authority) {
  await assertCleanupHelperIdentity(authority);
  await runBoundedExactProcess(authority.cleanupHelper.pythonExecutable, ["-I", authority.cleanupHelper.helperPath, "--self-test"], {
    env: {}, stdio: "ignore",
  }, "private runtime cleanup helper preflight", authority.cleanupHelper.timeoutMs);
}

async function runCleanupHelper(authority, directory) {
  const { pythonExecutable, helperPath } = authority.cleanupHelper;
  await assertCleanupHelperIdentity(authority);
  await runBoundedExactProcess(pythonExecutable, [
      "-I", helperPath, "3", String(authority.dev), String(authority.ino),
      String(authority.uid), String(authority.mode),
    ], {
      env: {},
      stdio: ["ignore", "pipe", "pipe", directory.fd],
    }, "private runtime cleanup helper", authority.cleanupHelper.timeoutMs);
}

function isRuntimeIdentityOpenError(error) {
  return ["EACCES", "EISDIR", "ELOOP", "ENOTDIR", "ENOENT", "EPERM"].includes(error?.code);
}

async function removeAnchoredRuntimeDirectory(claimed, authority) {
  let directory;
  try {
    directory = await open(claimed, fsConstants.O_RDONLY | fsConstants.O_DIRECTORY | fsConstants.O_NOFOLLOW);
  } catch (error) {
    if (isRuntimeIdentityOpenError(error)) privateRuntimeIdentityChanged();
    throw error;
  }
  try {
    let current;
    try {
      current = await directory.stat();
    } catch (error) {
      if (isRuntimeIdentityOpenError(error)) privateRuntimeIdentityChanged();
      throw error;
    }
    if (!hasPrivateRuntimeIdentity(current, authority)) privateRuntimeIdentityChanged();
    await runCleanupHelper(authority, directory);
  } finally {
    await directory.close();
  }
  // Darwin exposes no inode-conditional rmdir. The caller must exclude concurrent
  // same-UID replacement after fd-authenticated cleanup; rmdir can only remove an
  // empty replacement, while any nonempty victim is preserved by ENOTEMPTY.
  try {
    await rmdir(claimed);
  } catch (error) {
    if (error?.code === "ENOTEMPTY" || error?.code === "EEXIST") privateRuntimeIdentityChanged();
    throw error;
  }
}

function appendBoundedLog(authority, stream, chunk) {
  const combined = Buffer.concat([authority[stream], Buffer.from(chunk)]);
  authority[stream] = combined.length > MAX_LOG_BYTES ? combined.subarray(-MAX_LOG_BYTES) : combined;
}

async function waitForExit(owned, timeoutMs) {
  let timeout;
  const timedOut = new Promise((resolve) => {
    timeout = setTimeout(() => resolve(false), timeoutMs);
  });
  const exited = await Promise.race([owned.exitPromise.then(() => true), timedOut]);
  clearTimeout(timeout);
  return exited;
}

function boundedDiagnostic(value, limit = MAX_LOG_BYTES) {
  const sanitized = String(value).replace(/[\u0000-\u001F\u007F]/g, " ");
  let bytes = 0;
  let result = "";
  for (const codePoint of sanitized) {
    const width = Buffer.byteLength(codePoint);
    if (bytes + width > limit) break;
    result += codePoint;
    bytes += width;
  }
  return result;
}

function processExitCode(authority) {
  return boundedDiagnostic(authority.exitCode ?? authority.child.exitCode ?? authority.child.signalCode ?? "unknown", MAX_DIAGNOSTIC_FIELD_BYTES);
}

function processFailure(owned) {
  const authority = PROCESS_AUTHORITIES.get(owned);
  if (!authority) return new Error("refusing an unowned process handle");
  const exited = authority.exited || authority.child.exitCode !== null || authority.child.signalCode !== null;
  if (!exited || authority.expectedStop) return null;
  return new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} exited with ${processExitCode(authority)}`);
}

function processFailureFor(processes) {
  for (const owned of processes) {
    const failure = processFailure(owned);
    if (failure) return failure;
  }
  return null;
}

function watchProcessFailures(processes) {
  const failure = processFailureFor(processes);
  if (failure) return { promise: Promise.resolve(failure), cancel() {} };

  const listeners = [];
  let resolveFailure;
  const promise = new Promise((resolve) => {
    resolveFailure = resolve;
  });
  const check = () => {
    const nextFailure = processFailureFor(processes);
    if (nextFailure) resolveFailure(nextFailure);
  };
  for (const owned of processes) {
    const authority = PROCESS_AUTHORITIES.get(owned);
    if (!authority) continue;
    authority.child.once("exit", check);
    authority.child.once("error", check);
    listeners.push([authority.child, "exit", check], [authority.child, "error", check]);
  }
  return {
    promise,
    cancel() {
      for (const [child, event, listener] of listeners) child.removeListener(event, listener);
    },
  };
}

function deadlineTimer(milliseconds) {
  let timeout;
  return {
    promise: new Promise((resolve) => {
      timeout = setTimeout(() => resolve({ kind: "deadline" }), milliseconds);
    }),
    cancel() {
      clearTimeout(timeout);
    },
  };
}

async function validateCleanupHelper(cleanupHelper) {
  if (!cleanupHelper || typeof cleanupHelper.pythonExecutable !== "string"
    || typeof cleanupHelper.helperPath !== "string"
    || !path.isAbsolute(cleanupHelper.pythonExecutable) || !path.isAbsolute(cleanupHelper.helperPath)) {
    throw new Error("private runtime cleanup helper must use exact absolute executable and helper paths");
  }
  const [pythonExecutable, helperPath] = await Promise.all([
    realpath(cleanupHelper.pythonExecutable), realpath(cleanupHelper.helperPath),
  ]);
  const [pythonStat, helperStat] = await Promise.all([lstat(pythonExecutable), lstat(helperPath)]);
  if (!pythonStat.isFile() || pythonStat.isSymbolicLink() || (pythonStat.mode & 0o111) === 0) {
    throw new Error("private runtime cleanup helper Python executable must be a regular executable file");
  }
  if (!helperStat.isFile() || helperStat.isSymbolicLink()) throw new Error("private runtime cleanup helper must be a regular file");
  return Object.freeze({
    pythonExecutable,
    helperPath,
    python: Object.freeze({ dev: pythonStat.dev, ino: pythonStat.ino, uid: pythonStat.uid, mode: pythonStat.mode & 0o777 }),
    helper: Object.freeze({ dev: helperStat.dev, ino: helperStat.ino, uid: helperStat.uid, mode: helperStat.mode & 0o777 }),
    timeoutMs: Number.isSafeInteger(cleanupHelper.timeoutMs) && cleanupHelper.timeoutMs > 0
      ? cleanupHelper.timeoutMs : HELPER_PHASE_TIMEOUT_MS,
  });
}

export async function createPrivateRuntime({ cleanupHelper } = {}) {
  const validatedCleanupHelper = await validateCleanupHelper(cleanupHelper);
  const parent = await realpath(os.tmpdir());
  const directory = await mkdtemp(path.join(parent, PREFIX));
  await chmod(directory, 0o700);
  const [stat, parentStat] = await Promise.all([lstat(directory), lstat(parent)]);
  assertPrivateRuntimeStat(stat);
  const runtime = Object.freeze({ parent, directory, dev: stat.dev, ino: stat.ino });
  RUNTIME_AUTHORITIES.set(runtime, {
    parent,
    directory,
    dev: stat.dev,
    ino: stat.ino,
    uid: stat.uid,
    mode: stat.mode & 0o777,
    parentDev: parentStat.dev,
    parentIno: parentStat.ino,
    quarantine: null,
    removed: false,
    cleanupHelper: validatedCleanupHelper,
  });
  return runtime;
}

export async function removePrivateRuntime(runtime) {
  const authority = RUNTIME_AUTHORITIES.get(runtime);
  if (!authority || runtime.parent !== authority.parent || runtime.directory !== authority.directory
    || runtime.dev !== authority.dev || runtime.ino !== authority.ino || Object.isFrozen(runtime) === false) {
    privateRuntimeIdentityChanged();
  }
  if (path.dirname(authority.directory) !== authority.parent
    || !path.basename(authority.directory).startsWith(PREFIX)) {
    privateRuntimeIdentityChanged();
  }
  if (authority.removed) return;
  await assertPrivateRuntimeParent(authority);
  await preflightCleanupHelper(authority);

  let claimed = authority.quarantine;
  if (!claimed) {
    claimed = quarantinePath(authority);
    try {
      await rename(authority.directory, claimed);
    } catch (error) {
      if (error?.code === "ENOENT") {
        authority.removed = true;
        return;
      }
      throw error;
    }
    authority.quarantine = claimed;
  }

  await removeAnchoredRuntimeDirectory(claimed, authority);
  authority.removed = true;
}

export function chooseRunError(primary, cleanupErrors) {
  return primary ?? cleanupErrors[0] ?? null;
}

export function startOwnedProcess(command, args, { cwd, env, name }) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  let resolveExit;
  const authority = {
    child,
    name,
    stdout: Buffer.alloc(0),
    stderr: Buffer.alloc(0),
    expectedStop: false,
    exited: false,
    exitCode: null,
    exitPromise: new Promise((resolve) => {
      resolveExit = resolve;
    }),
    kill: child.kill.bind(child),
    stopPromise: null,
  };
  const childFacade = {};
  for (const key of ["pid", "exitCode", "signalCode", "killed"]) {
    Object.defineProperty(childFacade, key, { enumerable: true, get: () => authority.child[key] });
  }
  Object.freeze(childFacade);
  const owned = {};
  Object.defineProperties(owned, {
    child: { enumerable: true, get: () => childFacade },
    name: { enumerable: true, get: () => authority.name },
    stdout: { enumerable: true, get: () => Buffer.from(authority.stdout) },
    stderr: { enumerable: true, get: () => Buffer.from(authority.stderr) },
    expectedStop: { enumerable: true, get: () => authority.expectedStop },
    exited: { enumerable: true, get: () => authority.exited },
    exitCode: { enumerable: true, get: () => authority.exitCode },
  });
  Object.freeze(owned);
  PROCESS_AUTHORITIES.set(owned, authority);
  child.stdout.on("data", (chunk) => appendBoundedLog(authority, "stdout", chunk));
  child.stderr.on("data", (chunk) => appendBoundedLog(authority, "stderr", chunk));
  child.once("exit", (code, signal) => {
    authority.exited = true;
    authority.exitCode = code ?? signal;
    resolveExit();
  });
  child.once("error", (error) => {
    authority.exited = true;
    authority.exitCode = error.code ?? error.message;
    resolveExit();
  });
  return owned;
}

export function assertProcessesRunning(processes) {
  const failure = processFailureFor(processes);
  if (failure) throw failure;
}

export async function stopOwnedProcess(owned) {
  const authority = PROCESS_AUTHORITIES.get(owned);
  if (!authority) {
    throw new Error("refusing to stop an unowned process handle");
  }
  if (authority.stopPromise) return authority.stopPromise;
  authority.stopPromise = stopOwnedProcessImpl(authority);
  return authority.stopPromise;
}

async function stopOwnedProcessImpl(authority) {
  authority.expectedStop = true;
  if (authority.exited) return;

  try {
    if (!authority.kill("SIGTERM")) {
      throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} refused SIGTERM`);
    }
  } catch (error) {
    if (error?.code === "ESRCH" && authority.exited) return;
    throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} failed to send SIGTERM: ${boundedDiagnostic(error?.message ?? error, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
  }
  await waitForExit(authority, STOP_TIMEOUT_MS);
  if (authority.exited) return;
  try {
    if (!authority.kill("SIGKILL")) {
      throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} refused SIGKILL`);
    }
  } catch (error) {
    if (error?.code === "ESRCH" && authority.exited) return;
    throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} failed to send SIGKILL: ${boundedDiagnostic(error?.message ?? error, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
  }
  if (!await waitForExit(authority, STOP_TIMEOUT_MS)) {
    throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} did not exit after SIGKILL within ${STOP_TIMEOUT_MS}ms`);
  }
}

export async function waitUntil(probe, { label, timeoutMs, intervalMs = 100, processes = [] }) {
  const deadline = Date.now() + timeoutMs;
  let latest = "probe returned a falsy value";
  while (true) {
    assertProcessesRunning(processes);
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const timer = deadlineTimer(remaining);
    const watcher = watchProcessFailures(processes);
    const controller = new AbortController();
    const probeResult = Promise.resolve()
      .then(() => probe({ signal: controller.signal }))
      .then((value) => ({ kind: "probe", value }), (error) => ({ kind: "probe-error", error }));
    const outcome = await Promise.race([
      probeResult,
      timer.promise,
      watcher.promise.then((error) => ({ kind: "process", error })),
    ]);
    timer.cancel();
    watcher.cancel();
    controller.abort();
    if (outcome.kind === "deadline") break;
    if (outcome.kind === "process") throw outcome.error;
    if (outcome.kind === "probe") {
      if (outcome.value) {
        assertProcessesRunning(processes);
        return outcome.value;
      }
      latest = "probe returned a falsy value";
    } else {
      latest = boundedDiagnostic(
        outcome.error instanceof Error ? outcome.error.message : outcome.error,
        MAX_DIAGNOSTIC_FIELD_BYTES,
      );
    }
    assertProcessesRunning(processes);
    const nextRemaining = deadline - Date.now();
    if (nextRemaining <= 0) break;
    const interval = deadlineTimer(Math.min(intervalMs, nextRemaining));
    const intervalWatcher = watchProcessFailures(processes);
    const intervalOutcome = await Promise.race([
      interval.promise,
      intervalWatcher.promise.then((error) => ({ kind: "process", error })),
    ]);
    interval.cancel();
    intervalWatcher.cancel();
    if (intervalOutcome.kind === "process") throw intervalOutcome.error;
  }
  assertProcessesRunning(processes);
  throw new Error(`${boundedDiagnostic(label, MAX_DIAGNOSTIC_FIELD_BYTES)} timed out: ${boundedDiagnostic(latest, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
}

export function parseVerifierArgs(args) {
  if (args.length === 0) return { timeoutMs: 180_000 };
  if (args.length !== 2 || args[0] !== "--timeout-seconds" || !/^\d+$/.test(args[1])) {
    throw new Error(USAGE);
  }

  const timeoutSeconds = Number(args[1]);
  if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 300) {
    throw new Error(USAGE);
  }
  return { timeoutMs: timeoutSeconds * 1_000 };
}

export function assertLoopbackUrl(value) {
  if (typeof value !== "string" || !EXACT_LOOPBACK_AUTHORITY.test(value)) {
    throw new Error("verifier URL must be a credential-free HTTP loopback URL");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("verifier URL must be a credential-free HTTP loopback URL");
  }
  if (url.protocol !== "http:" || url.hostname !== LOOPBACK_HOST || url.username || url.password) {
    throw new Error("verifier URL must be a credential-free HTTP loopback URL");
  }
  return url;
}

export function buildVerifierEnvironment({ host, databaseUrl, token, backendUrl }) {
  assertLoopbackUrl(backendUrl);
  const environment = {};
  for (const key of PRESERVED_ENVIRONMENT_KEYS) {
    if (typeof host?.[key] === "string" && host[key].length > 0) environment[key] = host[key];
  }
  return {
    ...environment,
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

export function assertWorkspace(workspace, expected) {
  if (!isRecord(workspace) || workspace.schema_version !== "underwriting.v1") {
    workspaceError("schema version mismatch");
  }
  if (!isRecord(expected) || workspace.project_id !== expected.projectId) {
    workspaceError("project identity mismatch");
  }
  if (!isRecord(workspace.company)
    || workspace.company.id !== expected.companyId
    || workspace.company.object_id !== expected.companyId) {
    workspaceError("company identity mismatch");
  }
  if (!isRecord(workspace.preparation)
    || !WORKSPACE_STATES.has(workspace.preparation.status)
    || workspace.preparation.status !== expected.status) {
    workspaceError("status mismatch");
  }
  if (workspace.preparation.progress !== expected.progress) workspaceError("progress mismatch");
  if (!isRecord(workspace.draft) || !isSafePositiveInteger(workspace.draft.lock_version)) {
    workspaceError("draft lock version mismatch");
  }
  return workspace;
}
