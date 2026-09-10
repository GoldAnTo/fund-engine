import { describe, expect, it, vi, afterEach } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";
import { MockResearchAdapter } from "../data/mockResearchAdapter";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

describe("event research adapters", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("maps an extracted event without inventing optional fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          event_title: "公司宣布新指引，盘后下跌",
          company_name: null,
          ticker: null,
          event_at: null,
          market_reaction: "盘后下跌",
          summary: null,
          research_question: "新指引是否改变了市场预期？",
          candidate_factors: ["因素一", "因素二", "因素三"],
          confirmation_required: true,
        }),
      ),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(
      adapter.extractEventResearch({ rawInput: "公司宣布新指引，盘后下跌。", sourceUrl: "" }),
    ).resolves.toMatchObject({
      companyName: null,
      ticker: null,
      confirmationRequired: true,
    });
  });

  it("returns separate event rows and an actionable lifecycle", async () => {
    const adapter = new MockResearchAdapter();
    const events = await adapter.listEventResearch();

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "event-alphabet", status: "researching" }),
        expect.objectContaining({ id: "event-tsm", status: "awaiting_key_review" }),
      ]),
    );
  });
});
