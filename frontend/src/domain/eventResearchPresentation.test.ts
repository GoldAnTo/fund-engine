import { describe, expect, it } from "vitest";

import type { EventWorkbench } from "./eventResearch";
import {
  eventActionPresentation,
  eventResearchStage,
  EVENT_RESEARCH_STAGES,
} from "./eventResearchPresentation";

function workbench(
  lifecycleStatus: EventWorkbench["lifecycle"]["status"],
  nextAction: EventWorkbench["nextAction"],
): EventWorkbench {
  return {
    event: {
      id: "event-1",
      eventTitle: "测试事件",
      companyName: "测试公司",
      ticker: "TEST",
      eventAt: "2026-08-12T00:00:00Z",
      status: lifecycleStatus,
      statusSummary: "状态说明",
      nextHumanAction: nextAction.kind === "wait" ? null : nextAction.label,
      updatedAt: "2026-08-12T08:00:00Z",
    },
    lifecycle: {
      status: lifecycleStatus,
      activeRunId: lifecycleStatus === "researching" ? "run-1" : null,
      currentRound: 1,
      summary: "状态说明",
      currentGap: "当前缺少能区分主要解释的证据",
      nextHumanAction: nextAction.kind === "wait" ? null : nextAction.label,
    },
    conclusion: {
      state: lifecycleStatus === "published" ? "published" : "cannot_conclude",
      text: "暂不下结论",
      confidence: "low",
      citations: [],
    },
    factors: [],
    evidence: [],
    progress: { verified: 0, pending: 0, invalidSource: 0, currentGap: null },
    scope: { version: 2, factors: [], unmappedEvidenceCount: 0 },
    nextAction,
  };
}

describe("event research presentation", () => {
  it("maps backend lifecycle states onto the six user-facing research stages", () => {
    expect(EVENT_RESEARCH_STAGES.map((stage) => stage.label)).toEqual([
      "资料接入",
      "定义研究",
      "执行补证",
      "审核证据",
      "形成结论",
      "持续跟踪",
    ]);
    expect(eventResearchStage(workbench("extracting", { kind: "wait", label: "等待资料解析" }))).toBe(1);
    expect(eventResearchStage(workbench("awaiting_scope", { kind: "edit_factors", label: "确认研究范围" }))).toBe(2);
    expect(eventResearchStage(workbench("researching", { kind: "wait", label: "系统补证中" }))).toBe(3);
    expect(eventResearchStage(workbench("awaiting_key_review", { kind: "review_evidence", label: "审核证据" }))).toBe(4);
    expect(eventResearchStage(workbench("draft_ready", { kind: "review_conclusion", label: "复核结论" }))).toBe(5);
    expect(eventResearchStage(workbench("published", { kind: "wait", label: "等待下一验证事件" }))).toBe(6);
  });

  it("describes a concrete human task and routes it to the existing work surface", () => {
    const presentation = eventActionPresentation(
      workbench("awaiting_key_review", { kind: "review_evidence", label: "审核 3 条候选证据", count: 3 }),
      "event-1",
    );

    expect(presentation.owner).toBe("你需要做");
    expect(presentation.title).toBe("审核 3 条候选证据");
    expect(presentation.steps).toEqual([
      "核对冻结原文与精确位置",
      "判断证据支持、反驳或需要补证",
      "填写理由并提交审核记录",
    ]);
    expect(presentation.to).toBe("/events/event-1/review");
    expect(presentation.buttonLabel).toBe("进入证据审核");
  });

  it("routes protocol completion to the Case protocol page", () => {
    const presentation = eventActionPresentation(
      workbench("awaiting_scope", {
        kind: "complete_research_protocol",
        label: "完成新增因素的研究协议后再启动补证",
      }),
      "event-1",
    );

    expect(presentation.to).toBe("/events/event-1/protocol");
    expect(presentation.buttonLabel).toBe("完成研究协议");
  });

  it("makes automatic work explicit without inventing a human action", () => {
    const presentation = eventActionPresentation(
      workbench("researching", { kind: "wait", label: "系统正在补证" }),
      "event-1",
    );

    expect(presentation.owner).toBe("现在不用做");
    expect(presentation.title).toBe("系统正在补证");
    expect(presentation.steps).toContain("需要判断时生成明确的人工任务");
    expect(presentation.to).toBe("/events/event-1/monitor");
    expect(presentation.buttonLabel).toBe("查看系统正在做什么");
  });
});
