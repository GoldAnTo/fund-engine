import { describe, expect, it } from "vitest";

import {
  automaticResearchStatusLabel,
  automaticStageStatusLabel,
  formatAutomaticDuration,
  formatAutomaticTimestamp,
} from "./automaticResearch";

describe("automatic research presentation helpers", () => {
  it.each([
    ["queued", "已排队"],
    ["running", "处理中"],
    ["completed", "已完成"],
    ["failed", "未完成"],
  ] as const)("gives %s a textual overall status", (status, label) => {
    expect(automaticResearchStatusLabel(status)).toBe(label);
  });

  it.each([
    ["pending", "等待中"],
    ["running", "进行中"],
    ["completed", "已完成"],
    ["failed", "未完成"],
  ] as const)("gives %s a textual stage status", (status, label) => {
    expect(automaticStageStatusLabel(status)).toBe(label);
  });

  it("formats real durations without inventing a missing value", () => {
    expect(formatAutomaticDuration(null)).toBeNull();
    expect(formatAutomaticDuration(Number.NaN)).toBeNull();
    expect(formatAutomaticDuration(0)).toBe("0 秒");
    expect(formatAutomaticDuration(65)).toBe("1 分 5 秒");
    expect(formatAutomaticDuration(3660)).toBe("1 小时 1 分");
  });

  it("formats valid timestamps and leaves absent or invalid timestamps empty", () => {
    expect(formatAutomaticTimestamp(null)).toBeNull();
    expect(formatAutomaticTimestamp("not-a-date")).toBeNull();
    expect(formatAutomaticTimestamp("2026-08-17T02:03:00Z")).toMatch(/2026/);
  });
});
