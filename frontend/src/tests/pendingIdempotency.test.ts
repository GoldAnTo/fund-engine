import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearPendingSubmission, hasPendingSubmissions, preparePendingSubmission, type PendingPrincipal } from "@/gateway/pendingIdempotency";

const principal: PendingPrincipal = { tenantId: "team-a", subjectId: "alice", roles: ["researcher"] };
const scope = { type: "team" as const, conversationId: "conversation-1", runSpecId: "run-1" };

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("pending idempotency storage", () => {
  it("reuses the original key and expected revision for the same scoped digest without storing the body", async () => {
    const first = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "核对现金流", expected_revision: undefined }, expectedRevision: 1, generateKey: () => "key-1" });
    const second = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "核对现金流", expected_revision: undefined }, expectedRevision: 4, generateKey: () => "key-2" });

    expect(second).toMatchObject({ idempotencyKey: "key-1", expectedRevision: 1, restored: true, durable: true });
    expect(first.storageKey).toBe(second.storageKey);
    expect(JSON.stringify(sessionStorage)).not.toContain("核对现金流");
    expect(JSON.stringify(sessionStorage)).not.toContain("finance");
  });

  it("uses a new key for a different body or different principal", async () => {
    const first = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "核对现金流" }, expectedRevision: 1, generateKey: () => "key-1" });
    const changedBody = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "核对费用" }, expectedRevision: 1, generateKey: () => "key-2" });
    const changedPrincipal = await preparePendingSubmission({ principal: { ...principal, subjectId: "bob" }, scope, operation: "team.message", body: { recipient: "finance", text: "核对现金流" }, expectedRevision: 1, generateKey: () => "key-3" });

    expect(changedBody.idempotencyKey).toBe("key-2");
    expect(changedBody.storageKey).not.toBe(first.storageKey);
    expect(changedPrincipal.idempotencyKey).toBe("key-3");
  });

  it("tracks pending entries by principal and scope without replaying them", async () => {
    await preparePendingSubmission({ principal, scope, operation: "team.review", body: { decision: "approved", comment: "已核对", output_ids: ["o1"] }, expectedRevision: 7, generateKey: () => "review-key" });

    expect(await hasPendingSubmissions({ principal, scope, operations: ["team.review"] })).toMatchObject({ pending: true, durable: true });
    expect(await hasPendingSubmissions({ principal, scope: { ...scope, runSpecId: "run-2" }, operations: ["team.review"] })).toMatchObject({ pending: false });
  });

  it("clears a confirmed or rejected pending key", async () => {
    const pending = await preparePendingSubmission({ principal, scope, operation: "team.command", body: { kind: "pause" }, expectedRevision: 3, generateKey: () => "command-key" });
    await clearPendingSubmission(pending.storageKey);

    const next = await preparePendingSubmission({ principal, scope, operation: "team.command", body: { kind: "pause" }, expectedRevision: 5, generateKey: () => "next-key" });
    expect(next).toMatchObject({ idempotencyKey: "next-key", expectedRevision: 5, restored: false });
  });

  it("degrades honestly when sessionStorage cannot be written", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });

    const pending = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "team", text: "不落盘" }, expectedRevision: 1, generateKey: () => "memory-key" });

    expect(pending).toMatchObject({ idempotencyKey: "memory-key", durable: false, restored: false });
    expect(pending.notice).toContain("浏览器无法保存待确认提交标识");
    await clearPendingSubmission(pending.storageKey);
  });

  it("falls back to the memory entry when storage exists but the write failed", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });

    const first = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "team", text: "不落盘" }, expectedRevision: 1, generateKey: () => "memory-key" });
    const restored = await preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "team", text: "不落盘" }, expectedRevision: 7, generateKey: () => "different-key" });

    expect(restored).toMatchObject({ idempotencyKey: "memory-key", expectedRevision: 1, durable: false, restored: true });
    await clearPendingSubmission(first.storageKey);
  });

  it("coalesces parallel preparations for the same intent", async () => {
    let generated = 0;

    const first = preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "同一意图" }, expectedRevision: 1, generateKey: () => `key-${++generated}` });
    const second = preparePendingSubmission({ principal, scope, operation: "team.message", body: { recipient: "finance", text: "同一意图" }, expectedRevision: 2, generateKey: () => `key-${++generated}` });
    const [firstPrepared, secondPrepared] = await Promise.all([first, second]);

    expect(secondPrepared.idempotencyKey).toBe(firstPrepared.idempotencyKey);
    expect(secondPrepared.expectedRevision).toBe(firstPrepared.expectedRevision);
    expect(generated).toBe(1);
  });

  it("uses a full SHA-256 digest when WebCrypto subtle is unavailable", async () => {
    const crypto = globalThis.crypto;
    const descriptor = Object.getOwnPropertyDescriptor(crypto, "subtle");
    Object.defineProperty(crypto, "subtle", { configurable: true, value: undefined });
    try {
      const pending = await preparePendingSubmission({ principal, scope, operation: "team.command", body: { kind: "pause" }, expectedRevision: 1, generateKey: () => "sha-key" });

      expect(pending).toMatchObject({ durable: true, idempotencyKey: "sha-key" });
      expect(pending.storageKey).not.toContain("fallback-");
      expect(pending.storageKey).toMatch(/[0-9a-f]{64}/);
    } finally {
      if (descriptor) Object.defineProperty(crypto, "subtle", descriptor);
    }
  });

  it("reports storage as non-durable when the write probe is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });

    const check = await hasPendingSubmissions({ principal: { ...principal, subjectId: "storage-check" }, scope, operations: ["team.message"] });

    expect(check).toMatchObject({ pending: false, durable: false });
    expect(check.notice).toContain("浏览器无法保存待确认提交标识");
  });
});
