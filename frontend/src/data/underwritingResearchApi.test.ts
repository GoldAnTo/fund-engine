import { afterEach, expect, it, vi } from "vitest";
import { resetUnderwritingResearchApi, underwritingResearchApi } from "./underwritingResearchApi";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); resetUnderwritingResearchApi(); });

it("reads archives through the same-origin server proxy without exposing browser credentials", async () => {
  vi.stubEnv("VITE_RESEARCH_BEARER_TOKEN", "must-not-leave-browser-config");
  vi.stubEnv("VITE_UNDERWRITING_API_URL", "https://untrusted.invalid");
  const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify({ schema_version: "underwriting.v1", items: [], next_cursor: null })));
  vi.stubGlobal("fetch", fetchSpy);
  resetUnderwritingResearchApi();
  await underwritingResearchApi.listArchives({ query: "公司 & 行业", limit: 20 });
  const [url, init] = fetchSpy.mock.calls[0]!;
  expect(url).toBe("/api/underwriting/v1/research-archives?query=%E5%85%AC%E5%8F%B8+%26+%E8%A1%8C%E4%B8%9A&limit=20");
  expect(init).toEqual({ method: "GET", credentials: "include" });
});
