import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { chmod, lstat, mkdtemp, realpath, rename, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const USAGE = "usage: verify-live-company-research-ui.mjs [--timeout-seconds 30..300]";
const LOOPBACK_HOST = "127.0.0.1";
const EXACT_LOOPBACK_AUTHORITY = /^http:\/\/127\.0\.0\.1(?::\d+)?(?:[/?#]|$)/;
const PREFIX = "fund-engine-live-company-research-";
const MAX_LOG_BYTES = 16_384;
const MAX_DIAGNOSTIC_FIELD_BYTES = 8_000;
const STOP_TIMEOUT_MS = 5_000;
const RUNTIME_AUTHORITIES = new WeakMap();
const OWNED_PROCESSES = new WeakSet();
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
  return path.join(authority.parent, `.${PREFIX}cleanup-${randomUUID()}`);
}

async function restoreClaimedDirectoryIfSafe(authority, claimed) {
  try {
    await lstat(authority.directory);
    return false;
  } catch (error) {
    if (error?.code !== "ENOENT") return false;
  }
  try {
    await rename(claimed, authority.directory);
    authority.quarantine = null;
    return true;
  } catch {
    return false;
  }
}

function appendBoundedLog(owned, stream, chunk) {
  const combined = Buffer.concat([owned[stream], Buffer.from(chunk)]);
  owned[stream] = combined.length > MAX_LOG_BYTES ? combined.subarray(-MAX_LOG_BYTES) : combined;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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
  return String(value).replace(/[\u0000-\u001F\u007F]/g, " ").slice(0, limit);
}

function processExitCode(owned) {
  return boundedDiagnostic(owned.exitCode ?? owned.child.exitCode ?? owned.child.signalCode ?? "unknown", MAX_DIAGNOSTIC_FIELD_BYTES);
}

function processFailure(owned) {
  const exited = owned.exited || owned.child.exitCode !== null || owned.child.signalCode !== null;
  if (!exited || owned.expectedStop) return null;
  return new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} exited with ${processExitCode(owned)}`);
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
    owned.child.once("exit", check);
    owned.child.once("error", check);
    listeners.push([owned.child, "exit", check], [owned.child, "error", check]);
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

export async function createPrivateRuntime() {
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

  let stat;
  try {
    stat = await lstat(claimed);
  } catch (error) {
    if (error?.code === "ENOENT") {
      authority.removed = true;
      return;
    }
    throw error;
  }
  if (!hasPrivateRuntimeIdentity(stat, authority)) {
    await restoreClaimedDirectoryIfSafe(authority, claimed);
    privateRuntimeIdentityChanged();
  }

  const finalClaim = quarantinePath(authority);
  await rename(claimed, finalClaim);
  authority.quarantine = finalClaim;
  stat = await lstat(finalClaim);
  if (!hasPrivateRuntimeIdentity(stat, authority)) {
    await restoreClaimedDirectoryIfSafe(authority, finalClaim);
    privateRuntimeIdentityChanged();
  }
  await rm(finalClaim, { recursive: true });
  authority.removed = true;
}

export function chooseRunError(primary, cleanupErrors) {
  return primary ?? cleanupErrors[0] ?? null;
}

export function startOwnedProcess(command, args, { cwd, env, name }) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  let resolveExit;
  const owned = {
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
  };
  child.stdout.on("data", (chunk) => appendBoundedLog(owned, "stdout", chunk));
  child.stderr.on("data", (chunk) => appendBoundedLog(owned, "stderr", chunk));
  child.once("exit", (code, signal) => {
    owned.exited = true;
    owned.exitCode = code ?? signal;
    resolveExit();
  });
  child.once("error", (error) => {
    owned.exited = true;
    owned.exitCode = error.code ?? error.message;
    resolveExit();
  });
  OWNED_PROCESSES.add(owned);
  return owned;
}

export function assertProcessesRunning(processes) {
  const failure = processFailureFor(processes);
  if (failure) throw failure;
}

export async function stopOwnedProcess(owned) {
  if (!OWNED_PROCESSES.has(owned)) {
    throw new Error("refusing to stop an unowned process handle");
  }
  if (owned.stopPromise) return owned.stopPromise;
  owned.stopPromise = stopOwnedProcessImpl(owned);
  return owned.stopPromise;
}

async function stopOwnedProcessImpl(owned) {
  owned.expectedStop = true;
  if (owned.exited) return;

  try {
    if (!owned.child.kill("SIGTERM")) {
      throw new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} refused SIGTERM`);
    }
  } catch (error) {
    if (error?.code === "ESRCH" && owned.exited) return;
    throw new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} failed to send SIGTERM: ${boundedDiagnostic(error?.message ?? error, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
  }
  await waitForExit(owned, STOP_TIMEOUT_MS);
  if (owned.exited) return;
  try {
    if (!owned.child.kill("SIGKILL")) {
      throw new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} refused SIGKILL`);
    }
  } catch (error) {
    if (error?.code === "ESRCH" && owned.exited) return;
    throw new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} failed to send SIGKILL: ${boundedDiagnostic(error?.message ?? error, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
  }
  if (!await waitForExit(owned, STOP_TIMEOUT_MS)) {
    throw new Error(`${boundedDiagnostic(owned.name, MAX_DIAGNOSTIC_FIELD_BYTES)} did not exit after SIGKILL within ${STOP_TIMEOUT_MS}ms`);
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
    const probeResult = Promise.resolve()
      .then(probe)
      .then((value) => ({ kind: "probe", value }), (error) => ({ kind: "probe-error", error }));
    const outcome = await Promise.race([
      probeResult,
      timer.promise,
      watcher.promise.then((error) => ({ kind: "process", error })),
    ]);
    timer.cancel();
    watcher.cancel();
    if (outcome.kind === "deadline") break;
    if (outcome.kind === "process") throw outcome.error;
    if (outcome.kind === "probe") {
      if (outcome.value) {
        assertProcessesRunning(processes);
        return outcome.value;
      }
    } else {
      latest = boundedDiagnostic(
        outcome.error instanceof Error ? outcome.error.message : outcome.error,
        MAX_DIAGNOSTIC_FIELD_BYTES,
      );
    }
    assertProcessesRunning(processes);
    const nextRemaining = deadline - Date.now();
    if (nextRemaining <= 0) break;
    await sleep(Math.min(intervalMs, nextRemaining));
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
