import { describe, expect, it } from "vitest";

import {
  formatRunEventDetails,
  monitorStatusLabel,
  runFrequencyLabel,
  runStageLabel,
  runStatusLabel,
  runStopReasonLabel,
  runTriggerLabel,
} from "./runPresentation";

describe("run presentation", () => {
  it("translates frozen run metadata without changing its scope", () => {
    expect(runStageLabel("retrieve")).toBe("采集资料");
    expect(runStageLabel("resume_after_claim_review")).toBe("审核完成，等待继续执行");
    expect(runStageLabel("source_scope")).toBe("核对资料来源范围");
    expect(runStatusLabel("waiting_for_review")).toBe("等待人工审核");
    expect(runStatusLabel("awaiting_review")).toBe("等待人工审核");
    expect(runTriggerLabel("schedule")).toBe("定时任务");
    expect(runFrequencyLabel("weekday_08_30")).toBe("工作日 08:30");
    expect(runFrequencyLabel("weekday_12_30")).toBe("工作日 12:30");
    expect(runFrequencyLabel("daily_20_00")).toBe("每日 20:00");
    expect(monitorStatusLabel("active")).toBe("已启用");
    expect(monitorStatusLabel("paused")).toBe("已暂停");
    expect(runTriggerLabel("factor_manual")).toBe("立即补证此因素");
    expect(runTriggerLabel("material_continuation")).toBe("新增材料重新复核");
    expect(runStopReasonLabel("task_failed")).toBe("任务执行失败");
  });

  it("renders source permissions in a run event as research language", () => {
    expect(
      formatRunEventDetails({
        trigger: "factor_manual",
        allowed_source_types: ["licensed_provider", "company_disclosure"],
        stop_reason: "budget_exhausted",
      }),
    ).toBe(
      "触发方式：立即补证此因素 · 允许来源：授权供应商资料、公司披露 · 停止原因：达到资料预算上限",
    );
  });

  it("explains source-scope exclusions without exposing internal reason codes", () => {
    expect(
      formatRunEventDetails({
        allowed_source_types: ["company_disclosure"],
        excluded_count: 2,
        excluded_by_reason: { source_type_not_in_frozen_scope: 2 },
      }),
    ).toBe(
      "允许来源：公司披露 · 已排除资料：2 · 排除原因：来源类型不在本次冻结范围内：2",
    );
  });
});
