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

  it("maps reviewed workflow mode and keeps the reviewed fallback for older responses", async () => {
    const response = (workflowMode?: "reviewed") => ({
      items: [{
        case_id: "event-1",
        ...(workflowMode ? { workflow_mode: workflowMode } : {}),
        event_title: "事件研究",
        company_name: null,
        ticker: null,
        event_at: null,
        lifecycle_status: "researching",
        status_summary: "研究中",
        next_human_action: null,
        next_action_kind: "wait",
        updated_at: "2026-08-17T00:00:00Z",
      }],
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(response("reviewed")))
      .mockResolvedValueOnce(jsonResponse(response()));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(adapter.listEventResearch()).resolves.toMatchObject([
      { workflowMode: "reviewed" },
    ]);
    await expect(adapter.listEventResearch()).resolves.toMatchObject([
      { workflowMode: "reviewed" },
    ]);
  });
});
