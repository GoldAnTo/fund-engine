import type { GatewaySession } from "./contracts";

const prefix = "fundclaw.pending-idempotency.v1";
const unavailableNotice = "浏览器无法保存待确认提交标识；刷新或关闭页面后无法保证沿用同一提交标识。请先核对已确认请求历史，避免重复提交。";

export type PendingPrincipal = Pick<GatewaySession, "tenantId" | "subjectId" | "roles">;
export type PendingScope = { type: "start" } | { type: "team"; conversationId: string; runSpecId: string } | {type:"company";studyId:string};
export type PendingOperation = "start" | "team.message" | "team.command" | "team.review" | "company.create" | "company.activity" | "company.link" | "company.revision" | "company.monitor" | "company.retry";

type Entry = {
  version: 1;
  idempotencyKey: string;
  operation: PendingOperation;
  principalDigest: string;
  scopeDigest: string;
  intentDigest: string;
  expectedRevision: number | null;
  createdAt: string;
};

export type PreparedPendingSubmission = {
  idempotencyKey: string;
  expectedRevision: number | null;
  restored: boolean;
  durable: boolean;
  storageKey: string;
  notice: string;
};

export type PreparePendingSubmissionInput = {
  principal: PendingPrincipal;
  scope: PendingScope;
  operation: PendingOperation;
  body: unknown;
  expectedRevision?: number | null;
  generateKey?: () => string;
  signal?: AbortSignal;
};

export type PendingCheck = {
  pending: boolean;
  durable: boolean;
  notice: string;
};

const memory = new Map<string, Entry>();
const preparationLocks = new Map<string, Promise<void>>();

export async function preparePendingSubmission(input: PreparePendingSubmissionInput): Promise<PreparedPendingSubmission> {
  const digests = await pendingDigests(input, input.signal);
  const key = storageKey(input.operation, digests);
  const activePreparation = preparationLocks.get(key);
  if (activePreparation) {
    await activePreparation;
  }
  throwIfAborted(input.signal);
  const storageState = getStorageState();
  const existing = readEntry(key, storageState.storage);
  if (existing) {
    const durable = storageState.writable && memory.get(key) !== existing;
    return { idempotencyKey: existing.idempotencyKey, expectedRevision: existing.expectedRevision, restored: true, durable, storageKey: key, notice: pendingEntryNotice(input.operation, durable) };
  }

  let releasePreparation!: () => void;
  const preparation = new Promise<void>((resolve) => { releasePreparation = resolve; });
  preparationLocks.set(key, preparation);
  try {
    const racedExisting = readEntry(key, storageState.storage);
    if (racedExisting) {
      const durable = storageState.writable && memory.get(key) !== racedExisting;
      return { idempotencyKey: racedExisting.idempotencyKey, expectedRevision: racedExisting.expectedRevision, restored: true, durable, storageKey: key, notice: pendingEntryNotice(input.operation, durable) };
    }
    throwIfAborted(input.signal);
    const entry: Entry = {
      version: 1,
      idempotencyKey: input.generateKey?.() ?? newIdempotencyKey(),
      operation: input.operation,
      principalDigest: digests.principalDigest,
      scopeDigest: digests.scopeDigest,
      intentDigest: digests.intentDigest,
      expectedRevision: input.expectedRevision ?? null,
      createdAt: new Date().toISOString(),
    };
    if (!storageState.storage || !storageState.writable) {
      memory.set(key, entry);
      return { idempotencyKey: entry.idempotencyKey, expectedRevision: entry.expectedRevision, restored: false, durable: false, storageKey: key, notice: unavailableNotice };
    }
    try {
      storageState.storage.setItem(key, JSON.stringify(entry));
      memory.delete(key);
      return { idempotencyKey: entry.idempotencyKey, expectedRevision: entry.expectedRevision, restored: false, durable: true, storageKey: key, notice: "" };
    } catch {
      memory.set(key, entry);
      return { idempotencyKey: entry.idempotencyKey, expectedRevision: entry.expectedRevision, restored: false, durable: false, storageKey: key, notice: unavailableNotice };
    }
  } finally {
    releasePreparation();
    if (preparationLocks.get(key) === preparation) {
      preparationLocks.delete(key);
    }
  }
}

export async function hasPendingSubmissions({ principal, scope, operations }: { principal: PendingPrincipal; scope: PendingScope; operations: PendingOperation[] }): Promise<PendingCheck> {
  const principalDigest = await digest(canonicalize(normalizedPrincipal(principal)));
  const scopeDigest = await digest(canonicalize(scope));
  const storageState = getStorageState();
  const entries = [...memory.values(), ...entriesFromStorage(storageState.storage)];
  const pending = entries.some((entry) => entry.principalDigest === principalDigest && entry.scopeDigest === scopeDigest && operations.includes(entry.operation));
  return { pending, durable: storageState.writable, notice: pendingCheckNotice(operations, pending, storageState.writable) };
}

export async function pendingStorageKey(input: Pick<PreparePendingSubmissionInput, "principal" | "scope" | "operation" | "body">): Promise<string> {
  return storageKey(input.operation, await pendingDigests(input));
}

export async function clearPendingSubmission(storageKey: string): Promise<void> {
  memory.delete(storageKey);
  const storage = getStorage();
  if (!storage) return;
  try { storage.removeItem(storageKey); } catch { /* best effort */ }
}

export function pendingNotice(operations: readonly PendingOperation[]): string {
  const target = operations.includes("start") ? "新建研究" : "专业团队提交";
  return `检测到${target}存在待确认提交标识。刷新不会自动重发；请先核对已确认请求历史，若仍需重试，请重新输入相同内容以沿用原提交标识。`;
}

function restoredNotice(operation: PendingOperation): string {
  if(operation.startsWith('company.')) return '已沿用待确认的公司研究提交标识；请核对已保存的活动和配置，避免重复提交。';
  return operation === "start"
    ? "已沿用待确认的新建研究提交标识；请核对研究列表与已确认请求历史，避免重复创建。"
    : "已沿用待确认的专业团队提交标识和原始版本；请核对已确认请求历史，避免重复任务或复核。";
}

function pendingEntryNotice(operation: PendingOperation, durable: boolean): string {
  return durable ? restoredNotice(operation) : `${restoredNotice(operation)} ${unavailableNotice}`;
}

function pendingCheckNotice(operations: readonly PendingOperation[], pending: boolean, durable: boolean): string {
  if (pending && !durable) return `${pendingNotice(operations)} ${unavailableNotice}`;
  if (pending) return pendingNotice(operations);
  return durable ? "" : unavailableNotice;
}

async function pendingDigests(input: Pick<PreparePendingSubmissionInput, "principal" | "scope" | "body">, signal?: AbortSignal): Promise<{ principalDigest: string; scopeDigest: string; intentDigest: string }> {
  const [principalDigest, scopeDigest, intentDigest] = await Promise.all([
    digest(canonicalize(normalizedPrincipal(input.principal))),
    digest(canonicalize(input.scope)),
    digest(canonicalize(input.body)),
  ]);
  throwIfAborted(signal);
  return { principalDigest, scopeDigest, intentDigest };
}

function storageKey(operation: PendingOperation, digests: { principalDigest: string; scopeDigest: string; intentDigest: string }): string {
  return `${prefix}.${operation}.${digests.principalDigest}.${digests.scopeDigest}.${digests.intentDigest}`;
}

function readEntry(key: string, storage: Storage | null): Entry | null {
  const fallback = memory.get(key) ?? null;
  if (!storage) return fallback;
  try {
    const raw = storage.getItem(key);
    return raw ? parseEntry(raw) : fallback;
  } catch {
    return fallback;
  }
}

function entriesFromStorage(storage: Storage | null): Entry[] {
  if (!storage) return [];
  const entries: Entry[] = [];
  try {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const raw = storage.getItem(key);
      const entry = raw ? parseEntry(raw) : null;
      if (entry) entries.push(entry);
    }
  } catch {
    return [];
  }
  return entries;
}

function parseEntry(raw: string): Entry | null {
  try {
    const value = JSON.parse(raw) as Partial<Entry>;
    if (value.version !== 1 || !value.idempotencyKey || !value.operation || !value.principalDigest || !value.scopeDigest || !value.intentDigest) return null;
    if (!["start", "team.message", "team.command", "team.review", "company.create", "company.activity", "company.link", "company.revision", "company.monitor", "company.retry"].includes(value.operation)) return null;
    return { version: 1, idempotencyKey: value.idempotencyKey, operation: value.operation, principalDigest: value.principalDigest, scopeDigest: value.scopeDigest, intentDigest: value.intentDigest, expectedRevision: typeof value.expectedRevision === "number" ? value.expectedRevision : null, createdAt: value.createdAt || "" };
  } catch {
    return null;
  }
}

function normalizedPrincipal(principal: PendingPrincipal): PendingPrincipal {
  return { tenantId: principal.tenantId, subjectId: principal.subjectId, roles: [...principal.roles].sort() };
}

function canonicalize(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, entry]) => [key, sortValue(entry)]));
  }
  return value;
}

async function digest(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    const result = await subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(result)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return sha256Hex(bytes);
}

function getStorageState(): { storage: Storage | null; writable: boolean } {
  const storage = getStorage();
  if (!storage) return { storage: null, writable: false };
  const probe = `${prefix}.probe`;
  try {
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return { storage, writable: true };
  } catch {
    return { storage, writable: false };
  }
}

function getStorage(): Storage | null {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `idempotency-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (!signal?.aborted) return;
  throw signal.reason ?? new DOMException("The operation was aborted.", "AbortError");
}

function sha256Hex(bytes: Uint8Array): string {
  const words = new Uint32Array(64);
  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const paddedLength = (((bytes.length + 9 + 63) >> 6) << 6);
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const bitLength = bytes.length * 8;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 4, bitLength >>> 0);
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = view.getUint32(offset + index * 4);
    }
    for (let index = 16; index < 64; index += 1) {
      const word15 = words[index - 15]!;
      const word2 = words[index - 2]!;
      const s0 = rotateRight(word15, 7) ^ rotateRight(word15, 18) ^ (word15 >>> 3);
      const s1 = rotateRight(word2, 17) ^ rotateRight(word2, 19) ^ (word2 >>> 10);
      words[index] = (words[index - 16]! + s0 + words[index - 7]! + s1) >>> 0;
    }
    let a = state[0]!, b = state[1]!, c = state[2]!, d = state[3]!;
    let e = state[4]!, f = state[5]!, g = state[6]!, h = state[7]!;
    for (let index = 0; index < 64; index += 1) {
      const s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + constants[index]! + words[index]!) >>> 0;
      const s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    state[0] = (state[0]! + a) >>> 0; state[1] = (state[1]! + b) >>> 0;
    state[2] = (state[2]! + c) >>> 0; state[3] = (state[3]! + d) >>> 0;
    state[4] = (state[4]! + e) >>> 0; state[5] = (state[5]! + f) >>> 0;
    state[6] = (state[6]! + g) >>> 0; state[7] = (state[7]! + h) >>> 0;
  }
  return [...state].map((word) => word.toString(16).padStart(8, "0")).join("");
}

function rotateRight(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}
