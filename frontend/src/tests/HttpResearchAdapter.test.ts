import { describe, it, expect, vi, afterEach } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";
import { PageStateError } from "../domain/types";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("HttpResearchAdapter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps a dossier DTO without leaking wire-only basis fields", async () => {
    const dossierDto = {
      schema_version: "v1",
      basis: {
        cutoff: "2024-05-24T00:00:00+08:00",
        is_historical: false,
      },
      case: {
        id: "case-1",
        title: "Test Case",
        topic: "Test Topic",
        created_by: "tester",
        created_at: "2024-01-01T00:00:00+08:00",
        updated_at: "2024-01-02T00:00:00+08:00",
      },
      theses: [],
      focus_thesis_id: "t-1",
      assessment: null,
      causal_chain: [],
      evidence: {},
      competitive_explanations: [],
      gaps: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(dossierDto))
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const dossier = await adapter.getCaseDossier("case-1");

    expect(dossier.case.id).toBe("case-1");
    expect(dossier.focus_thesis_id).toBe("t-1");
    expect(
      (dossier as unknown as Record<string, unknown>).basis
    ).toBeUndefined();
  });

  it("maps the stable error envelope to PageStateError", async () => {
    const errorEnvelope = {
      error: {
        code: "permission_denied",
        message: "forbidden",
        request_id: "req-1",
        details: {},
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(errorEnvelope, false, 403))
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await expect(adapter.getCaseSummaries()).rejects.toBeInstanceOf(
      PageStateError
    );
    await expect(adapter.getCaseSummaries()).rejects.toMatchObject({
      kind: "permission_denied",
    });
  });

  it("getReviewQueue throws backend_unavailable", async () => {
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await expect(adapter.getReviewQueue()).rejects.toBeInstanceOf(
      PageStateError
    );
    await expect(adapter.getReviewQueue()).rejects.toMatchObject({
      kind: "backend_unavailable",
    });
  });

  it("getOverview resolves the focus case_id from the case list", async () => {
    const caseList = {
      schema_version: "v1",
      items: [
        {
          id: "case-9",
          title: "Focus Case",
          topic: "t",
          created_by: "u",
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      page: { has_more: false },
    };
    const overviewDto = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      case: caseList.items[0],
      thesis: null,
      assessment: null,
      key_changes: [],
      framework: [],
      totals: { evidence_total: 0, pending_review: 0, major_gaps: 0 },
      task_queue: [],
      evidence_changes: [],
      activity: [],
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/research-cases")) return jsonResponse(caseList);
      if (url.includes("/overview")) return jsonResponse(overviewDto);
      return jsonResponse({}, false, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const overview = await adapter.getOverview();

    expect(overview.case_id).toBe("case-9");
    // the overview request must carry the resolved case_id
    const overviewCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/overview")
    );
    expect(String(overviewCall?.[0])).toContain("case_id=case-9");
  });
});
