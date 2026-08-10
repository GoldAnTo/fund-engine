import { describe, expect, it } from "vitest";

import {
  formatRunEventDetails,
  runFrequencyLabel,
  runStageLabel,
  runStatusLabel,
  runStopReasonLabel,
  runTriggerLabel,
} from "./runPresentation";

describe("run presentation", () => {
  it("translates frozen run metadata without changing its scope", () => {
    expect(runStageLabel("retrieve")).toBe("采集资料");
    expect(runStatusLabel("waiting_for_review")).toBe("等待人工审核");
    expect(runTriggerLabel("schedule")).toBe("定时任务");
    expect(runFrequencyLabel("weekday_08_30")).toBe("工作日 08:30");
    expect(runStopReasonLabel("task_failed")).toBe("任务执行失败");
  });

  it("renders source permissions in a run event as research language", () => {
    expect(
      formatRunEventDetails({
        trigger: "manual",
        allowed_source_types: ["licensed_provider", "company_disclosure"],
        stop_reason: "budget_exhausted",
      }),
    ).toBe(
      "触发方式：立即补证 · 允许来源：授权供应商资料、公司披露 · 停止原因：达到资料预算上限",
    );
  });
});
