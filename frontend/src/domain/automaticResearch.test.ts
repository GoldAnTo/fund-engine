import { describe, expect, it } from "vitest";

import {
  AUTOMATIC_STAGE_ORDER,
  automaticResearchPollDelay,
  automaticResearchStatusLabel,
  automaticStageStatusLabel,
  formatAutomaticDuration,
  formatAutomaticTimestamp,
  normalizeAutomaticResearchStages,
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
    expect(formatAutomaticTimestamp("2026-08-17T02:03:04Z")).toMatch(
      /2026.*\d{2}:\d{2}:\d{2}/,
    );
  });

  it("bounds transient polling backoff while preserving the normal cadence", () => {
    expect(automaticResearchPollDelay(0)).toBe(2_000);
    expect(automaticResearchPollDelay(1)).toBe(4_000);
    expect(automaticResearchPollDelay(2)).toBe(8_000);
    expect(automaticResearchPollDelay(20)).toBe(8_000);
  });

  it("normalizes a complete unique stage set into canonical order", () => {
    const stages = [...AUTOMATIC_STAGE_ORDER].reverse().map((key) => ({
      key,
      label: key,
      status: "completed",
      summary: `${key} completed`,
      startedAt: null,
      completedAt: null,
    }));

    expect(normalizeAutomaticResearchStages(stages)?.map((stage) => stage.key)).toEqual(
      AUTOMATIC_STAGE_ORDER,
    );
  });

  it.each([
    {
      name: "missing",
      stages: ["acquire", "parse", "admit", "analyze"],
    },
    {
      name: "duplicate",
      stages: ["acquire", "parse", "admit", "analyze", "analyze"],
    },
    {
      name: "unknown",
      stages: ["acquire", "parse", "admit", "analyze", "unknown"],
    },
  ])("rejects a $name stage projection", ({ stages }) => {
    expect(normalizeAutomaticResearchStages(stages.map((key) => ({
      key,
      label: key,
      status: "completed",
      summary: "done",
      startedAt: null,
      completedAt: null,
    })))).toBeNull();
  });

});
