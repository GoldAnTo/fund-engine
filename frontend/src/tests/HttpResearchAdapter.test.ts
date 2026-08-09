import { describe, it, expect, vi, afterEach } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { PageStateError } from "../domain/types";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("HttpResearchAdapter", () => {
  it("creates new event Cases with the strict research protocol by default", async () => {
    let requestBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestBody = JSON.parse(String(init?.body));
      return jsonResponse({ case_id: "event-1", brief_id: "brief-1", lifecycle: { status: "awaiting_key_review", active_run_id: null, current_round: 0, status_summary: "等待核验", current_gap: null, next_human_action: "核验原文资料并完成研究协议" } });
    }));

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    await adapter.createEventResearch({ rawInput: "公司更新资本开支指引。", eventTitle: "资本开支更新", companyName: null, ticker: null, eventAt: null, marketReaction: null, summary: null, researchQuestion: "影响是什么？", candidateFactors: ["因素一", "因素二", "因素三"], confirmationRequired: true, createdBy: "human:researcher" });

    expect((requestBody as Record<string, unknown> | null)?.research_protocol_required).toBe(true);
  });

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
    expect(hits[0].navigate_to).toBe("/events/c-1");
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

  it("maps document/span nodes and contains/derived edges (P2 缺陷 9)", async () => {
    // P2 缺陷 9 修复：原文层 (DocumentVersion / SourceSpan) 必须出现在图
    // 谱读模型，附 contains（document→span）和 derived（span→statement）
    // 边，前端按白名单契约接受。
    const graph = {
      schema_version: "graph/v1",
      basis: { cutoff: "2024-05-24T00:00:00Z", is_historical: false },
      nodes: [
        { id: "case-1", kind: "case", label: "case", properties: {} },
        { id: "th-1", kind: "thesis", label: "th", properties: {} },
        { id: "doc-1", kind: "document", label: "doc", properties: {} },
        { id: "span-1", kind: "span", label: "span", properties: {} },
        {
          id: "stmt-1",
          kind: "statement",
          label: "stmt",
          properties: { document_id: "doc-1", span_id: "span-1" },
        },
      ],
      edges: [
        {
          id: "ct",
          semantic_kind: "contains_thesis",
          source: "case-1",
          target: "th-1",
          review_state: null,
          available_at: null,
          valid_interval: null,
          source_refs: [],
          properties: {},
        },
        {
          id: "c1",
          semantic_kind: "contains",
          source: "doc-1",
          target: "span-1",
          review_state: null,
          available_at: null,
          valid_interval: null,
          source_refs: [],
          properties: {},
        },
        {
          id: "d1",
          semantic_kind: "derived",
          source: "span-1",
          target: "stmt-1",
          review_state: null,
          available_at: null,
          valid_interval: null,
          source_refs: [],
          properties: {},
        },
        {
          id: "ev1",
          semantic_kind: "evidence",
          source: "th-1",
          target: "stmt-1",
          review_state: "reviewed",
          available_at: null,
          valid_interval: null,
          source_refs: [],
          properties: { role: "supports" },
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
    const view = await adapter.getRelationshipGraph("c-1");
    // document / span 节点进入图谱节点集合，白名单不抛错
    const nodeKinds = new Set(view.nodes.map((n) => n.kind));
    expect(nodeKinds.has("document")).toBe(true);
    expect(nodeKinds.has("span")).toBe(true);
    expect(nodeKinds.has("statement")).toBe(true);
    // 原文层边类型进入图谱白名单
    const edgeKinds = new Set(view.edges.map((e) => e.kind));
    expect(edgeKinds.has("contains")).toBe(true);
    expect(edgeKinds.has("derived")).toBe(true);
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

  it("search maps case deep links to the active React /events/ route", async () => {
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
    expect(hits[0].navigate_to).toBe("/events/c-1");
  });

  it("search maps graph deep links to the active Case Wiki route", async () => {
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
    expect(hits[0].navigate_to).toBe("/events/c-1/wiki");
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

  it("maps listCompanies DTO to CompanyListView and strips wire-only basis", async () => {
    const listDto = {
      schema_version: "v1",
      basis: { cutoff: "2026-08-02T00:00:00+00:00", is_historical: false },
      page: { has_more: false, next_cursor: null },
      items: [
        {
          id: "co-a",
          code: "688256",
          name: "寒武纪",
          type: "listed",
          stock_count: 1,
          theme_role_count: 2,
          latest_report_period: "2026-03-31",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/companies")) return jsonResponse(listDto);
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const view = await adapter.listCompanies("寒武纪");

    expect(view.items).toHaveLength(1);
    expect(view.items[0]).toEqual({
      id: "co-a",
      code: "688256",
      name: "寒武纪",
      type: "listed",
      stockCount: 1,
      themeRoleCount: 2,
      latestReportPeriod: "2026-03-31",
    });
    expect(view.hasMore).toBe(false);
    expect(view.nextCursor).toBeNull();
    // 传输层 basis 不应泄漏到领域类型
    expect(
      (view as unknown as Record<string, unknown>).basis
    ).toBeUndefined();
  });

  it("maps getCompanyDossier DTO without leaking wire-only fields", async () => {
    const dossierDto = {
      schema_version: "v1",
      basis: { cutoff: "2026-08-02T00:00:00+00:00", is_historical: false },
      company: {
        id: "co-a",
        code: "688256",
        name: "寒武纪",
        type: "listed",
        created_at: "2026-05-24T02:30:00+00:00",
      },
      stocks: [{ id: "st-a", code: "688256.SH", name: "寒武纪-U", market: "SSE" }],
      theme_roles: [
        {
          id: "tr-1",
          case_id: "RC-AIC-2025-01",
          case_title: "AI 算力链",
          role: "算力芯片受益方",
          scope: { chain: "AI 算力" },
          applicable_from: "2026-01-01",
          applicable_to: null,
          statement_id: "stmt-1",
          statement_text: "阿里云 2025 采购 5-6 万张思元",
          span_id: "span-1",
          document_version_id: "dv-1",
        },
      ],
      related_theses: [
        {
          thesis_id: "th-1",
          case_id: "RC-AIC-2025-01",
          case_title: "AI 算力链",
          statement: "CapEx 增长",
          title: "CapEx 上行",
          ai_assessment: {
            conclusion: "supported",
            provisional: true,
            assessed_at: "2026-07-28T09:12:00+00:00",
          },
          review: null,
        },
      ],
      valuations: [
        {
          stock_id: "st-a",
          stock_code: "688256.SH",
          metric_name: "PE_TTM",
          metric_value: 255.76,
          as_of_date: "2026-07-31",
          source: "gildata",
          definition: "PE(TTM) · 占总市值",
        },
      ],
      fund_holders: [
        {
          fund_id: "f-1",
          fund_code: "588200",
          fund_name: "科创50ETF",
          stock_id: "st-a",
          stock_code: "688256.SH",
          weight: 1.27,
          report_period: "2026-03-31",
          published_at: "2026-04-22T00:00:00+00:00",
          acquired_at: "2026-04-23T00:00:00+00:00",
          source: "占流通A股",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/companies/co-a")) return jsonResponse(dossierDto);
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const view = await adapter.getCompanyDossier("co-a");

    expect(view.company.name).toBe("寒武纪");
    expect(view.stocks[0].code).toBe("688256.SH");
    expect(view.themeRoles[0]).toMatchObject({
      caseId: "RC-AIC-2025-01",
      role: "算力芯片受益方",
      statementId: "stmt-1",
    });
    // AI 草案与人工复核分离承载
    expect(view.relatedTheses[0].aiConclusion).toBe("supported");
    expect(view.relatedTheses[0].aiProvisional).toBe(true);
    expect(view.relatedTheses[0].reviewOutcome).toBeNull();
    expect(view.valuations[0].metricValue).toBe(255.76);
    expect(view.fundHolders[0].source).toBe("占流通A股");
    expect(
      (view as unknown as Record<string, unknown>).basis
    ).toBeUndefined();
  });

  it("maps listThemes DTO with page-shaped envelopes stripped", async () => {
    const listDto = {
      schema_version: "v1",
      basis: { cutoff: "2026-08-02T00:00:00+00:00", is_historical: false },
      items: [
        { tag: "算力国产化", case_count: 2, company_count: 2, thesis_count: 3 },
        { tag: "云厂商CapEx", case_count: 1, company_count: 1, thesis_count: 1 },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/themes")) return jsonResponse(listDto);
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const view = await adapter.listThemes();

    expect(view).toEqual([
      { tag: "算力国产化", caseCount: 2, companyCount: 2, thesisCount: 3 },
      { tag: "云厂商CapEx", caseCount: 1, companyCount: 1, thesisCount: 1 },
    ]);
  });

  it("maps getThemeView DTO and preserves derivedFrom references", async () => {
    const viewDto = {
      schema_version: "v1",
      basis: { cutoff: "2026-08-02T00:00:00+00:00", is_historical: false },
      tag: "算力国产化",
      cases: [
        {
          case_id: "RC-AIC-2025-01",
          case_title: "AI 算力链",
          thesis_counts: { supported: 1, contradicted: 0, ai_pending: 1 },
          theses: [
            {
              thesis_id: "th-1",
              statement: "CapEx 上行",
              title: "CapEx 上行",
              ai_assessment: {
                conclusion: "supported",
                provisional: true,
                assessed_at: "2026-07-28T09:12:00+00:00",
              },
              review: null,
            },
          ],
        },
      ],
      company_roles: [
        {
          company_id: "co-a",
          company_code: "688256",
          company_name: "寒武纪",
          case_id: "RC-AIC-2025-01",
          case_title: "AI 算力链",
          role: "算力芯片受益方",
          scope: {},
          applicable_from: "2026-01-01",
          applicable_to: null,
          statement_id: "stmt-1",
        },
      ],
      fund_exposure: [
        {
          fund_id: "f-1",
          fund_code: "588200",
          fund_name: "科创50ETF",
          stock_id: "st-a",
          stock_code: "688256.SH",
          stock_name: "寒武纪-U",
          weight: 1.27,
          report_period: "2026-03-31",
          source: "占流通A股",
        },
      ],
      derived_from: {
        case_ids: ["RC-AIC-2025-01"],
        thesis_ids: ["th-1"],
        theme_role_ids: ["tr-1"],
        disclosure_ids: ["hd-1"],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        // 标签含中文，需要 percent-encode 检查
        if (url.includes("/themes/")) return jsonResponse(viewDto);
        throw new Error(`unexpected fetch: ${url}`);
      }),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const view = await adapter.getThemeView("算力国产化");

    expect(view.tag).toBe("算力国产化");
    expect(view.cases[0].theses[0].aiConclusion).toBe("supported");
    expect(view.companyRoles[0].companyId).toBe("co-a");
    expect(view.fundExposure[0].weight).toBe(1.27);
    expect(view.derivedFrom).toEqual({
      caseIds: ["RC-AIC-2025-01"],
      thesisIds: ["th-1"],
      themeRoleIds: ["tr-1"],
      disclosureIds: ["hd-1"],
    });
  });

  it("maps an event review queue summary and source admission fields", async () => {
    const queueDto = {
      summary: {
        total: 3,
        reviewed: 1,
        pending: 1,
        invalid_source: 1,
        current_round: 2,
        next_action: "审核 1 条关键证据",
      },
      items: [
        {
          proposal_id: "proposal-1",
          status: "pending",
          proposed_at: "2026-08-07T09:00:00Z",
          link_id: "link-1",
          thesis_id: "thesis-1",
          case_id: "event-1",
          thesis_statement: "资本开支担忧",
          ai_role: "supports",
          ai_reason: "自由现金流承压",
          ai_scope: { period: "2026Q2" },
          statement_id: "statement-1",
          statement_text: "资本开支上调",
          statement_kind: "disclosed_fact",
          span_id: "span-1",
          verbatim_text: "资本开支预计为...",
          locator: { page: 12 },
          document_version_id: "document-1",
          document_source_url: "https://ir.example.org/report",
          document_published_at: "2026-08-01T00:00:00Z",
          available_at: "2026-08-01T00:00:00Z",
          source_title: "公司季度财报",
          source_status: "invalid",
          source_status_reason: "测试域名不能作为正式证据来源",
          can_accept: false,
          proposal_reason: "自由现金流承压",
          position: 2,
        },
        {
          proposal_id: "proposal-2",
          status: "pending",
          proposed_at: "2026-08-07T09:01:00Z",
          link_id: "link-2",
          case_id: "event-1",
          source_status: "accessible",
          source_status_reason: "来源链接可访问且内容已验证",
          can_accept: true,
          proposal_reason: "",
        },
      ],
    };
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(async () => jsonResponse(queueDto));
    vi.stubGlobal("fetch", fetchMock);

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const queue = await adapter.getEventReviewQueue("event-1");

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/api/v1/event-research/event-1/review-queue",
    );
    expect(queue.summary).toEqual({
      total: 3,
      reviewed: 1,
      pending: 1,
      invalidSource: 1,
      currentRound: 2,
      nextAction: "审核 1 条关键证据",
    });
    expect(queue.items[0]).toMatchObject({
      proposalId: "proposal-1",
      sourceTitle: "公司季度财报",
      sourceStatus: "invalid",
      sourceStatusReason: "测试域名不能作为正式证据来源",
      canAccept: false,
      proposalReason: "自由现金流承压",
      position: 2,
      documentSourceUrl: "https://ir.example.org/report",
    });
    expect(queue.items[1]).toMatchObject({
      sourceStatus: "accessible",
      canAccept: true,
    });
  });

  it("defaults omitted optional event review evidence fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({
        summary: { total: 0, reviewed: 0, pending: 0, invalid_source: 0, current_round: 0, next_action: null },
        items: [{ proposal_id: "proposal-3", status: "pending", proposed_at: "2026-08-07T09:00:00Z", link_id: "link-3", case_id: "event-2", source_status: "pasted_unverified", source_status_reason: "来源内容尚未验证", can_accept: false, proposal_reason: "" }],
      })),
    );

    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });
    const queue = await adapter.getEventReviewQueue("event-2");

    expect(queue.items[0]).toMatchObject({
      thesisId: null,
      aiScope: {},
      locator: {},
      statementText: null,
      documentSourceUrl: null,
      sourceTitle: null,
      sourceStatus: "pasted_unverified",
      canAccept: false,
      position: null,
    });
  });

  it("downgrades an unknown event source status to invalid", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({
        summary: { total: 1, reviewed: 0, pending: 0, invalid_source: 0, current_round: 1, next_action: null },
        items: [{ proposal_id: "proposal-unknown", status: "pending", proposed_at: "2026-08-07T09:00:00Z", link_id: "link-unknown", case_id: "event-unknown", source_status: "retired", source_status_reason: "服务端返回了已废弃状态", can_accept: true, proposal_reason: "" }],
      })),
    );

    const queue = await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" })
      .getEventReviewQueue("event-unknown");

    expect(queue.items[0]).toMatchObject({
      sourceStatus: "invalid",
      sourceStatusReason: "服务端返回了已废弃状态",
      canAccept: false,
    });
  });

  it("allows acceptance only for an accessible source", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({
        summary: { total: 2, reviewed: 0, pending: 2, invalid_source: 1, current_round: 1, next_action: null },
        items: [
          { proposal_id: "proposal-invalid", status: "pending", proposed_at: "2026-08-07T09:00:00Z", link_id: "link-invalid", case_id: "event-1", source_status: "invalid", source_status_reason: "invalid source", can_accept: true, proposal_reason: "" },
          { proposal_id: "proposal-pasted", status: "pending", proposed_at: "2026-08-07T09:01:00Z", link_id: "link-pasted", case_id: "event-1", source_status: "pasted_unverified", source_status_reason: "unverified source", can_accept: true, proposal_reason: "" },
        ],
      })),
    );

    const queue = await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" })
      .getEventReviewQueue("event-1");

    expect(queue.items.map((item) => item.canAccept)).toEqual([false, false]);
  });

  it("maps conclusion-first event workbench progress, current scope, and edit action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({
        event: {
          case_id: "event-1", event_title: "Event", company_name: null, ticker: null,
          event_at: null, lifecycle_status: "exhausted", status_summary: "无法下结论",
          next_human_action: null, updated_at: "2026-08-08T00:00:00Z",
        },
        lifecycle: {
          status: "exhausted", active_run_id: null, current_round: 3,
          status_summary: "无法下结论", current_gap: "缺少反证", next_human_action: null,
        },
        conclusion: { state: "cannot_conclude", text: "当前不能下结论", confidence: "medium", citations: [] },
        factors: [{
          statement: "资本开支担忧", position: 1, reviewed_support_count: 2,
          reviewed_contradiction_count: 1, pending_proposal_count: 1, current_gap: "缺少反证",
        }],
        evidence: [],
        progress: { verified: 3, pending: 2, invalid_source: 1, current_gap: "缺少反证" },
        scope: { version: 4, factors: ["资本开支担忧", "盈利预期变化", "估值重定价"], unmapped_evidence_count: 2 },
        next_action: { kind: "edit_factors", label: "编辑并继续自动研究", count: null },
      })),
    );

    const view = await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" })
      .getEventWorkbench("event-1");

    expect(view.progress).toEqual({ verified: 3, pending: 2, invalidSource: 1, currentGap: "缺少反证" });
    expect(view.scope).toEqual({ version: 4, factors: [{ statement: "资本开支担忧", description: null }, { statement: "盈利预期变化", description: null }, { statement: "估值重定价", description: null }], unmappedEvidenceCount: 2 });
    expect(view.factors[0]).toMatchObject({
      statement: "资本开支担忧",
      position: 1,
      reviewedSupportCount: 2,
      reviewedContradictionCount: 1,
      pendingProposalCount: 1,
      currentGap: "缺少反证",
    });
    expect(view.conclusion.confidence).toBe("medium");
    expect(view.nextAction).toEqual({ kind: "edit_factors", label: "编辑并继续自动研究" });
  });

  it("maps event evidence to a Case-owned frozen-document action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({
        event: {
          case_id: "event-1", event_title: "Event", company_name: null, ticker: null,
          event_at: null, lifecycle_status: "awaiting_key_review", status_summary: "等待审核",
          next_human_action: "审核证据", updated_at: "2026-08-08T00:00:00Z",
        },
        lifecycle: {
          status: "awaiting_key_review", active_run_id: null, current_round: 1,
          status_summary: "等待审核", current_gap: null, next_human_action: "审核证据",
        },
        conclusion: { state: "cannot_conclude", text: "尚不能下结论", confidence: "low", citations: [] },
        factors: [],
        evidence: [{
          case_id: "event-1", factor_statement: "资本开支担忧", role: "supports",
          review_state: "reviewed", source_title: "公司披露", source_url: "https://live.example/source",
          document_version_id: "doc-1", source_visible_in_case: true,
          excerpt: "冻结原文", locator: { page: 12 }, available_at: "2026-08-08T00:00:00Z",
        }],
        progress: { verified: 1, pending: 0, invalid_source: 0, current_gap: null },
        scope: { version: 1, factors: [], unmapped_evidence_count: 0 },
        next_action: { kind: "review_evidence", label: "审核证据", count: 1 },
      })),
    );

    const view = await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" })
      .getEventWorkbench("event-1");

    expect(view.evidence[0]).toMatchObject({
      caseId: "event-1",
      factorStatement: "资本开支担忧",
      documentVersionId: "doc-1",
      sourceVisibleInCase: true,
    });
  });

  it("sends event scope updates as PUT and maps the current scope response", async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(async () => jsonResponse({
      version: 2,
      factors: [{ statement: "因素甲", description: null }, { statement: "因素乙", description: null }, { statement: "因素丙", description: null }],
      reclassified_evidence_count: 1,
      unmapped_evidence_count: 2,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const scope = await new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" })
      .updateEventResearchScope({
        caseId: "event-1",
        factors: ["因素甲", "因素乙", "因素丙"],
        changedBy: "reviewer",
        changeReason: "补足验证缺口",
      });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/api/v1/event-research/event-1/scope",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({
        factors: ["因素甲", "因素乙", "因素丙"],
        changed_by: "reviewer",
        change_reason: "补足验证缺口",
      }),
    });
    expect(scope).toEqual({
      version: 2,
      factors: [{ statement: "因素甲", description: null }, { statement: "因素乙", description: null }, { statement: "因素丙", description: null }],
      reclassifiedEvidenceCount: 1,
      unmappedEvidenceCount: 2,
    });
  });

  it("mock event review queue covers all source admission states", async () => {
    const queue = await new MockResearchAdapter().getEventReviewQueue("event-tsm");

    expect(queue.summary).toMatchObject({ total: 3, pending: 1, invalidSource: 1 });
    expect(queue.items.map((item) => item.sourceStatus)).toEqual([
      "accessible",
      "pasted_unverified",
      "invalid",
    ]);
    expect(queue.items.filter((item) => !item.canAccept)).toHaveLength(2);
  });

  it("mock event scope updates persist the latest scope and continue research", async () => {
    const adapter = new MockResearchAdapter();
    const factors = ["新因素甲", "新因素乙", "新因素丙"];

    const updated = await adapter.updateEventResearchScope({
      caseId: "event-exhausted",
      factors: factors.map((statement) => ({ statement, description: null })),
      changedBy: "reviewer",
      changeReason: "调整验证范围",
    });
    const view = await adapter.getEventWorkbench("event-exhausted");

    expect(updated).toEqual({
      version: 2,
      factors: factors.map((statement) => ({ statement, description: null })),
      reclassifiedEvidenceCount: 0,
      unmappedEvidenceCount: 0,
    });
    expect(view.scope).toEqual({
      version: 2,
      factors: factors.map((statement) => ({ statement, description: null })),
      unmappedEvidenceCount: 0,
    });
    expect(view.factors.map((factor) => ({
      statement: factor.statement,
      position: factor.position,
      currentGap: factor.currentGap,
    }))).toEqual([
      { statement: "新因素甲", position: 1, currentGap: "尚缺少可采纳证据" },
      { statement: "新因素乙", position: 2, currentGap: "尚缺少可采纳证据" },
      { statement: "新因素丙", position: 3, currentGap: "尚缺少可采纳证据" },
    ]);
    expect(view.lifecycle).toMatchObject({
      status: "continuing",
      nextHumanAction: null,
      currentGap: "缺少能区分主要解释的反证",
    });
    expect(view.progress.currentGap).toBe("缺少能区分主要解释的反证");
    expect(view.nextAction).toEqual({ kind: "wait", label: "系统继续处理" });
  });
});
