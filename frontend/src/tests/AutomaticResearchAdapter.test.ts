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
        { key: "acquire", label: "资料获取", status: "completed", summary: "已采集", started_at: "2026-08-17T01:00:00Z", completed_at: "2026-08-17T01:01:00Z" },
        { key: "parse", label: "内容解析", status: "completed", summary: "已解析", started_at: null, completed_at: null },
        { key: "admit", label: "证据校验", status: "completed", summary: "已准入", started_at: null, completed_at: null },
        { key: "analyze", label: "分析判断", status: "completed", summary: "已分析", started_at: null, completed_at: null },
        { key: "conclude", label: "生成结论", status: "completed", summary: "已完成", started_at: null, completed_at: null },
      ],
      stats: { source_count: 4, admitted_evidence_count: 3, skipped_count: 1, duration_seconds: 62 },
      narrative: {
        current_action: "研究结论已生成",
        completed_count: 3,
        total_count: 3,
        next_action: "可查看关键因素与证据详情。",
        elapsed_seconds: 62,
        estimated_remaining_seconds_min: null,
        estimated_remaining_seconds_max: null,
      },
      activities: [{
        label: "资料处理",
        count: 2,
        technical_details: [{
          occurred_at: "2026-08-17T01:00:15Z",
          work_item: "来源筛选",
          internal_status: "succeeded",
        }],
      }],
      exceptions: [{
        reason: "来源许可不满足",
        count: 1,
        impact: "来源未通过许可核验，已被过滤。",
        system_action: "系统继续处理其他允许来源。",
      }],
      factors: [{
        statement: "AI 供需变化",
        classification: "key",
        ranking_reason: "与问题直接相关且证据充分。",
        support_count: 3,
        counter_evidence_count: 1,
        evidence_gap: null,
      }],
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
        { key: "acquire", label: "资料获取", status: "completed", summary: "已采集", startedAt: "2026-08-17T01:00:00Z", completedAt: "2026-08-17T01:01:00Z" },
        { key: "parse", label: "内容解析", status: "completed", summary: "已解析", startedAt: null, completedAt: null },
        { key: "admit", label: "证据校验", status: "completed", summary: "已准入", startedAt: null, completedAt: null },
        { key: "analyze", label: "分析判断", status: "completed", summary: "已分析", startedAt: null, completedAt: null },
        { key: "conclude", label: "生成结论", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
      ],
      stats: { sourceCount: 4, admittedEvidenceCount: 3, skippedCount: 1, durationSeconds: 62 },
      narrative: {
        currentAction: "研究结论已生成",
        completedCount: 3,
        totalCount: 3,
        nextAction: "可查看关键因素与证据详情。",
        elapsedSeconds: 62,
        estimatedRemainingSecondsMin: null,
        estimatedRemainingSecondsMax: null,
      },
      activities: [{
        label: "资料处理",
        count: 2,
        technicalDetails: [{
          occurredAt: "2026-08-17T01:00:15Z",
          workItem: "来源筛选",
          internalStatus: "succeeded",
        }],
      }],
      exceptions: [{
        reason: "来源许可不满足",
        count: 1,
        impact: "来源未通过许可核验，已被过滤。",
        systemAction: "系统继续处理其他允许来源。",
      }],
      factors: [{
        statement: "AI 供需变化",
        classification: "key",
        rankingReason: "与问题直接相关且证据充分。",
        supportCount: 3,
        counterEvidenceCount: 1,
        evidenceGap: null,
      }],
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

  it("preserves a failed result with omitted optional stage timestamps", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      case_id: "case-failed",
      run_id: "run-failed",
      title: "失败的自动研究",
      status: "failed",
      stages: [
        { key: "acquire", label: "资料获取", status: "completed", summary: "已采集" },
        { key: "parse", label: "内容解析", status: "failed", summary: "解析失败" },
        { key: "admit", label: "证据校验", status: "pending", summary: "尚未开始" },
        { key: "analyze", label: "分析判断", status: "pending", summary: "尚未开始" },
        { key: "conclude", label: "生成结论", status: "pending", summary: "尚未开始" },
      ],
      stats: { source_count: 1, admitted_evidence_count: 0, skipped_count: 1, duration_seconds: 3 },
      narrative: {
        current_action: "研究未能完成",
        completed_count: 0,
        total_count: 3,
        next_action: "请稍后重试",
        elapsed_seconds: 3,
        estimated_remaining_seconds_min: null,
        estimated_remaining_seconds_max: null,
      },
      activities: [],
      exceptions: [{
        reason: "内容无法解析",
        count: 1,
        impact: "解析内容缺少结构化事实，无法继续。",
        system_action: "系统暂停本轮后返回未完成。",
      }],
      factors: [],
      failure_reason: "自动研究未能完成，请重试。",
      result: null,
    })));
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    const view = await adapter.getAutomaticResearch("case-failed");

    expect(view).toMatchObject({
      status: "failed",
      failureReason: "自动研究未能完成，请重试。",
      stages: expect.any(Array),
      narrative: {
        currentAction: "研究未能完成",
        completedCount: 0,
        totalCount: 3,
        nextAction: "请稍后重试",
        elapsedSeconds: 3,
        estimatedRemainingSecondsMin: null,
        estimatedRemainingSecondsMax: null,
      },
      activities: [],
      exceptions: [{
        reason: "内容无法解析",
        count: 1,
        impact: "解析内容缺少结构化事实，无法继续。",
        systemAction: "系统暂停本轮后返回未完成。",
      }],
      factors: [],
      result: null,
    });
    expect(view.stages.every((stage) =>
      stage.startedAt === null && stage.completedAt === null,
    )).toBe(true);
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

