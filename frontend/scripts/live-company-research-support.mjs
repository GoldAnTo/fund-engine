const USAGE = "usage: verify-live-company-research-ui.mjs [--timeout-seconds 30..300]";
const LOOPBACK_HOST = "127.0.0.1";
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
    workspaceError("state mismatch");
  }
  if (workspace.preparation.progress !== expected.progress) workspaceError("progress mismatch");
  if (!isRecord(workspace.draft) || !isSafePositiveInteger(workspace.draft.lock_version)) {
    workspaceError("draft lock version mismatch");
  }
  return workspace;
}
