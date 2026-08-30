import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, realpath, rename, rmdir } from "node:fs/promises";
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
const BROWSER_AUTHORITIES = new WeakMap();
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

export function repositoryPythonVenvRoots(repositoryRoot) {
  if (typeof repositoryRoot !== "string" || !path.isAbsolute(repositoryRoot)) {
    throw new Error("repository root must be absolute");
  }
  const root = path.normalize(repositoryRoot);
  const roots = [path.join(root, "backend", ".venv")];
  const worktreesParent = path.dirname(root);
  if (path.basename(worktreesParent) === ".worktrees") {
    roots.push(path.join(path.dirname(worktreesParent), "backend", ".venv"));
  }
  return roots;
}

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

function decodeBoundedUtf8(buffer) {
  let best = "";
  let bestLength = 0;
  for (let leading = 0; leading <= Math.min(3, buffer.length); leading += 1) {
    for (let trailing = 0; trailing <= Math.min(3, buffer.length - leading); trailing += 1) {
      const candidate = buffer.subarray(leading, buffer.length - trailing);
      if (candidate.length === 0 || candidate.length <= bestLength) continue;
      try {
        best = new TextDecoder("utf-8", { fatal: true }).decode(candidate);
        bestLength = candidate.length;
      } catch {
        // A bounded byte tail can split one code point at either edge.
      }
    }
  }
  return best;
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
      `${failureLabel} timed out: ${decodeBoundedUtf8(stderr) || decodeBoundedUtf8(stdout)}`,
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
        `${failureLabel} exited with ${code ?? signal ?? "unknown"}: ${decodeBoundedUtf8(stderr) || decodeBoundedUtf8(stdout)}`,
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
  await preflightCleanupHelper({ cleanupHelper: validatedCleanupHelper });
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

function ownedRuntimeAuthority(runtime) {
  const authority = RUNTIME_AUTHORITIES.get(runtime);
  if (!authority || authority.removed || authority.quarantine
    || runtime.parent !== authority.parent || runtime.directory !== authority.directory
    || runtime.dev !== authority.dev || runtime.ino !== authority.ino
    || Object.isFrozen(runtime) === false) {
    privateRuntimeIdentityChanged();
  }
  return authority;
}

async function createOwnedBrowserDirectories(runtime, authority) {
  await assertPrivateRuntimeParent(authority);
  const current = await lstat(runtime.directory);
  assertPrivateRuntimeStat(current, authority);
  const root = path.join(runtime.directory, "browser");
  const directories = {
    root,
    artifacts: path.join(root, "artifacts"),
    downloads: path.join(root, "downloads"),
    traces: path.join(root, "traces"),
    temporary: path.join(root, "tmp"),
    home: path.join(root, "home"),
    cache: path.join(root, "cache"),
    config: path.join(root, "config"),
    diskCache: path.join(root, "disk-cache"),
    crashes: path.join(root, "crashes"),
  };
  await mkdir(root, { mode: 0o700 });
  await Promise.all(Object.entries(directories)
    .filter(([key]) => key !== "root")
    .map(([, directory]) => mkdir(directory, { mode: 0o700 })));
  return Object.freeze(directories);
}

function settleWithin(promise, timeoutMs) {
  const timer = deadlineTimer(timeoutMs);
  return Promise.race([
    Promise.resolve(promise).then(
      (value) => ({ kind: "fulfilled", value }),
      (error) => ({ kind: "rejected", error }),
    ),
    timer.promise,
  ]).finally(() => timer.cancel());
}

async function killExactBrowserServer(server, timeoutMs) {
  const killed = await settleWithin(server.kill(), timeoutMs);
  if (killed.kind === "fulfilled") return;
  const child = typeof server.process === "function" ? server.process() : null;
  if (!child || typeof child.kill !== "function" || child.kill("SIGKILL") !== true) {
    throw new Error("exact browser server kill failed");
  }
  const exited = child.exitCode !== null || child.signalCode !== null
    ? { kind: "fulfilled" }
    : await settleWithin(new Promise((resolve) => {
      child.once("exit", resolve);
      child.once("error", resolve);
    }), timeoutMs);
  if (exited.kind !== "fulfilled") throw new Error("exact browser direct SIGKILL timed out");
  throw new Error("exact browser server kill required direct SIGKILL");
}

async function withOwnedProcessTemp(directory, operation) {
  const keys = ["TMPDIR", "TEMP", "TMP"];
  const previous = new Map(keys.map((key) => [key, process.env[key]]));
  for (const key of keys) process.env[key] = directory;
  try {
    return await operation();
  } finally {
    for (const key of keys) {
      const value = previous.get(key);
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

export async function startOwnedBrowser(browserType, runtime, {
  env = {}, channel, timeoutMs = 30_000,
} = {}) {
  if (!browserType || typeof browserType.launchServer !== "function"
    || typeof browserType.connect !== "function"
    || !Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new Error("owned browser launch configuration is invalid");
  }
  const runtimeAuthority = ownedRuntimeAuthority(runtime);
  const directories = await createOwnedBrowserDirectories(runtime, runtimeAuthority);
  const browserEnv = Object.fromEntries(Object.entries({
    PATH: env.PATH,
    SystemRoot: env.SystemRoot,
    WINDIR: env.WINDIR,
    NO_PROXY: env.NO_PROXY,
    no_proxy: env.no_proxy,
    HOME: directories.home,
    TMPDIR: directories.temporary,
    TEMP: directories.temporary,
    TMP: directories.temporary,
    XDG_CACHE_HOME: directories.cache,
    XDG_CONFIG_HOME: directories.config,
  }).filter(([, value]) => typeof value === "string" && value.length > 0));
  let server;
  try {
    server = await withOwnedProcessTemp(directories.temporary, () => browserType.launchServer({
      channel: channel || undefined,
      host: LOOPBACK_HOST,
      port: 0,
      timeout: timeoutMs,
      env: browserEnv,
      artifactsDir: directories.artifacts,
      downloadsPath: directories.downloads,
      tracesDir: directories.traces,
      args: [
        `--disk-cache-dir=${directories.diskCache}`,
        `--crash-dumps-dir=${directories.crashes}`,
      ],
    }));
  } catch {
    throw new Error("owned browser launch failed");
  }
  let browser;
  try {
    browser = await browserType.connect(server.wsEndpoint(), {
      exposeNetwork: "<loopback>",
      timeout: timeoutMs,
    });
  } catch {
    await killExactBrowserServer(server, timeoutMs);
    throw new Error("owned browser connection failed");
  }
  const owned = Object.freeze({
    directories,
    get connected() { return browser.isConnected?.() ?? true; },
  });
  BROWSER_AUTHORITIES.set(owned, {
    server, browser, directories, closePromise: null, closed: false,
  });
  return owned;
}

export async function newOwnedBrowserContext(owned, options = {}) {
  const authority = BROWSER_AUTHORITIES.get(owned);
  if (!authority || authority.closed) throw new Error("unowned browser handle");
  return authority.browser.newContext({ ...options, serviceWorkers: "block" });
}

export async function closeOwnedBrowser(owned, { timeoutMs = STOP_TIMEOUT_MS } = {}) {
  const authority = BROWSER_AUTHORITIES.get(owned);
  if (!authority) throw new Error("unowned browser handle");
  if (authority.closePromise) return authority.closePromise;
  authority.closePromise = (async () => {
    if (authority.closed) return;
    const graceful = await settleWithin(authority.server.close(), timeoutMs);
    if (graceful.kind === "fulfilled") {
      authority.closed = true;
      return;
    }
    await killExactBrowserServer(authority.server, timeoutMs);
    authority.closed = true;
    if (graceful.kind === "rejected") throw new Error("browser graceful close failed; exact browser server was killed");
    throw new Error("browser graceful close timed out; exact browser server was killed");
  })();
  return authority.closePromise;
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

function terminationState(values) {
  return Object.freeze(values);
}

export function advanceTerminationState(state, event) {
  const phase = state?.phase ?? "running";
  if (phase === "stopped" || phase === "failed") return state;
  if (phase === "running" && event?.type === "start") {
    return terminationState({ phase: "signal-term", effect: "signal", signal: "SIGTERM" });
  }
  if (phase === "signal-term" && event?.type === "signal-accepted") {
    return terminationState({ phase: "wait-term", effect: "wait", timeoutMs: STOP_TIMEOUT_MS });
  }
  if (phase === "signal-term" && event?.type === "signal-refused") {
    return terminationState({ phase: "failed", failure: "refused-sigterm" });
  }
  if (phase === "wait-term" && event?.type === "exited") {
    return terminationState({ phase: "stopped" });
  }
  if (phase === "wait-term" && event?.type === "deadline") {
    return terminationState({ phase: "signal-kill", effect: "signal", signal: "SIGKILL" });
  }
  if (phase === "signal-kill" && event?.type === "signal-accepted") {
    return terminationState({ phase: "wait-kill", effect: "wait", timeoutMs: STOP_TIMEOUT_MS });
  }
  if (phase === "signal-kill" && event?.type === "signal-refused") {
    return terminationState({ phase: "failed", failure: "refused-sigkill" });
  }
  if (phase === "wait-kill" && event?.type === "exited") {
    return terminationState({ phase: "stopped" });
  }
  if (phase === "wait-kill" && event?.type === "deadline") {
    return terminationState({ phase: "failed", failure: "sigkill-timeout" });
  }
  throw new Error("invalid process termination transition");
}

function stopFailure(authority, failure) {
  const name = boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES);
  if (failure === "refused-sigterm") return new Error(`${name} refused SIGTERM`);
  if (failure === "refused-sigkill") return new Error(`${name} refused SIGKILL`);
  return new Error(`${name} did not exit after SIGKILL within ${STOP_TIMEOUT_MS}ms`);
}

async function stopOwnedProcessImpl(authority) {
  authority.expectedStop = true;
  if (authority.exited) return;

  let state = advanceTerminationState(undefined, { type: "start" });
  while (state.phase !== "stopped" && state.phase !== "failed") {
    if (state.effect === "signal") {
      try {
        const accepted = authority.kill(state.signal);
        state = advanceTerminationState(state, {
          type: accepted ? "signal-accepted" : "signal-refused",
        });
      } catch (error) {
        if (error?.code === "ESRCH" && authority.exited) return;
        throw new Error(`${boundedDiagnostic(authority.name, MAX_DIAGNOSTIC_FIELD_BYTES)} failed to send ${state.signal}: ${boundedDiagnostic(error?.message ?? error, MAX_DIAGNOSTIC_FIELD_BYTES)}`);
      }
      continue;
    }
    const exited = await waitForExit(authority, state.timeoutMs);
    state = advanceTerminationState(state, {
      type: exited || authority.exited ? "exited" : "deadline",
    });
  }
  if (state.failure) throw stopFailure(authority, state.failure);
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

export function createTrafficAudit(uiBase) {
  const origin = assertLoopbackUrl(uiBase).origin;
  const websocketOrigin = origin.replace(/^http:/u, "ws:");
  const requests = [];
  const responses = [];
  const sanitizedUrl = (raw) => {
    let url;
    try {
      url = new URL(raw);
    } catch {
      throw new Error("unexpected malformed browser request URL");
    }
    return url;
  };
  const isAllowedLocalScheme = (url) => ["about:", "blob:", "data:"].includes(url.protocol);
  const assertAllowedRequest = (raw) => {
    const url = sanitizedUrl(raw);
    const hasExactAuthority = typeof raw === "string"
      && (raw === origin || raw.startsWith(`${origin}/`)
        || raw.startsWith(`${origin}?`) || raw.startsWith(`${origin}#`));
    if (!isAllowedLocalScheme(url)
      && (url.protocol !== "http:" || url.origin !== origin || !hasExactAuthority)) {
      throw new Error(`unexpected external request: ${url.origin}${url.pathname}`);
    }
    return url;
  };
  const assertAllowedWebSocket = (raw) => {
    const url = sanitizedUrl(raw);
    const hasExactAuthority = typeof raw === "string"
      && (raw === websocketOrigin || raw.startsWith(`${websocketOrigin}/`)
        || raw.startsWith(`${websocketOrigin}?`) || raw.startsWith(`${websocketOrigin}#`));
    if (url.protocol !== "ws:" || url.origin !== websocketOrigin || !hasExactAuthority) {
      throw new Error(`unexpected external WebSocket: ${url.origin}${url.pathname}`);
    }
    return url;
  };

  return Object.freeze({
    assertAllowedRequest,
    assertAllowedWebSocket,
    recordRequest(method, raw) {
      const url = assertAllowedRequest(raw);
      if (url.origin === origin && url.pathname.startsWith("/api/")) {
        requests.push(Object.freeze([method, url.pathname]));
      }
    },
    recordRequestFailure(method, raw) {
      const url = sanitizedUrl(raw);
      if (url.origin !== origin && !isAllowedLocalScheme(url)) {
        throw new Error(`unexpected external request failure: ${url.origin}${url.pathname}`);
      }
      if (url.origin === origin) {
        const label = url.pathname.startsWith("/api/") ? "API request failed" : "browser request failed";
        throw new Error(`${label}: ${method} ${url.pathname}`);
      }
    },
    recordResponse(status, method, raw) {
      const url = sanitizedUrl(raw);
      if (url.origin === origin && url.pathname.startsWith("/api/")) {
        if (!Number.isSafeInteger(status) || status < 200 || status >= 300) {
          throw new Error(`unexpected API response ${status} ${method} ${url.pathname}`);
        }
        responses.push(Object.freeze([status, method, url.pathname]));
      }
    },
    assertSingleton(method, pathname) {
      const count = requests.filter(([seenMethod, seenPath]) =>
        seenMethod === method && seenPath === pathname).length;
      if (count !== 1) throw new Error(`expected one ${method} ${pathname}; saw ${count}`);
    },
    requestCount(method, pathname) {
      return requests.filter(([seenMethod, seenPath]) =>
        seenMethod === method && seenPath === pathname).length;
    },
    snapshot() {
      return {
        requests: requests.map((entry) => [...entry]),
        responses: responses.map((entry) => [...entry]),
      };
    },
  });
}

export function createBrowserFailureCollector() {
  const errors = [];
  return Object.freeze({
    record(error) {
      if (errors.length < 20) {
        errors.push(error instanceof Error ? error : new Error("browser audit failure"));
      }
    },
    capture(operation) {
      try {
        operation();
      } catch (error) {
        this.record(error);
      }
    },
    throwIfAny() {
      if (errors.length > 0) throw errors[0];
    },
    count() { return errors.length; },
  });
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;

export function assertAlphabetIdentityBinding({ foundation, search, preview }) {
  if (!isRecord(foundation)
    || foundation.schema_version !== "product.foundation-identities.v1"
    || !SHA256_PATTERN.test(foundation.content_hash)
    || !Array.isArray(foundation.companies) || !Array.isArray(foundation.securities)
    || !isRecord(search) || !Array.isArray(search.items) || !isRecord(preview)) {
    throw new Error("Alphabet foundation identity contract is malformed");
  }
  const expectedCompanies = foundation.companies.filter((company) =>
    isRecord(company) && company.external_key === "US:ALPHABET:COMPANY");
  const expectedSecurities = foundation.securities.filter((security) =>
    isRecord(security) && security.company_key === "US:ALPHABET:COMPANY");
  if (expectedCompanies.length !== 1 || expectedCompanies[0].canonical_name !== "Alphabet Inc."
    || expectedSecurities.length !== 2
    || expectedSecurities.map((security) => security.external_key).sort().join(",")
      !== ["NASDAQ:GOOG", "NASDAQ:GOOGL"].join(",")) {
    throw new Error("Alphabet foundation identity contract is incomplete");
  }

  const companyMatches = search.items.filter((item) =>
    isRecord(item) && item.external_key === "US:ALPHABET:COMPANY");
  if (companyMatches.length !== 1) throw new Error("Alphabet company identity mismatch");
  const company = companyMatches[0];
  if (company.kind !== "company" || !UUID_PATTERN.test(company.object_id)
    || company.canonical_name !== expectedCompanies[0].canonical_name
    || !isRecord(preview.company) || preview.company.object_id !== company.object_id
    || preview.company.external_key !== company.external_key
    || preview.company.canonical_name !== company.canonical_name) {
    throw new Error("Alphabet company identity mismatch");
  }
  if (!Array.isArray(preview.securities) || preview.securities.length !== expectedSecurities.length) {
    throw new Error("Alphabet security identity mismatch");
  }

  const seenObjectIds = new Set();
  for (const expected of expectedSecurities) {
    const searched = search.items.filter((item) =>
      isRecord(item) && item.external_key === expected.external_key);
    const projected = preview.securities.filter((item) =>
      isRecord(item) && item.external_key === expected.external_key);
    if (searched.length !== 1 || projected.length !== 1) {
      throw new Error("Alphabet security identity mismatch");
    }
    const searchSecurity = searched[0];
    const previewSecurity = projected[0];
    const expectedFields = {
      canonical_name: expected.canonical_name,
      symbol: expected.symbol,
      exchange: expected.exchange,
      share_class: expected.share_class,
      trading_currency: expected.currency,
    };
    if (searchSecurity.kind !== "security" || !UUID_PATTERN.test(searchSecurity.object_id)
      || previewSecurity.object_id !== searchSecurity.object_id
      || previewSecurity.external_key !== searchSecurity.external_key
      || Object.entries(expectedFields).some(([field, value]) =>
        searchSecurity[field] !== value || previewSecurity[field] !== value)
      || seenObjectIds.has(searchSecurity.object_id)) {
      throw new Error("Alphabet security identity mismatch");
    }
    seenObjectIds.add(searchSecurity.object_id);
  }
  if (seenObjectIds.has(company.object_id)) throw new Error("Alphabet security identity mismatch");
  return company;
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

function reviewFacts(artifact, label) {
  if (!isRecord(artifact) || typeof artifact.id !== "string" || artifact.id.length === 0
    || !isSafePositiveInteger(artifact.version) || !isRecord(artifact.payload)
    || !Array.isArray(artifact.payload.facts)) {
    throw new Error(`review ${label} artifact is malformed`);
  }
  const facts = artifact.payload.facts;
  const keys = new Set();
  for (const fact of facts) {
    if (!isRecord(fact) || typeof fact.fact_key !== "string" || fact.fact_key.length === 0
      || keys.has(fact.fact_key)) {
      throw new Error(`review ${label} fact identities are invalid`);
    }
    keys.add(fact.fact_key);
  }
  return facts;
}

export function assertExactReviewSuccessor(before, after, factKey) {
  const previousFacts = reviewFacts(before, "previous");
  const nextFacts = reviewFacts(after, "successor");
  for (const [field, label] of [
    ["schema_version", "schema version"],
    ["project_id", "project id"],
    ["kind", "kind"],
  ]) {
    if (typeof before[field] !== "string" || before[field].length === 0
      || after[field] !== before[field]) {
      throw new Error(`review ${label} changed`);
    }
  }
  if (!Array.isArray(before.source_refs) || !Array.isArray(after.source_refs)
    || !jsonValuesEqual(after.source_refs, before.source_refs)) {
    throw new Error("review source refs changed");
  }
  if (after.id === before.id) throw new Error("review artifact identity did not advance");
  if (after.version !== before.version + 1) throw new Error("review version did not advance exactly once");
  if (previousFacts.length !== nextFacts.length) throw new Error("review fact cardinality changed");

  const previous = new Map(previousFacts.map((fact) => [fact.fact_key, fact]));
  const next = new Map(nextFacts.map((fact) => [fact.fact_key, fact]));
  const reviewedBefore = previous.get(factKey);
  const reviewedAfter = next.get(factKey);
  if (!reviewedBefore || !reviewedAfter
    || (reviewedBefore.review_decision ?? null) !== null
    || reviewedAfter.review_decision !== "confirmed") {
    throw new Error("reviewed fact successor mismatch");
  }

  for (const [key, previousFact] of previous) {
    const nextFact = next.get(key);
    if (!nextFact) throw new Error("review fact identity changed");
    if (key === factKey) {
      const expected = { ...previousFact, review_decision: "confirmed" };
      if (!jsonValuesEqual(nextFact, expected)) throw new Error("reviewed fact contents changed");
    } else if (!jsonValuesEqual(nextFact, previousFact)) {
      throw new Error("review changed an unrelated fact");
    }
  }
  const expectedPayload = {
    ...before.payload,
    facts: previousFacts.map((fact) =>
      fact.fact_key === factKey ? { ...fact, review_decision: "confirmed" } : fact),
  };
  if (!jsonValuesEqual(after.payload, expectedPayload)) {
    throw new Error("review fact payload changed outside the exact decision successor");
  }
  return after;
}

const NOT_ANSWERABLE_MODEL_ARTIFACTS = Object.freeze([
  "business_map",
  "driver_map",
  "evidence_index",
  "financial_bridge",
  "judgment_context",
  "memo",
  "research_gaps",
  "scenario_set",
]);

export function assertModelWorkspace(workspace) {
  if (!isRecord(workspace)
    || workspace.schema_version !== "underwriting.v1"
    || typeof workspace.project_id !== "string" || !UUID_PATTERN.test(workspace.project_id)) {
    throw new Error("model workspace identity mismatch");
  }
  if (!isRecord(workspace.preparation)
    || workspace.preparation.status !== "awaiting_judgment_review"
    || workspace.preparation.progress !== 85) {
    throw new Error("model workspace state mismatch");
  }
  if (!Array.isArray(workspace.artifacts)) throw new Error("model artifact set mismatch");
  const kinds = workspace.artifacts.map((artifact) => artifact?.kind).sort();
  if (kinds.length !== NOT_ANSWERABLE_MODEL_ARTIFACTS.length
    || !jsonValuesEqual(kinds, NOT_ANSWERABLE_MODEL_ARTIFACTS)) {
    throw new Error("model artifact set mismatch");
  }
  const identities = new Set();
  for (const artifact of workspace.artifacts) {
    if (!isRecord(artifact) || artifact.schema_version !== "underwriting.v1"
      || typeof artifact.id !== "string" || !UUID_PATTERN.test(artifact.id)
      || artifact.project_id !== workspace.project_id || identities.has(artifact.id)) {
      throw new Error("model artifact identity mismatch");
    }
    identities.add(artifact.id);
    if (!isSafePositiveInteger(artifact.version)) throw new Error("model artifact version mismatch");
    if (!isRecord(artifact.payload)) throw new Error("model artifact payload mismatch");
  }
  const memo = workspace.artifacts.find((artifact) => artifact.kind === "memo");
  const populatedInvestmentField = ["direction", "confidence", "target_value", "expected_return"]
    .some((field) => memo.payload[field] !== undefined && memo.payload[field] !== null);
  if (memo.payload.candidate_status !== "machine_draft"
    || memo.payload.assessment_status !== "not_answerable"
    || memo.payload.valuation_set_ref !== null
    || populatedInvestmentField) {
    throw new Error("not-answerable memo contract mismatch");
  }
  return workspace;
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
