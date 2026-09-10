import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clearSubmissionKey, submissionKey } from "@/data/researchSubmission";
beforeEach(() => { sessionStorage.clear(); vi.stubGlobal("crypto", webcrypto); });
afterEach(() => vi.unstubAllGlobals());
it("retains retry identity without storing original materials and rotates changed payloads", async () => {
  const payload = { raw_input: "private original material", event_title: "事件" };
  const first = await submissionKey(payload);
  expect(await submissionKey(payload)).toBe(first);
  expect(sessionStorage.getItem("fundclaw:create-submission")).not.toContain("private original material");
  expect(await submissionKey({ ...payload, event_title: "修改事件" })).not.toBe(first);
});
it("removes successful submission identity", async () => {
  const first = await submissionKey({ title: "a" });
  clearSubmissionKey(first);
  expect(await submissionKey({ title: "a" })).not.toBe(first);
});
