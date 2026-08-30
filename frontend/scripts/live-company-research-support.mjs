import { spawn } from "node:child_process";
import { chmod, lstat, mkdtemp, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const USAGE = "usage: verify-live-company-research-ui.mjs [--timeout-seconds 30..300]";
const LOOPBACK_HOST = "127.0.0.1";
const EXACT_LOOPBACK_AUTHORITY = /^http:\/\/127\.0\.0\.1(?::\d+)?(?:[/?#]|$)/;
const PREFIX = "fund-engine-live-company-research-";
const MAX_LOG_BYTES = 16_384;
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

function assertPrivateRuntimeStat(stat, runtime) {
  if (!stat.isDirectory()
    || stat.isSymbolicLink()
    || (stat.mode & 0o777) !== 0o700
    || (typeof process.getuid === "function" && stat.uid !== process.getuid())
    || (runtime && (stat.dev !== runtime.dev || stat.ino !== runtime.ino))) {
    privateRuntimeIdentityChanged();
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
    timeout = setTimeout(resolve, timeoutMs);
  });
  await Promise.race([owned.exitPromise, timedOut]);
  clearTimeout(timeout);
}

function boundedMessage(error) {
  const message = error instanceof Error ? error.message : String(error);
  return message.length > MAX_LOG_BYTES ? message.slice(-MAX_LOG_BYTES) : message;
}

function processExitCode(owned) {
  return owned.exitCode ?? owned.child.exitCode ?? owned.child.signalCode ?? "unknown";
}

export async function createPrivateRuntime() {
  const parent = await realpath(os.tmpdir());
  const directory = await mkdtemp(path.join(parent, PREFIX));
  await chmod(directory, 0o700);
  const stat = await lstat(directory);
  assertPrivateRuntimeStat(stat);
  return { parent, directory, dev: stat.dev, ino: stat.ino };
}

export async function removePrivateRuntime(runtime) {
  if (!runtime
    || typeof runtime.parent !== "string"
    || typeof runtime.directory !== "string"
    || path.dirname(runtime.directory) !== runtime.parent
    || !path.basename(runtime.directory).startsWith(PREFIX)) {
    privateRuntimeIdentityChanged();
  }

  let stat;
  try {
    stat = await lstat(runtime.directory);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  assertPrivateRuntimeStat(stat, runtime);
  await rm(runtime.directory, { recursive: true });
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
  return owned;
}

export function assertProcessesRunning(processes) {
  for (const owned of processes) {
    if (owned.exited && !owned.expectedStop) {
      throw new Error(`${owned.name} exited with ${processExitCode(owned)}`);
    }
  }
}

export async function stopOwnedProcess(owned) {
  if (!owned || owned.expectedStop) return;
  owned.expectedStop = true;
  if (owned.exited) return;

  try {
    owned.child.kill("SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await waitForExit(owned, 5_000);
  if (owned.exited) return;
  try {
    owned.child.kill("SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await owned.exitPromise;
}

export async function waitUntil(probe, { label, timeoutMs, intervalMs = 100, processes = [] }) {
  const deadline = Date.now() + timeoutMs;
  let latest = "probe returned a falsy value";
  while (Date.now() <= deadline) {
    assertProcessesRunning(processes);
    try {
      const result = await probe();
      if (result) return result;
    } catch (error) {
      latest = boundedMessage(error);
    }
    assertProcessesRunning(processes);
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await sleep(Math.min(intervalMs, remaining));
  }
  assertProcessesRunning(processes);
  throw new Error(`${label} timed out: ${latest}`);
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
