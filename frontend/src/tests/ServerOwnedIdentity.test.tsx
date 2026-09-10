import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpResearchAdapter } from "../data/httpResearchAdapter";

const FORBIDDEN = [
  "actor",
  "reviewer",
  "reviewer_id",
  "created_by",
  "changed_by",
  "tenant_id",
  "admitted_by",
  "approved_by",
  "triggered_by",
  "requested_by",
  "recorded_by",
  "reviewed_by",
  "creator_type",
  "proposed_by",
];

describe("server-owned command identity", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not serialize acting identity when creating a Case", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      case_id: "case-1",
      theses: [],
    }), { status: 201, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" }).createCase({
      title: "CoWoS 指引上调",
      industryTopic: "半导体",
      theses: [{ statement: "先进封装需求继续增长" }],
    });

    const init = fetch.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    for (const field of FORBIDDEN) expect(body).not.toHaveProperty(field);
    const thesis = (body.initial_theses as Record<string, unknown>[])[0];
    for (const field of FORBIDDEN) expect(thesis).not.toHaveProperty(field);
    expect(new Headers(init.headers).has("X-Actor")).toBe(false);
  });
});
