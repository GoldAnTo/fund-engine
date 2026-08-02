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
    expect(dossier.assessment).toBeNull();
    expect(
      (dossier as unknown as Record<string, unknown>).basis
    ).toBeUndefined();
  });

  it("preserves assessment.review and propagates review_state, framework, citations", async () => {
    const dossierDto = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      case: {
        id: "case-1",
        title: "Test",
        topic: "t",
        created_by: "u",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
      theses: [],
      focus_thesis_id: "t-1",
      assessment: {
        id: "a-1",
        thesis_id: "t-1",
        conclusion: "supported",
        rationale: "r",
        gaps: [],
        provisional: true,
        review: {
          outcome: "modified",
          conclusion: "supported",
          reason: "ok",
          reviewer: "alice",
          reviewed_at: "2024-05-01T00:00:00Z",
        },
      },
      causal_chain: [
        { id: "s-1", sequence: 1, description: "step one" },
      ],
      evidence: {
        supports: [
          {
            link_id: "l-1",
            statement_id: "st-1",
            statement_text: "real statement text",
            statement_kind: "disclosed_fact",
            span_id: "sp-1",
            verbatim_text: "v",
            locator: {},
            role: "supports",
            reason: "r",
            scope: {},
            observed_period: null,
            available_at: "2024-01-01T00:00:00Z",
            review_state: "reviewed",
          },
        ],
        contradicts: [],
        contextualizes: [],
      },
      competitive_explanations: [],
      gaps: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(dossierDto)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const dossier = await adapter.getCaseDossier("case-1");
    expect(dossier.assessment?.review).not.toBeNull();
    expect(dossier.assessment?.review?.reviewer).toBe("alice");
    expect(dossier.causal_chain[0].description).toBe("step one");
    expect(dossier.causal_chain[0].status).toBeNull();
    expect(dossier.evidence.supports[0].statement_text).toBe("real statement text");
    expect(dossier.evidence.supports[0].reliability).toBeNull();
    expect(dossier.evidence.supports[0].source_label).toBeNull();
  });

  it("document citations are populated from the backend", async () => {
    const detail = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      document: {
        id: "doc-1",
        content_sha256: "x".repeat(64),
        source_url: "u",
        published_at: null,
        available_at: "2024-01-01T00:00:00Z",
        acquired_at: "2024-01-01T00:00:00Z",
        parser_version: "1",
        supersedes_id: null,
        span_count: 1,
        statement_count: 1,
        parse_state: "parsed",
      },
      spans: [
        {
          id: "sp-1",
          document_version_id: "doc-1",
          locator: { p: 1 },
          verbatim_text: "v",
          citations: [
            { link_id: "l-1", thesis_id: "t-1", role: "supports" },
          ],
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(detail)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const out = await adapter.getDocumentDetail("doc-1");
    expect(out.spans[0].cited_by).toEqual([
      { evidence_id: "l-1", thesis_id: "t-1", role: "supports" },
    ]);
  });

  it("search returns empty for queries shorter than the backend minimum", async () => {
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await adapter.search("a")).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("search rewrites backend deep links to the React routes", async () => {
    const search = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      groups: [
        {
          object_type: "case",
          hits: [
            {
              object_type: "case",
              object_id: "c-1",
              title: "t",
              snippet: "s",
              case_id: "c-1",
              review_state: null,
              available_at: null,
              deep_link: "/research-cases/c-1/dossier",
            },
          ],
        },
      ],
      page: { has_more: false },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(search)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const hits = await adapter.search("ab");
    expect(hits[0].navigate_to).toBe("/cases/c-1");
  });

  it("throws on unknown graph semantic_kind instead of silently rewriting", async () => {
    const graph = {
      schema_version: "graph/v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      nodes: [{ id: "n1", kind: "case", label: "c", properties: {} }],
      edges: [
        {
          id: "e1",
          semantic_kind: "made_up_kind",
          source: "n1",
          target: "n1",
          review_state: null,
          available_at: null,
          valid_interval: null,
          source_refs: [],
          properties: {},
        },
      ],
      paths: [],
      page: { has_more: false },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(graph)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await expect(adapter.getRelationshipGraph("c-1")).rejects.toMatchObject({
      kind: "backend_unavailable",
    });
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

  it("overview key_changes propagate review_state (AI vs human)", async () => {
    const caseList = {
      schema_version: "v1",
      items: [
        {
          id: "case-1",
          title: "c",
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
      key_changes: [
        {
          id: "k1",
          tag: "新增",
          text: "AI proposal",
          occurred_at: "2024-05-01T00:00:00Z",
          source_label: "ai",
          review_state: "machine_generated",
        },
        {
          id: "k2",
          tag: "新增",
          text: "Human reviewed",
          occurred_at: "2024-05-02T00:00:00Z",
          source_label: "human",
          review_state: "reviewed",
        },
      ],
      framework: [],
      totals: { evidence_total: 2, pending_review: 1, major_gaps: 0 },
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
    expect(overview.key_changes[0].review_state).toBe("machine_generated");
    expect(overview.key_changes[1].review_state).toBe("reviewed");
  });

  it("search maps case deep links to the React /cases/ route (not graph)", async () => {
    const search = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      groups: [
        {
          object_type: "case",
          hits: [
            {
              object_type: "case",
              object_id: "c-1",
              title: "t",
              snippet: "s",
              case_id: "c-1",
              review_state: null,
              available_at: null,
              deep_link: "/research-cases/c-1",
            },
          ],
        },
      ],
      page: { has_more: false },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(search)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const hits = await adapter.search("ab");
    expect(hits[0].navigate_to).toBe("/cases/c-1");
  });

  it("search maps graph deep links to the React /relationships/ route", async () => {
    const search = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      groups: [
        {
          object_type: "case",
          hits: [
            {
              object_type: "case",
              object_id: "c-1",
              title: "t",
              snippet: "s",
              case_id: "c-1",
              review_state: null,
              available_at: null,
              deep_link: "/research-cases/c-1/graph",
            },
          ],
        },
      ],
      page: { has_more: false },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(search)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const hits = await adapter.search("ab");
    expect(hits[0].navigate_to).toBe("/relationships/c-1");
  });

  it("rejects unknown review outcome (does not silently coerce to human_confirmed)", async () => {
    const dossierDto = {
      schema_version: "v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      case: {
        id: "case-1",
        title: "t",
        topic: "t",
        created_by: "u",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
      theses: [],
      focus_thesis_id: "t-1",
      assessment: {
        id: "a-1",
        thesis_id: "t-1",
        conclusion: "supported",
        rationale: "r",
        gaps: [],
        provisional: true,
        review: {
          outcome: "approved",
          conclusion: null,
          reason: "r",
          reviewer: "u",
          reviewed_at: "2024-05-01T00:00:00Z",
        },
      },
      causal_chain: [],
      evidence: {},
      competitive_explanations: [],
      gaps: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(dossierDto)),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await expect(adapter.getCaseDossier("case-1")).rejects.toMatchObject({
      kind: "backend_unavailable",
    });
  });

  it("maps research-ops KPIs into the data-center view", async () => {
    const kpisDto = {
      as_of: "2026-08-02T04:00:00+00:00",
      case_id: null,
      throughput: {
        link_reviews_total: 4,
        link_reviews_last_7d: 2,
        assessment_reviews_total: 3,
        assessment_reviews_last_7d: 3,
        reviews_by_reviewer: { "analyst-a": 3, "analyst-b": 1 },
        pending_link_reviews: 12,
        pending_assessment_reviews: 0,
      },
      agreement: {
        assessment_outcomes: { confirmed: 2, modified: 1 },
        assessment_agreement_rate: 0.6667,
        conclusion_changed: 1,
        link_outcomes: { confirmed: 3, rejected: 1 },
        link_agreement_rate: 0.75,
        link_modified: 1,
      },
      latency: {
        evidence_to_assessment_avg_days: 1.5,
        evidence_to_assessment_max_days: 4.0,
        assessment_to_review_avg_days: 2.25,
        assessment_to_review_max_days: 6.0,
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/research-ops/kpis")) return jsonResponse(kpisDto);
        if (url.includes("/metrics/catalog"))
          return jsonResponse({ entries: [] });
        if (url.includes("/provider-runs")) return jsonResponse({ runs: [] });
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const view = await adapter.getDataCenterView();

    expect(view.researchOps.asOf).toBe("2026-08-02T04:00:00+00:00");
    expect(view.researchOps.throughput.pendingLinkReviews).toBe(12);
    expect(view.researchOps.throughput.reviewsByReviewer).toEqual([
      { reviewer: "analyst-a", count: 3 },
      { reviewer: "analyst-b", count: 1 },
    ]);
    expect(view.researchOps.agreement.assessmentAgreementRate).toBe(0.6667);
    expect(view.researchOps.agreement.linkAgreementRate).toBe(0.75);
    expect(view.researchOps.agreement.linkModified).toBe(1);
    expect(view.researchOps.latency.assessmentToReviewAvgDays).toBe(2.25);
  });
});
