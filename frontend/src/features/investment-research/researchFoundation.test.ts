import { describe, expect, it } from "vitest";

import {
  agendaItemsHash,
  generateFoundationAgenda,
  toFrozenIso,
  type FoundationAgendaInput,
} from "./researchFoundation";

const input: FoundationAgendaInput = {
  company: {
    objectId: "00000000-0000-4000-8000-000000000001",
    canonicalName: "测试公司",
    externalKey: "TEST:COMPANY",
  },
  securities: [
    {
      objectId: "00000000-0000-4000-8000-000000000002",
      canonicalName: "测试证券 B",
      externalKey: "TEST:B",
      symbol: "TST.B",
    },
    {
      objectId: "00000000-0000-4000-8000-000000000003",
      canonicalName: "测试证券 A",
      externalKey: "TEST:A",
      symbol: "TST.A",
    },
  ],
  mandate: {
    horizonYears: 3,
    baseCurrency: "CNY",
    requiredReturn: "0.12",
    permanentLossLimit: "0.25",
    comparisonSet: ["同行 B", "同行 A"],
    benchmarkKey: null,
    requiredExcessReturn: null,
  },
  scope: {
    industryNames: ["行业 B", "行业 A"],
    coveredSegments: [],
    userFocus: null,
    exclusions: [],
  },
};

describe("investment research foundation helpers", () => {
  it("freezes datetime-local values against an explicit offset independent of host TZ", () => {
    expect(toFrozenIso("2026-08-24T08:05", "+08:00"))
      .toBe("2026-08-24T00:05:00.000Z");
    expect(toFrozenIso("2026-08-24T08:05", "Z"))
      .toBe("2026-08-24T08:05:00.000Z");
    expect(() => Reflect.apply(toFrozenIso, null, ["2026-08-24T08:05", "+09:00"]))
      .toThrow("不支持的冻结时区");
    expect(() => toFrozenIso("not-a-time", "+08:00"))
      .toThrow("时间格式无法识别");
    expect(() => toFrozenIso("2026-02-30T08:05", "+08:00"))
      .toThrow("时间格式无法识别");
  });

  it("generates a canonical generic agenda and stable provenance hashes", async () => {
    await expect(agendaItemsHash(["核验公司边界"]))
      .resolves.toBe("57e7c6fda6962645939bf3b4ac9ece70be170d1170a2571252593c42a50ece73");
    const first = await generateFoundationAgenda(input);
    const second = await generateFoundationAgenda({
      ...input,
      securities: input.securities.slice().reverse(),
      mandate: {
        ...input.mandate,
        comparisonSet: input.mandate.comparisonSet.slice().reverse(),
      },
      scope: {
        ...input.scope,
        industryNames: input.scope.industryNames.slice().reverse(),
      },
    });

    expect(first).toEqual(second);
    expect(first.items).toHaveLength(6);
    expect(first.items.join(" ")).toContain("测试公司");
    expect(first.items.join(" ")).toContain("TST.A、TST.B");
    expect(first.items.join(" ")).not.toMatch(/推荐|目标价|仓位|已经完成/);
    expect(first.generator).toMatchObject({
      schema_version: "underwriting.v1",
      method: "deterministic_template",
      template_key: "product.foundation.agenda",
      template_version: "1.0.0",
      model_name: null,
      prompt_template_version: null,
    });
    expect(first.inputSummaryHash).toMatch(/^[0-9a-f]{64}$/);
    expect(first.generator.input_summary_hash).toBe(first.inputSummaryHash);
    expect(first.generator.output_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("changes both canonical hashes when mandate input changes", async () => {
    const baseline = await generateFoundationAgenda(input);
    const changed = await generateFoundationAgenda({
      ...input,
      mandate: { ...input.mandate, horizonYears: 5 },
    });

    expect(changed.inputSummaryHash).not.toBe(baseline.inputSummaryHash);
    expect(changed.generator.output_hash)
      .not.toBe(baseline.generator.output_hash);
  });
});
