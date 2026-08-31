import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
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
  }, "private runtime cleanup helper preflight", authority.cleanupHelper.preflightTimeoutMs);
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
  let finalPathStat;
  try {
    finalPathStat = await lstat(claimed);
  } catch (error) {
    if (isRuntimeIdentityOpenError(error)) privateRuntimeIdentityChanged();
    throw error;
  }
  if (!hasPrivateRuntimeIdentity(finalPathStat, authority)) privateRuntimeIdentityChanged();
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
    preflightTimeoutMs: Number.isSafeInteger(cleanupHelper.preflightTimeoutMs)
      && cleanupHelper.preflightTimeoutMs > 0
      ? cleanupHelper.preflightTimeoutMs
      : Number.isSafeInteger(cleanupHelper.timeoutMs) && cleanupHelper.timeoutMs > 0
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

async function readExactProcessGroupId(pid, timeoutMs) {
  if (!["darwin", "linux"].includes(process.platform) || !Number.isSafeInteger(pid) || pid < 1) {
    throw new Error("authenticated POSIX browser process group is unavailable");
  }
  const result = await runBoundedExactProcess(
    "/bin/ps",
    ["-o", "pid=,pgid=", "-p", String(pid)],
    { env: {}, stdio: ["ignore", "pipe", "pipe"] },
    "browser process group authentication",
    timeoutMs,
  );
  const fields = decodeBoundedUtf8(result.stdout).trim().split(/\s+/u);
  const [reportedPid, processGroupId] = fields.map((value) => Number(value));
  if (fields.length !== 2 || reportedPid !== pid || processGroupId !== pid) {
    throw new Error("Playwright browser process is not its authenticated POSIX group leader");
  }
  try {
    process.kill(pid, 0);
  } catch {
    throw new Error("authenticated POSIX browser process group exited during launch");
  }
  return Object.freeze({ childPid: pid, processGroupId });
}

async function cleanupUnauthenticatedBrowserServer(server, timeoutMs) {
  if (typeof server?.kill !== "function") {
    throw new Error("browser process group authentication failed and launch cleanup is unavailable");
  }
  const outcome = await settleWithin(Promise.resolve().then(() => server.kill()), timeoutMs);
  if (outcome.kind !== "fulfilled") {
    throw new Error("browser process group authentication failed and launch cleanup did not finish");
  }
}

function browserProcessGroupExists(capability) {
  try {
    process.kill(-capability.processGroupId, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    if (error?.code === "EPERM") return true;
    throw error;
  }
}

async function waitForBrowserProcessGroupAbsence(capability, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (browserProcessGroupExists(capability)) {
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, Math.min(10, deadline - Date.now())));
  }
  return true;
}

async function terminateExactBrowserProcessGroup(capability, timeoutMs) {
  if (!browserProcessGroupExists(capability)) return { signaled: false, escalated: false };
  try {
    process.kill(-capability.processGroupId, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw new Error("exact browser process group SIGTERM failed");
  }
  if (await waitForBrowserProcessGroupAbsence(capability, timeoutMs)) {
    return { signaled: true, escalated: false };
  }
  try {
    process.kill(-capability.processGroupId, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw new Error("exact browser process group SIGKILL failed");
  }
  if (!await waitForBrowserProcessGroupAbsence(capability, timeoutMs)) {
    throw new Error("exact browser process group remained after SIGKILL");
  }
  return { signaled: true, escalated: true };
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
      handleSIGINT: false,
      handleSIGTERM: false,
      handleSIGHUP: false,
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
  const browserProcess = typeof server.process === "function" ? server.process() : null;
  let processGroup;
  try {
    if (!browserProcess || !Number.isSafeInteger(browserProcess.pid)
      || browserProcess.exitCode !== null || browserProcess.signalCode !== null) {
      throw new Error("Playwright browser process group capability is unavailable");
    }
    processGroup = await readExactProcessGroupId(browserProcess.pid, timeoutMs);
  } catch (error) {
    await cleanupUnauthenticatedBrowserServer(server, timeoutMs);
    throw new Error(`owned browser process group authentication failed: ${error?.message ?? "unavailable"}`);
  }
  let browser;
  try {
    browser = await browserType.connect(server.wsEndpoint(), {
      exposeNetwork: "<loopback>",
      timeout: timeoutMs,
    });
  } catch {
    await terminateExactBrowserProcessGroup(processGroup, timeoutMs);
    throw new Error("owned browser connection failed");
  }
  const owned = Object.freeze({
    directories,
    get connected() { return browser.isConnected?.() ?? true; },
  });
  BROWSER_AUTHORITIES.set(owned, {
    server, browser, directories, processGroup, closePromise: null, closed: false,
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
    const graceful = await settleWithin(
      Promise.resolve().then(() => authority.server.close()),
      timeoutMs,
    );
    const groupRemained = browserProcessGroupExists(authority.processGroup);
    if (graceful.kind === "fulfilled" && !groupRemained) {
      authority.closed = true;
      return;
    }
    await terminateExactBrowserProcessGroup(authority.processGroup, timeoutMs);
    authority.closed = true;
    if (graceful.kind === "fulfilled") {
      throw new Error("browser graceful close left its exact process group; browser tree was killed");
    }
    if (graceful.kind === "rejected") throw new Error("browser graceful close failed; exact browser server was killed");
    throw new Error("browser graceful close timed out; exact browser server was killed");
  })();
  return authority.closePromise;
}

export function retainPrimaryFailure(promise) {
  void Promise.resolve(promise).catch(() => {});
  return promise;
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

export function assertProcessRunningBeforeIntentionalStop(owned) {
  assertProcessesRunning([owned]);
  return owned;
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

export function createPendingObserverTracker() {
  const pending = new Set();
  let failed = false;
  return Object.freeze({
    track(operation) {
      let tracked;
      tracked = Promise.resolve().then(operation).catch(() => {
        failed = true;
      }).finally(() => {
        pending.delete(tracked);
      });
      pending.add(tracked);
    },
    async drain({ timeoutMs }) {
      if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) {
        throw new Error("pending browser observer drain requires a positive timeout");
      }
      const deadline = Date.now() + timeoutMs;
      while (pending.size > 0) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) throw new Error("pending browser observer drain timed out");
        const timer = deadlineTimer(remaining);
        const outcome = await Promise.race([
          Promise.allSettled([...pending]).then(() => "settled"),
          timer.promise.then(() => "timeout"),
        ]);
        timer.cancel();
        if (outcome === "timeout") throw new Error("pending browser observer drain timed out");
      }
      if (failed) throw new Error("pending browser observer failed");
    },
  });
}

export function createSequencedResponseQueue({ maximumEntries = 64 } = {}) {
  if (!Number.isSafeInteger(maximumEntries) || maximumEntries < 1) {
    throw new Error("response queue bound must be a positive integer");
  }
  const authorities = new WeakMap();
  const entries = [];
  let sequence = 0;
  return Object.freeze({
    begin(metadata) {
      const token = Object.freeze({});
      authorities.set(token, { sequence: ++sequence, metadata });
      return token;
    },
    complete(token, value) {
      const authority = authorities.get(token);
      if (!authority) throw new Error("response queue token is invalid");
      authorities.delete(token);
      if (entries.length >= maximumEntries) throw new Error("response queue exceeded its bound");
      entries.push({ ...authority, value });
    },
    cursor() {
      return sequence;
    },
    takeAfter(after, predicate) {
      if (!Number.isSafeInteger(after) || after < 0 || typeof predicate !== "function") {
        throw new Error("response queue selection is invalid");
      }
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        if (entries[index].sequence <= after) entries.splice(index, 1);
      }
      const index = entries.findIndex((entry) => predicate(entry));
      if (index < 0) return null;
      return entries.splice(index, 1)[0].value;
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

const ARTIFACT_KEYS = Object.freeze([
  "schema_version", "id", "project_id", "kind", "version", "input_hash",
  "content_hash", "payload", "source_refs",
]);
const SOURCE_REF_KEYS = Object.freeze(["raw_hash", "source_locator", "source_role", "source_url"]);
const LINEAGE_SOURCE_REF_KEYS = Object.freeze([...SOURCE_REF_KEYS, "fact_key"]);
const LINEAGE_KEYS = Object.freeze(["artifact_refs", "market_snapshot_ids", "market_snapshot_bindings"]);
const LINEAGE_PARENT_KEYS = Object.freeze(["artifact_id", "artifact_kind", "content_hash"]);
const MARKET_BINDING_KEYS = Object.freeze([
  "snapshot_id", "snapshot_kind", "snapshot_content_hash", "security_external_key", "source_ref",
  "capture_envelope_id", "capture_content_hash", "provenance_role", "provider_policy_version", "raw_components",
]);
const RAW_COMPONENT_KEYS = Object.freeze(["raw_file", "raw_hash", "source_url", "source_locator"]);

function hasExactKeys(value, keys) {
  return isRecord(value) && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key));
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function canonicalHash(value) {
  const canonicalize = (item) => Array.isArray(item)
    ? item.map(canonicalize)
    : isRecord(item)
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonicalize(item[key])]))
      : item;
  return createHash("sha256").update(JSON.stringify(canonicalize(value))).digest("hex");
}

function isStringArray(value, { nonempty = false } = {}) {
  return Array.isArray(value) && (!nonempty || value.length > 0)
    && value.every((item) => isNonEmptyString(item));
}

function isDateTime(value) {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.exec(value);
  if (!parts || !Number.isFinite(Date.parse(value))) return false;
  const wallClock = new Date(Date.UTC(
    Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]),
    Number(parts[4]), Number(parts[5]), Number(parts[6]),
  ));
  return wallClock.getUTCFullYear() === Number(parts[1])
    && wallClock.getUTCMonth() + 1 === Number(parts[2])
    && wallClock.getUTCDate() === Number(parts[3])
    && wallClock.getUTCHours() === Number(parts[4])
    && wallClock.getUTCMinutes() === Number(parts[5])
    && wallClock.getUTCSeconds() === Number(parts[6]);
}

function canonicalDecimal(value) {
  if (typeof value !== "string") return null;
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/u.exec(value.trim());
  if (!match) return null;
  const integer = match[2].replace(/^0+(?=\d)/u, "");
  const fraction = (match[3] ?? "").replace(/0+$/u, "");
  const zero = integer === "0" && fraction === "";
  return `${match[1] === "-" && !zero ? "-" : ""}${integer}${fraction ? `.${fraction}` : ""}`;
}

function isDateOnly(value) {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(value);
  if (!match) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return date.getUTCFullYear() === Number(match[1])
    && date.getUTCMonth() + 1 === Number(match[2])
    && date.getUTCDate() === Number(match[3]);
}

function isSourceRef(value, withFactKey = false) {
  const keys = withFactKey ? LINEAGE_SOURCE_REF_KEYS : SOURCE_REF_KEYS;
  return hasExactKeys(value, keys) && SHA256_PATTERN.test(value.raw_hash)
    && keys.filter((key) => key !== "raw_hash")
      .every((key) => isNonEmptyString(value[key]) && value[key] === value[key].trim());
}

function sourceRefIdentity(value) {
  return JSON.stringify([value.source_role, value.source_url, value.source_locator, value.raw_hash]);
}

function canonicalSourceRefs(values) {
  const unique = new Map(values.map((value) => [sourceRefIdentity(value), value]));
  return [...unique.keys()].sort().map((identity) => unique.get(identity));
}

function assertArtifactEnvelope(artifact, label) {
  if (!hasExactKeys(artifact, ARTIFACT_KEYS)) throw new Error(`${label} artifact envelope mismatch`);
  if (artifact.schema_version !== "underwriting.v1" || !UUID_PATTERN.test(artifact.id)
    || !UUID_PATTERN.test(artifact.project_id) || !isNonEmptyString(artifact.kind)) {
    throw new Error(`${label} artifact identity mismatch`);
  }
  if (!isSafePositiveInteger(artifact.version)) throw new Error(`${label} artifact version mismatch`);
  if (!SHA256_PATTERN.test(artifact.input_hash) || !SHA256_PATTERN.test(artifact.content_hash)) {
    throw new Error(`${label} artifact hash mismatch`);
  }
  if (!isRecord(artifact.payload)) throw new Error(`${label} artifact payload mismatch`);
  if (!Array.isArray(artifact.source_refs) || artifact.source_refs.length === 0
    || !artifact.source_refs.every((ref) => isSourceRef(ref))) {
    throw new Error(`${label} artifact source refs mismatch`);
  }
  const sourceIdentities = artifact.source_refs.map(sourceRefIdentity);
  if (new Set(sourceIdentities).size !== sourceIdentities.length) {
    throw new Error(`${label} artifact source refs mismatch`);
  }
  return artifact;
}

function isNumericSource(value) {
  if (!isRecord(value) || value.kind !== "external") return false;
  const { kind: _kind, ...ref } = value;
  return isSourceRef(ref, true);
}

function isComputationSource(value, lineage) {
  if (!hasExactKeys(value, ["kind", "artifact_refs", "market_snapshot_ids", "equation_id"])
    || value.kind !== "artifact_computation" || !isNonEmptyString(value.equation_id)
    || !Array.isArray(value.artifact_refs) || value.artifact_refs.length === 0
    || !value.artifact_refs.every((ref) => hasExactKeys(ref, LINEAGE_PARENT_KEYS)
      && UUID_PATTERN.test(ref.artifact_id) && isNonEmptyString(ref.artifact_kind)
      && SHA256_PATTERN.test(ref.content_hash))
    || new Set(value.artifact_refs.map((ref) => ref.artifact_id)).size !== value.artifact_refs.length
    || !Array.isArray(value.market_snapshot_ids)
    || !value.market_snapshot_ids.every((id) => UUID_PATTERN.test(id))
    || new Set(value.market_snapshot_ids).size !== value.market_snapshot_ids.length) return false;
  return !lineage || (jsonValuesEqual(value.artifact_refs, lineage.artifact_refs)
    && jsonValuesEqual(value.market_snapshot_ids, lineage.market_snapshot_ids));
}

function isNumericObservation(value, computationLineage) {
  if (!hasExactKeys(value, [
    "key", "value", "unit", "currency", "period", "state", "source_ref", "gap_key", "assumption_key",
  ]) || !isNonEmptyString(value.key) || !isNonEmptyString(value.value)
    || canonicalDecimal(value.value) !== value.value
    || !isNonEmptyString(value.unit) || !isNonEmptyString(value.currency)
    || !isNonEmptyString(value.period) || !["reported", "derived", "assumption", "gap"].includes(value.state)) return false;
  const provenanceCount = [value.source_ref, value.gap_key, value.assumption_key]
    .filter((item) => item !== null).length;
  if (provenanceCount !== 1) return false;
  if (value.state === "reported") return isNumericSource(value.source_ref);
  if (value.state === "assumption") return isNonEmptyString(value.assumption_key);
  if (value.state === "gap") return isNonEmptyString(value.gap_key);
  return isComputationSource(value.source_ref, computationLineage);
}

function isEvidenceFact(value, { requireReviewed = false } = {}) {
  const keys = [
    "fact_key", "company_external_key", "business_module", "metric_key", "observation",
    "period_start", "period_end", "published_at", "available_at", "source_role", "source_url",
    "source_locator", "raw_hash",
  ];
  const exact = hasExactKeys(value, keys) || hasExactKeys(value, [...keys, "review_decision"]);
  if (!exact || !["fact_key", "company_external_key", "business_module", "metric_key", "source_role", "source_url", "source_locator"]
    .every((key) => isNonEmptyString(value[key]))
    || !isNumericObservation(value.observation)
    || !isDateOnly(value.period_start) || !isDateOnly(value.period_end) || value.period_start > value.period_end
    || !isDateTime(value.published_at) || !isDateTime(value.available_at)
    || Date.parse(value.published_at) > Date.parse(value.available_at)
    || !SHA256_PATTERN.test(value.raw_hash)) return false;
  const source = value.observation.source_ref;
  if (value.observation.key !== value.metric_key
    || value.observation.period !== `${value.period_start}/${value.period_end}`
    || !isRecord(source) || source.kind !== "external"
    || source.fact_key !== value.fact_key
    || ["source_role", "source_url", "source_locator", "raw_hash"]
      .some((key) => source[key] !== value[key])) return false;
  if (requireReviewed && !["confirmed", "rejected"].includes(value.review_decision)) return false;
  return !("review_decision" in value) || ["confirmed", "rejected"].includes(value.review_decision);
}

function isEvidencePayload(payload, sourceRefs, { requireReviewed = false } = {}) {
  if (!hasExactKeys(payload, [
    "fixture_content_hash", "cutoff", "company_external_key", "security_external_keys", "facts",
  ]) || !SHA256_PATTERN.test(payload.fixture_content_hash) || !isDateTime(payload.cutoff)
    || !isNonEmptyString(payload.company_external_key)
    || !isStringArray(payload.security_external_keys, { nonempty: true })
    || new Set(payload.security_external_keys).size !== payload.security_external_keys.length
    || !Array.isArray(payload.facts) || payload.facts.length === 0
    || !payload.facts.every((fact) => isEvidenceFact(fact, { requireReviewed }))) return false;
  const factKeys = payload.facts.map((fact) => fact.fact_key);
  if (new Set(factKeys).size !== factKeys.length) return false;
  const sourceIdentities = new Set(sourceRefs.map(sourceRefIdentity));
  if (!payload.facts.every((fact) => {
    const source = fact.observation?.source_ref;
    if (!isRecord(source) || source.kind !== "external") return false;
    const { kind: _kind, fact_key: _factKey, ...ref } = source;
    return sourceIdentities.has(sourceRefIdentity(ref));
  })) return false;
  const governedSources = canonicalSourceRefs(payload.facts.map((fact) => ({
    source_role: fact.source_role,
    source_url: fact.source_url,
    source_locator: fact.source_locator,
    raw_hash: fact.raw_hash,
  })));
  return jsonValuesEqual(sourceRefs, governedSources);
}

function isLineageSourceRefs(value) {
  return Array.isArray(value) && value.every((ref) => isSourceRef(ref, true))
    && new Set(value.map(lineageRefIdentity)).size === value.length;
}

function isLineageSourceRefArray(value) {
  return Array.isArray(value) && value.every((ref) => isSourceRef(ref, true));
}

function isArtifactMemoRef(value, kind, registry) {
  return hasExactKeys(value, ["artifact_kind", "content_hash"])
    && value.artifact_kind === kind && SHA256_PATTERN.test(value.content_hash)
    && registry.has(kind);
}

function isMarketBinding(value, expectedSnapshotId) {
  if (!hasExactKeys(value, MARKET_BINDING_KEYS) || value.snapshot_id !== expectedSnapshotId
    || !["price", "fx", "capital_structure", "security_rights"].includes(value.snapshot_kind)
    || !SHA256_PATTERN.test(value.snapshot_content_hash)
    || !isSourceRef(value.source_ref, true) || !UUID_PATTERN.test(value.capture_envelope_id)
    || !SHA256_PATTERN.test(value.capture_content_hash) || value.provenance_role !== "primary"
    || !isNonEmptyString(value.provider_policy_version)
    || !Array.isArray(value.raw_components)
    || !value.raw_components.every((component) => hasExactKeys(component, RAW_COMPONENT_KEYS)
      && isNonEmptyString(component.raw_file) && SHA256_PATTERN.test(component.raw_hash)
      && isNonEmptyString(component.source_url) && isNonEmptyString(component.source_locator))) return false;
  const requiresSecurity = ["price", "security_rights"].includes(value.snapshot_kind);
  return requiresSecurity ? isNonEmptyString(value.security_external_key) : value.security_external_key === null;
}

function assertArtifactLineage(payload, expectedKinds, registry, label) {
  const lineage = payload?._lineage;
  if (!hasExactKeys(lineage, LINEAGE_KEYS) || !Array.isArray(lineage.artifact_refs)
    || lineage.artifact_refs.length !== expectedKinds.length
    || !Array.isArray(lineage.market_snapshot_ids)
    || !lineage.market_snapshot_ids.every((id) => UUID_PATTERN.test(id))
    || new Set(lineage.market_snapshot_ids).size !== lineage.market_snapshot_ids.length
    || !Array.isArray(lineage.market_snapshot_bindings)
    || lineage.market_snapshot_bindings.length !== lineage.market_snapshot_ids.length) {
    throw new Error(`${label} artifact lineage mismatch`);
  }
  for (const [index, kind] of expectedKinds.entries()) {
    const ref = lineage.artifact_refs[index];
    const parent = registry.get(kind);
    if (!hasExactKeys(ref, LINEAGE_PARENT_KEYS) || !parent
      || ref.artifact_kind !== kind || ref.artifact_id !== parent.id
      || ref.content_hash !== parent.content_hash) {
      throw new Error(`${label} artifact lineage mismatch`);
    }
  }
  for (const [index, binding] of lineage.market_snapshot_bindings.entries()) {
    if (!isMarketBinding(binding, lineage.market_snapshot_ids[index])) {
      throw new Error(`${label} artifact lineage mismatch`);
    }
  }
  return lineage;
}

function isResearchGapsPayload(payload) {
  return hasExactKeys(payload, ["fixture_content_hash", "company_external_key", "gaps"])
    && SHA256_PATTERN.test(payload.fixture_content_hash) && isNonEmptyString(payload.company_external_key)
    && Array.isArray(payload.gaps) && payload.gaps.every((gap) =>
      hasExactKeys(gap, ["gap_key", "business_module", "reason"])
      && ["gap_key", "business_module", "reason"].every((key) => isNonEmptyString(gap[key])));
}

function isBusinessMapPayload(payload) {
  if (!(hasExactKeys(payload, ["modules", "_lineage"])
    && Array.isArray(payload.modules) && payload.modules.length > 0
    && payload.modules.every((module) => hasExactKeys(module, [
      "module_key", "revenue_sources", "cost_structure", "capital_needs", "fact_refs", "gap_refs", "classified_evidence",
    ]) && isNonEmptyString(module.module_key)
      && isStringArray(module.revenue_sources, { nonempty: true })
      && isStringArray(module.cost_structure, { nonempty: true })
      && isStringArray(module.capital_needs, { nonempty: true })
      && isLineageSourceRefs(module.fact_refs) && isStringArray(module.gap_refs)
      && Array.isArray(module.classified_evidence)
      && module.classified_evidence.every((item) => hasExactKeys(item, [
        "fact_ref", "metric_key", "category", "observation", "period_start", "period_end",
      ]) && isSourceRef(item.fact_ref, true) && isNonEmptyString(item.metric_key)
        && ["revenue", "cost", "capital"].includes(item.category)
        && isNumericObservation(item.observation, payload._lineage) && isDateOnly(item.period_start)
        && isDateOnly(item.period_end) && item.period_start <= item.period_end)))) return false;
  if (new Set(payload.modules.map((module) => module.module_key)).size !== payload.modules.length) return false;
  return payload.modules.every((module) => {
    const facts = module.fact_refs.map(lineageRefIdentity);
    const classified = module.classified_evidence.map((item) => lineageRefIdentity(item.fact_ref));
    return new Set(module.gap_refs).size === module.gap_refs.length
      && classified.length === facts.length
      && jsonValuesEqual([...classified].sort(), [...facts].sort())
      && module.classified_evidence.every((item) => item.observation.key === item.metric_key
        && item.observation.period === `${item.period_start}/${item.period_end}`
        && item.observation.state === "reported");
  });
}

function isDriverMapPayload(payload) {
  const equations = new Set([
    "revenue = volume * monetization", "operating_income = revenue * operating_margin",
    "cash_tax_rate = reported_tax_rate", "depreciation = reported_depreciation",
    "capex = reported_capex", "working_capital_change = reported_working_capital_change",
    "fcff = nopat + depreciation - capex - working_capital_change", "reported_value = reviewed_fact",
  ]);
  if (!(hasExactKeys(payload, ["drivers", "_lineage"])
    && Array.isArray(payload.drivers) && payload.drivers.length > 0
    && payload.drivers.every((driver) => hasExactKeys(driver, [
      "driver_key", "module_key", "fact_refs", "assumption_refs", "equation", "output_metric",
      "equation_id", "values", "assumption_rationale", "assumption_equation",
    ]) && ["driver_key", "module_key", "equation", "output_metric"].every((key) => isNonEmptyString(driver[key]))
      && isLineageSourceRefs(driver.fact_refs) && isLineageSourceRefs(driver.assumption_refs)
      && (driver.equation_id === null || isNonEmptyString(driver.equation_id))
      && Array.isArray(driver.values) && driver.values.length > 0
      && driver.values.every((value) => isNumericObservation(value, payload._lineage))
      && (driver.assumption_rationale === null || isNonEmptyString(driver.assumption_rationale))
      && (driver.assumption_equation === null || isNonEmptyString(driver.assumption_equation))))) return false;
  if (new Set(payload.drivers.map((driver) => driver.driver_key)).size !== payload.drivers.length) return false;
  return payload.drivers.every((driver) => {
    const states = new Set(driver.values.map((value) => value.state));
    if (!equations.has(driver.equation) || states.size !== 1
      || driver.values.some((value) => value.key !== driver.output_metric)
      || Boolean(driver.assumption_rationale) !== Boolean(driver.assumption_equation)) return false;
    const [state] = states;
    if (state === "reported") {
      return driver.equation_id === null && driver.assumption_refs.length === 0
        && driver.fact_refs.length === driver.values.length
        && driver.values.every((value, index) => {
          const { kind: _kind, ...source } = value.source_ref;
          return jsonValuesEqual(source, driver.fact_refs[index]);
        });
    }
    if (state === "derived") {
      return driver.fact_refs.length > 0 && driver.assumption_refs.length === 0
        && equations.has(driver.equation_id);
    }
    return state === "assumption" && driver.fact_refs.length === 0
      && driver.assumption_refs.length > 0
      && /^[a-z][a-z0-9_.-]*\.v[1-9][0-9]*:[a-z][a-z0-9_]*$/u.test(driver.values[0].assumption_key)
      && driver.values.every((value) => value.assumption_key === driver.values[0].assumption_key)
      && driver.equation_id === null;
  });
}

const FINANCIAL_OBSERVATION_KEYS = Object.freeze([
  "revenue", "operating_income", "cash_tax_rate", "depreciation", "capex", "working_capital_change", "fcff",
]);

function isFinancialBridgePayload(payload) {
  if (!(hasExactKeys(payload, ["rows", "_lineage"])
    && Array.isArray(payload.rows) && payload.rows.length === 5
    && payload.rows.every((row) => hasExactKeys(row, [
      "period", ...FINANCIAL_OBSERVATION_KEYS, "fact_refs", "assumption_refs",
    ]) && isNonEmptyString(row.period)
      && FINANCIAL_OBSERVATION_KEYS.every((key) => isNumericObservation(row[key], payload._lineage))
      && isLineageSourceRefs(row.fact_refs) && isLineageSourceRefs(row.assumption_refs)))) return false;
  const years = payload.rows.map((row) => /^FY([0-9]{4})$/u.exec(row.period));
  if (years.some((match) => match === null)) return false;
  const first = Number(years[0][1]);
  return payload.rows.every((row, index) => Number(years[index][1]) === first + index
    && FINANCIAL_OBSERVATION_KEYS.every((key) => row[key].key === key && row[key].period === row.period)
    && ["operating_income", "fcff"].every((key) => row[key].state === "derived"));
}

function isScenarioSetPayload(payload) {
  if (!hasExactKeys(payload, ["scenarios", "_lineage"])
    || !Array.isArray(payload.scenarios) || payload.scenarios.length !== 3) return false;
  const scenarioIds = payload.scenarios.map((scenario) => scenario?.scenario_id).sort();
  const mechanisms = payload.scenarios.map((scenario) => scenario?.mechanism_id);
  if (!jsonValuesEqual(scenarioIds, ["base", "bear", "bull"])
    || new Set(mechanisms).size !== payload.scenarios.length) return false;
  return payload.scenarios.every((scenario) => hasExactKeys(scenario, ["scenario_id", "mechanism_id", "driver_overrides"])
    && isNonEmptyString(scenario.mechanism_id)
    && Array.isArray(scenario.driver_overrides) && scenario.driver_overrides.length > 0
    && new Set(scenario.driver_overrides.map((item) => item.driver_key)).size === scenario.driver_overrides.length
    && scenario.driver_overrides.every((item) => hasExactKeys(item, ["driver_key", "observation", "rationale", "equation"])
      && isNonEmptyString(item.driver_key) && isNumericObservation(item.observation, payload._lineage)
      && item.observation.key === item.driver_key && ["assumption", "derived"].includes(item.observation.state)
      && item.observation.unit === "multiplier" && item.observation.currency === "N/A"
      && (item.rationale === null || isNonEmptyString(item.rationale))
      && (item.equation === null || isNonEmptyString(item.equation))));
}

function isJudgmentContextPayload(payload) {
  return hasExactKeys(payload, [
    "operating_baseline_available", "financial_bridge_closed", "market_security_bridge_available",
    "strongest_counterevidence", "next_verification_events", "_lineage",
  ]) && ["operating_baseline_available", "financial_bridge_closed", "market_security_bridge_available"]
    .every((key) => typeof payload[key] === "boolean")
    && isLineageSourceRefArray(payload.strongest_counterevidence)
    && isStringArray(payload.next_verification_events);
}

function isMachineMemoPayload(payload, registry) {
  return hasExactKeys(payload, [
    "assessment_status", "business_map_ref", "driver_map_ref", "financial_bridge_ref",
    "scenario_set_ref", "valuation_set_ref", "gap_keys", "strongest_counterevidence",
    "next_verification_events", "candidate_status", "_lineage",
  ]) && payload.assessment_status === "not_answerable" && payload.candidate_status === "machine_draft"
    && payload.valuation_set_ref === null
    && isArtifactMemoRef(payload.business_map_ref, "business_map", registry)
    && isArtifactMemoRef(payload.driver_map_ref, "driver_map", registry)
    && isArtifactMemoRef(payload.financial_bridge_ref, "financial_bridge", registry)
    && isArtifactMemoRef(payload.scenario_set_ref, "scenario_set", registry)
    && isStringArray(payload.gap_keys) && isLineageSourceRefArray(payload.strongest_counterevidence)
    && isStringArray(payload.next_verification_events);
}

function withoutLineage(payload) {
  const { _lineage: _lineage, ...domainPayload } = payload;
  return domainPayload;
}

function storedBusinessPayload(payload) {
  return {
    ...payload,
    modules: payload.modules.map((module) => ({
      ...module,
      classified_evidence: module.classified_evidence.map((item) => {
        const { observation, ...stored } = item;
        return { ...stored, value: observation.value, currency: observation.currency, unit: observation.unit };
      }),
    })),
  };
}

function storedDriverPayload(payload) {
  return {
    ...payload,
    drivers: payload.drivers.map((driver) => {
      const state = driver.values[0].state;
      return {
        ...driver,
        input_state: state,
        assumption_key: state === "assumption" ? driver.values[0].assumption_key : null,
        values: driver.values.map((value) => value.value),
      };
    }),
  };
}

const SCENARIO_FINANCIAL_DRIVER_KEYS = Object.freeze([
  "revenue", "operating_margin", "cash_tax_rate", "depreciation", "capex", "working_capital_change",
]);

function storedFinancialPayload(payload, storedDriver) {
  const statesByDriver = new Map(storedDriver.drivers.map((driver) => [driver.driver_key, driver.input_state]));
  return {
    ...payload,
    rows: payload.rows.map((row) => {
      const governedStates = SCENARIO_FINANCIAL_DRIVER_KEYS.map((key) => statesByDriver.get(key));
      if (governedStates.some((state) => state === undefined)) {
        throw new Error("financial_bridge payload lacks governed driver provenance");
      }
      return {
        fiscal_year: Number(row.period.replace(/^FY/u, "")),
        ...Object.fromEntries(FINANCIAL_OBSERVATION_KEYS.map((key) => [key, row[key].value])),
        fact_refs: row.fact_refs,
        assumption_refs: row.assumption_refs,
        input_states: [...new Set(governedStates)],
      };
    }),
  };
}

function storedScenarioPayload(payload) {
  return {
    ...payload,
    scenarios: payload.scenarios.map((scenario) => ({
      ...scenario,
      driver_overrides: scenario.driver_overrides.map((override) => ({
        driver_key: override.driver_key,
        value: override.observation.value,
        state: override.observation.state,
        assumption_key: override.observation.state === "assumption"
          ? override.observation.assumption_key
          : null,
        rationale: override.rationale,
        equation: override.equation,
      })),
    })),
  };
}

function artifactRootContentHash(artifact, storedPayload) {
  return canonicalHash({
    schema_version: "company-research-artifact.v1",
    project_id: artifact.project_id,
    kind: artifact.kind,
    version: artifact.version,
    supersedes_id: null,
    parent_content_hash: null,
    input_hash: artifact.input_hash,
    payload: storedPayload,
    source_refs: artifact.source_refs,
  });
}

function assertRootArtifactContent(artifact, storedPayload, label) {
  if (artifact.version !== 1 || artifact.content_hash !== artifactRootContentHash(artifact, storedPayload)) {
    throw new Error(`${label} content hash mismatch`);
  }
}

function lineageRefIdentity(value) {
  return JSON.stringify([
    value.fact_key, value.source_role, value.source_url, value.source_locator, value.raw_hash,
  ]);
}

function builderGapMessage(key) {
  for (const [prefix, message] of [
    ["builder_generated_operating_baseline_missing_", "Reviewed operating baseline is missing: "],
    ["builder_generated_missing_module_evidence_", "No confirmed evidence or governed gap covers "],
    ["builder_generated_operating_driver_missing_", "Confirmed numeric input is missing for "],
  ]) {
    if (key.startsWith(prefix)) return `${message}${key.slice(prefix.length)}`;
  }
  return null;
}

function closedMemoGaps({ gaps, business, judgment, memo, hasMarket }) {
  const gapKeys = memo.payload.gap_keys;
  if ([gapKeys, judgment.payload.next_verification_events, memo.payload.next_verification_events]
    .some((values) => !Array.isArray(values) || values.length !== gapKeys.length)
    || !jsonValuesEqual(memo.payload.next_verification_events, judgment.payload.next_verification_events)
    || !jsonValuesEqual([...gapKeys].sort(), gapKeys)
    || new Set(gapKeys).size !== gapKeys.length) {
    throw new Error("model memo gap semantics mismatch");
  }
  const sourceGaps = new Map(gaps.payload.gaps.map((gap) => [gap.gap_key, gap]));
  const moduleByGap = new Map();
  for (const module of business.payload.modules) {
    for (const key of module.gap_refs) {
      if (moduleByGap.has(key)) throw new Error("model memo gap semantics mismatch");
      moduleByGap.set(key, module.module_key);
    }
  }
  if (moduleByGap.size !== gapKeys.length || gapKeys.some((key) => !moduleByGap.has(key))) {
    throw new Error("model memo gap semantics mismatch");
  }
  const active = gapKeys.map((key, index) => {
    const source = sourceGaps.get(key);
    const generatedMessage = builderGapMessage(key);
    const message = judgment.payload.next_verification_events[index];
    if (source) {
      if (source.business_module !== moduleByGap.get(key) || source.reason !== message) {
        throw new Error("model memo gap semantics mismatch");
      }
      return { code: key, module_key: source.business_module, severity: "high", message };
    }
    if (generatedMessage === null || generatedMessage !== message) {
      throw new Error("model memo gap semantics mismatch");
    }
    return { code: key, module_key: moduleByGap.get(key), severity: "critical", message };
  });
  const closedSourceKeys = new Set(["forward_model_missing"]);
  if (hasMarket) {
    closedSourceKeys.add("market_price_missing");
    closedSourceKeys.add("usd_cny_fx_missing");
  }
  if ([...sourceGaps.keys()].some((key) => !gapKeys.includes(key) && !closedSourceKeys.has(key))) {
    throw new Error("model memo gap semantics mismatch");
  }
  return active;
}

function reviewFacts(artifact, label) {
  assertArtifactEnvelope(artifact, "review");
  if (artifact.kind !== "evidence_index" || !isEvidencePayload(artifact.payload, artifact.source_refs)) {
    throw new Error(`review ${label} fact payload is malformed`);
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

function storedEvidencePayload(projectedPayload) {
  return {
    ...projectedPayload,
    cutoff: projectedPayload.cutoff.endsWith("Z")
      ? `${projectedPayload.cutoff.slice(0, -1)}+00:00`
      : projectedPayload.cutoff,
    facts: projectedPayload.facts.map((fact) => {
      const { observation, ...storedFact } = fact;
      return {
        ...storedFact,
        published_at: fact.published_at.endsWith("Z")
          ? `${fact.published_at.slice(0, -1)}+00:00`
          : fact.published_at,
        available_at: fact.available_at.endsWith("Z")
          ? `${fact.available_at.slice(0, -1)}+00:00`
          : fact.available_at,
        value: observation.value,
        value_kind: observation.state,
        currency: observation.currency,
        unit: observation.unit,
      };
    }),
  };
}

export function assertExactReviewSuccessor(before, after, factKey) {
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
  const previousFacts = reviewFacts(before, "previous");
  const nextFacts = reviewFacts(after, "successor");
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
  const expectedInputHash = canonicalHash({
    parent: before.content_hash,
    fact_key: factKey,
    decision: "confirmed",
  });
  if (after.input_hash !== expectedInputHash) throw new Error("review input hash mismatch");

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
  const expectedContentHash = canonicalHash({
    schema_version: "company-research-artifact.v1",
    project_id: after.project_id,
    kind: after.kind,
    version: after.version,
    supersedes_id: before.id,
    parent_content_hash: before.content_hash,
    input_hash: after.input_hash,
    payload: storedEvidencePayload(after.payload),
    source_refs: after.source_refs,
  });
  if (after.content_hash !== expectedContentHash) throw new Error("review content hash mismatch");
  return after;
}

export function assertSameArtifactHead(expected, actual) {
  assertArtifactEnvelope(expected, "expected");
  assertArtifactEnvelope(actual, "actual");
  if (!jsonValuesEqual(actual, expected)) throw new Error("authenticated artifact head mismatch");
  return expected;
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

export function assertModelWorkspace(workspace, expected) {
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
    assertArtifactEnvelope(artifact, "model");
    if (artifact.project_id !== workspace.project_id || identities.has(artifact.id)) {
      throw new Error("model artifact identity mismatch");
    }
    identities.add(artifact.id);
  }
  const registry = new Map(workspace.artifacts.map((artifact) => [artifact.kind, artifact]));
  const modelSourceKinds = [
    "business_map", "driver_map", "financial_bridge", "scenario_set", "judgment_context", "memo",
  ];
  const evidence = registry.get("evidence_index");
  if (!isRecord(expected) || !isRecord(expected.evidenceArtifact)) {
    throw new Error("expected reviewed evidence head is required");
  }
  try {
    assertArtifactEnvelope(expected.evidenceArtifact, "expected reviewed evidence");
  } catch {
    throw new Error("expected reviewed evidence head is invalid");
  }
  if (!isEvidencePayload(expected.evidenceArtifact.payload, expected.evidenceArtifact.source_refs, { requireReviewed: true })
    || !jsonValuesEqual(evidence, expected.evidenceArtifact)) {
    throw new Error("model workspace reviewed evidence head mismatch");
  }
  if (!isEvidencePayload(evidence.payload, evidence.source_refs, { requireReviewed: true })) {
    throw new Error("evidence_index payload mismatch");
  }
  const gaps = registry.get("research_gaps");
  if (!isRecord(expected.researchGapsArtifact)) {
    throw new Error("expected research gaps head is required");
  }
  try {
    assertArtifactEnvelope(expected.researchGapsArtifact, "expected research gaps");
  } catch {
    throw new Error("expected research gaps head is invalid");
  }
  if (!jsonValuesEqual(gaps, expected.researchGapsArtifact)) {
    throw new Error("model workspace research gaps head mismatch");
  }
  if (!isResearchGapsPayload(gaps.payload)) {
    throw new Error("research_gaps payload mismatch");
  }
  assertRootArtifactContent(gaps, gaps.payload, "research_gaps");
  if (!jsonValuesEqual(gaps.source_refs, canonicalSourceRefs(gaps.source_refs))) {
    throw new Error("research_gaps source refs mismatch");
  }
  const business = registry.get("business_map");
  if (!isBusinessMapPayload(business.payload)) throw new Error("business_map payload mismatch");
  const modelLineage = assertArtifactLineage(
    business.payload, ["evidence_index"], registry, "business_map",
  );
  const assertSharedMarketLineage = (lineage, label) => {
    if (!jsonValuesEqual(lineage.market_snapshot_ids, modelLineage.market_snapshot_ids)
      || !jsonValuesEqual(lineage.market_snapshot_bindings, modelLineage.market_snapshot_bindings)) {
      throw new Error(`${label} artifact lineage mismatch`);
    }
  };
  const driver = registry.get("driver_map");
  if (!isDriverMapPayload(driver.payload)) throw new Error("driver_map payload mismatch");
  assertSharedMarketLineage(
    assertArtifactLineage(driver.payload, ["business_map"], registry, "driver_map"), "driver_map",
  );
  const financial = registry.get("financial_bridge");
  if (!isFinancialBridgePayload(financial.payload)) throw new Error("financial_bridge payload mismatch");
  assertSharedMarketLineage(
    assertArtifactLineage(financial.payload, ["driver_map"], registry, "financial_bridge"), "financial_bridge",
  );
  const scenarios = registry.get("scenario_set");
  if (!isScenarioSetPayload(scenarios.payload)) throw new Error("scenario_set payload mismatch");
  assertSharedMarketLineage(
    assertArtifactLineage(scenarios.payload, ["driver_map"], registry, "scenario_set"), "scenario_set",
  );
  const judgment = registry.get("judgment_context");
  if (!isJudgmentContextPayload(judgment.payload)) throw new Error("judgment_context payload mismatch");
  assertSharedMarketLineage(assertArtifactLineage(judgment.payload, [
    "evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set", "research_gaps",
  ], registry, "judgment_context"), "judgment_context");
  if (judgment.payload.market_security_bridge_available !== (modelLineage.market_snapshot_ids.length > 0)) {
    throw new Error("judgment_context payload mismatch");
  }
  const memo = registry.get("memo");
  if (!isMachineMemoPayload(memo.payload, registry)) {
    throw new Error("memo payload violates not-answerable memo contract");
  }
  assertSharedMarketLineage(
    assertArtifactLineage(memo.payload, ["judgment_context"], registry, "memo"), "memo",
  );
  const marketSources = modelLineage.market_snapshot_bindings.map((binding) => {
    const { fact_key: _factKey, ...source } = binding.source_ref;
    return source;
  });
  const expectedModelSources = canonicalSourceRefs([
    ...evidence.source_refs, ...gaps.source_refs, ...marketSources,
  ]);
  if (modelSourceKinds.some((kind) =>
    !jsonValuesEqual(registry.get(kind).source_refs, expectedModelSources))) {
    throw new Error("model artifact source refs do not match governed inputs");
  }
  const storedBusiness = storedBusinessPayload(business.payload);
  const storedDriver = storedDriverPayload(driver.payload);
  const storedFinancial = storedFinancialPayload(financial.payload, storedDriver);
  const storedScenarios = storedScenarioPayload(scenarios.payload);
  const storedJudgment = structuredClone(judgment.payload);
  const evidenceLineage = new Set(evidence.payload.facts.map((fact) => lineageRefIdentity({
    fact_key: fact.fact_key,
    source_role: fact.source_role,
    source_url: fact.source_url,
    source_locator: fact.source_locator,
    raw_hash: fact.raw_hash,
  })));
  const counterevidence = judgment.payload.strongest_counterevidence;
  const counterIdentities = counterevidence.map(lineageRefIdentity);
  if (new Set(counterIdentities).size !== counterIdentities.length
    || counterIdentities.some((identity) => !evidenceLineage.has(identity))
    || !jsonValuesEqual(memo.payload.strongest_counterevidence, counterevidence)) {
    throw new Error("model counterevidence semantics mismatch");
  }
  const memoGaps = closedMemoGaps({
    gaps, business, judgment, memo, hasMarket: modelLineage.market_snapshot_ids.length > 0,
  });
  for (const [artifact, storedPayload] of [
    [business, storedBusiness],
    [driver, storedDriver],
    [financial, storedFinancial],
    [scenarios, storedScenarios],
    [judgment, storedJudgment],
  ]) assertRootArtifactContent(artifact, storedPayload, artifact.kind);

  const referencedPayloads = new Map([
    ["business_map", storedBusiness],
    ["driver_map", storedDriver],
    ["financial_bridge", storedFinancial],
    ["scenario_set", storedScenarios],
  ]);
  for (const kind of referencedPayloads.keys()) {
    const ref = memo.payload[`${kind}_ref`];
    if (ref.content_hash !== canonicalHash(withoutLineage(referencedPayloads.get(kind)))) {
      throw new Error("model memo reference hash mismatch");
    }
  }
  const storedMemo = { ...memo.payload, research_gaps: memoGaps };
  assertRootArtifactContent(memo, storedMemo, "memo");
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
