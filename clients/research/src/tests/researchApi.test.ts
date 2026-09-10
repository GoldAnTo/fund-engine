import { afterEach, expect, it, vi } from "vitest";
import { researchApi } from "@/data/researchApi";
afterEach(() => vi.unstubAllGlobals());
it.each([401, 403, 404, 422, 503])("turns HTTP %s envelopes into actionable errors without exposing provider bodies", async (status) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "request_failed", message: "private upstream body", request_id: "req-123", details: {} } }), { status })));
  const error = await researchApi.list(new AbortController().signal).catch((value: unknown) => value) as Error;
  expect(error.message).not.toContain("private upstream body");
  expect(error.message).toContain("req-123");
  expect(error.message).toMatch(/登录|权限|重新选择|检查|重试/);
});
it("rejects a malformed successful list before rendering", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
  await expect(researchApi.list(new AbortController().signal)).rejects.toThrow("数据格式");
});
it("rejects a malformed successful workbench before rendering", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"event":{}}')));
  await expect(researchApi.workbench("a", new AbortController().signal)).rejects.toThrow("数据格式");
});
