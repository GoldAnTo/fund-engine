import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("automatic research HTTP adapter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("starts automatic research with only the user's input", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ case_id: "case-1", run_id: "run-1", status: "queued" }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(adapter.startAutomaticResearch("英伟达新产品会如何影响供应链？"))
      .resolves.toEqual({ caseId: "case-1", runId: "run-1", status: "queued" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/v1/automatic-research",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: "英伟达新产品会如何影响供应链？" }),
      }),
    );
  });

  it("maps a completed automatic result and all five stages explicitly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      case_id: "case-1",
      run_id: "run-1",
      title: "英伟达供应链研究",
      status: "completed",
      stages: [
        { key: "acquire", label: "采集", status: "completed", summary: "已采集", started_at: "2026-08-17T01:00:00Z", completed_at: "2026-08-17T01:01:00Z" },
        { key: "parse", label: "解析", status: "completed", summary: "已解析", started_at: null, completed_at: null },
        { key: "admit", label: "准入", status: "completed", summary: "已准入", started_at: null, completed_at: null },
        { key: "analyze", label: "分析", status: "completed", summary: "已分析", started_at: null, completed_at: null },
        { key: "conclude", label: "结论", status: "completed", summary: "已完成", started_at: null, completed_at: null },
      ],
      stats: { source_count: 4, admitted_evidence_count: 3, skipped_count: 1, duration_seconds: 62 },
      recent_activity: ["采集完成", "结论生成完成"],
      exceptions: [{ reason: "来源不可访问", stage: "acquire", count: 1 }],
      failure_reason: null,
      result: {
        label: "系统生成，未经人工审核",
        human_reviewed: false,
        conclusion: "供应链需求可能增加。",
        key_findings: ["需求上升"],
        counter_evidence: ["交付仍受约束"],
        limitations: ["缺少季度更新"],
        sources: [
          { title: "公司公告", url: "https://example.test/source", role: "supports", review_state: "automatically_admitted" },
          { title: null, url: null, role: "contextualizes", review_state: "automatically_admitted" },
        ],
      },
    })));
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    const view = await adapter.getAutomaticResearch("case-1");

    expect(view).toEqual({
      caseId: "case-1",
      runId: "run-1",
      title: "英伟达供应链研究",
      status: "completed",
      stages: [
        { key: "acquire", label: "采集", status: "completed", summary: "已采集", startedAt: "2026-08-17T01:00:00Z", completedAt: "2026-08-17T01:01:00Z" },
        { key: "parse", label: "解析", status: "completed", summary: "已解析", startedAt: null, completedAt: null },
        { key: "admit", label: "准入", status: "completed", summary: "已准入", startedAt: null, completedAt: null },
        { key: "analyze", label: "分析", status: "completed", summary: "已分析", startedAt: null, completedAt: null },
        { key: "conclude", label: "结论", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
      ],
      stats: { sourceCount: 4, admittedEvidenceCount: 3, skippedCount: 1, durationSeconds: 62 },
      recentActivity: ["采集完成", "结论生成完成"],
      exceptions: [{ reason: "来源不可访问", stage: "acquire", count: 1 }],
      failureReason: null,
      result: {
        label: "系统生成，未经人工审核",
        humanReviewed: false,
        conclusion: "供应链需求可能增加。",
        keyFindings: ["需求上升"],
        counterEvidence: ["交付仍受约束"],
        limitations: ["缺少季度更新"],
        sources: [
          { title: "公司公告", url: "https://example.test/source", role: "supports", reviewState: "automatically_admitted" },
          { title: null, url: null, role: "contextualizes", reviewState: "automatically_admitted" },
        ],
      },
    });
  });

  it("retries a failed automatic research case through the dedicated endpoint", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ case_id: "case-1", run_id: "run-2", status: "queued" }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(adapter.retryAutomaticResearch("case-1"))
      .resolves.toEqual({ caseId: "case-1", runId: "run-2", status: "queued" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/v1/automatic-research/case-1/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
