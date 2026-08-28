import { describe, expect, it } from "vitest";

import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";
import {
  COMPANY_RESEARCH_MODULES,
  answerabilityView,
  artifactByKind,
  numericObservationView,
  preparationIsActive,
} from "./companyResearchView";

describe("company research view model", () => {
  it("keeps the approved nine modules in product order", () => {
    expect(COMPANY_RESEARCH_MODULES.map(({ key, label }) => [key, label])).toEqual([
      ["overview", "概览与当前判断"],
      ["business_map", "Google 如何赚钱"],
      ["operating_drivers", "关键经营变量"],
      ["evidence_and_gaps", "来源、事实与缺口"],
      ["industry_competition_regulation", "行业、竞争与监管"],
      ["financials_cash_flow_capital_allocation", "财务、现金流与资本配置"],
      ["scenarios_valuation_implied_expectations", "情景、估值与当前价格隐含"],
      ["counterevidence_risks_next_checks", "反证、风险与下一验证"],
      ["versions_changes_memo", "版本、变化与研究备忘录"],
    ]);
  });

  it("exposes complete numeric metadata and never invents an absent value", () => {
    expect(numericObservationView({
      key: "revenue",
      value: "307394000000",
      unit: "currency",
      currency: "USD",
      period: "FY2025",
      state: "reported",
      source_ref: {
        kind: "external",
        fact_key: "revenue_2025",
        source_role: "filing",
        source_url: "https://example.test/10-k",
        source_locator: "10-K p. 32",
        raw_hash: "a".repeat(64),
      },
      gap_key: null,
      assumption_key: null,
    })).toEqual({
      value: "307394000000",
      unit: "currency",
      currency: "USD",
      period: "FY2025",
      state: "reported",
      traceLabel: "10-K p. 32",
      traceUrl: "https://example.test/10-k",
    });
    expect(numericObservationView(null)).toBeNull();
  });

  it("derives answerability without manufacturing direction, confidence, target, or return", () => {
    const notAnswerable = {
      artifacts: [{ kind: "memo", payload: { assessment_status: "not_answerable", gap_keys: ["missing_segment_margin"] } }],
    } as unknown as CompanyResearchWorkspace;
    const partial = {
      artifacts: [{ kind: "memo", payload: { assessment_status: "partially_answerable", gap_keys: ["missing_regulatory_case"] } }],
    } as unknown as CompanyResearchWorkspace;

    expect(answerabilityView(notAnswerable)).toEqual({
      status: "not_answerable",
      label: "当前不可回答",
      blockers: ["missing_segment_margin"],
    });
    expect(answerabilityView(partial)).toEqual({
      status: "partially_answerable",
      label: "暂定判断",
      blockers: ["missing_regulatory_case"],
    });
    expect(answerabilityView(notAnswerable)).not.toHaveProperty("direction");
    expect(answerabilityView(notAnswerable)).not.toHaveProperty("confidence");
    expect(answerabilityView(notAnswerable)).not.toHaveProperty("target");
    expect(answerabilityView(notAnswerable)).not.toHaveProperty("return");
  });

  it("selects exact artifact kinds and identifies only active preparation states", () => {
    const evidence = { kind: "evidence_index", version: 2 };
    const workspace = { artifacts: [evidence] } as unknown as CompanyResearchWorkspace;
    expect(artifactByKind(workspace, "evidence_index")).toBe(evidence);
    expect(artifactByKind(workspace, "valuation_set")).toBeNull();
    expect(preparationIsActive("building_model")).toBe(true);
    expect(preparationIsActive("awaiting_evidence_review")).toBe(true);
    expect(preparationIsActive("recoverable_failure")).toBe(false);
    expect(preparationIsActive("blocked")).toBe(false);
    expect(preparationIsActive("completed")).toBe(false);
  });
});
