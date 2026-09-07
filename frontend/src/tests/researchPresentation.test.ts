import { expect, it } from "vitest";
import { researchLabel, researchTime } from "@/workbench/researchPresentation";
it("translates known server vocabulary and preserves unknown statuses", () => {
  expect(researchLabel("queued")).toBe("已排队");
  expect(researchLabel("cannot_conclude")).toBe("暂不能下结论");
  expect(researchLabel("awaiting_key_review")).toBe("等待关键因素审核");
  expect(researchLabel("low")).toBe("低");
  expect(researchLabel("new_server_state")).toBe("new_server_state");
});
it("formats API UTC times as local readable time without guessing invalid dates", () => {
  expect(researchTime("2026-09-06T04:14:34.571822", "Asia/Shanghai")).toBe("2026/09/06 12:14");
  expect(researchTime("2026-09-06T04:14:34Z", "Asia/Shanghai")).toBe("2026/09/06 12:14");
  expect(researchTime("未知")).toBe("未知");
});
