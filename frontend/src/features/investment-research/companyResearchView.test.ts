import { describe, expect, it } from "vitest";

import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";
import {
  COMPANY_RESEARCH_MODULES,
  answerabilityView,
  artifactByKind,
  numericObservationView,
  preparationIsActive,
  workspaceSnapshotIsMonotonic,
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
      provenanceKind: "external",
      provenanceKey: "revenue_2025",
    });
    expect(numericObservationView({
      key: "fcff", value: "100", unit: "USD million", currency: "USD", period: "FY2025", state: "derived",
      source_ref: { kind: "artifact_computation", artifact_refs: [], market_snapshot_ids: [], equation_id: "fcff.v1" }, gap_key: null, assumption_key: null,
    })).toMatchObject({ traceLabel: "fcff.v1", provenanceKind: "equation", provenanceKey: "fcff.v1" });
    expect(numericObservationView({
      key: "growth", value: "0.1", unit: "ratio", currency: "N/A", period: "FY2025", state: "assumption",
      source_ref: null, gap_key: null, assumption_key: "search_growth",
    })).toMatchObject({ provenanceKind: "assumption", provenanceKey: "search_growth" });
    expect(numericObservationView(null)).toBeNull();
  });

  it("derives answerability without manufacturing direction, confidence, target, or return", () => {
    const preparing = { artifacts: [] } as unknown as CompanyResearchWorkspace;
    const notAnswerable = {
      artifacts: [{ kind: "memo", payload: { assessment_status: "not_answerable", gap_keys: ["missing_segment_margin"] } }],
    } as unknown as CompanyResearchWorkspace;
    const partial = {
      artifacts: [{ kind: "memo", payload: { assessment_status: "partially_answerable", gap_keys: ["missing_regulatory_case"] } }],
    } as unknown as CompanyResearchWorkspace;

    expect(answerabilityView(preparing)).toEqual({
      status: "preparing",
      label: "判断尚在准备",
      blockers: [],
    });
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

  it("rejects component-wise workspace regressions when evidence heads are equal or absent", () => {
    const base = {
      project_id: "project-1",
      preparation: { status: "building_model", current_step: "financial_bridge", progress: 60 },
      artifacts: [
        { kind: "evidence_index", id: "evidence-2", content_hash: "hash-2", version: 2 },
        { kind: "valuation_set", id: "valuation-3", content_hash: "value-3", version: 3 },
      ],
      modules: [
        { key: "overview", state: "ready", valuation_state: "not_applicable" },
        { key: "scenarios_valuation_implied_expectations", state: "ready", valuation_state: "ready" },
      ],
      draft: { lock_version: 4, base_revision_id: "revision-4" },
      selected_revision: "revision-4",
      change_summary: { reviewed_fact_count: 2, artifact_versions: { evidence_index: 2, valuation_set: 3 } },
    } as unknown as CompanyResearchWorkspace;
    const clone = () => structuredClone(base);
    const regressions = [
      ["project", (next: CompanyResearchWorkspace) => { next.project_id = "project-2"; }],
      ["preparation progress", (next: CompanyResearchWorkspace) => { next.preparation.progress = 59; }],
      ["preparation status", (next: CompanyResearchWorkspace) => { next.preparation.status = "awaiting_evidence_review"; }],
      ["preparation stage", (next: CompanyResearchWorkspace) => { next.preparation.current_step = "research_gaps"; }],
      ["draft lock", (next: CompanyResearchWorkspace) => { next.draft.lock_version = 3; }],
      ["frozen revision", (next: CompanyResearchWorkspace) => { next.selected_revision = null; }],
      ["module state", (next: CompanyResearchWorkspace) => { next.modules[0].state = "preparing"; }],
      ["valuation state", (next: CompanyResearchWorkspace) => { next.modules[1].valuation_state = "pending"; }],
      ["reviewed count", (next: CompanyResearchWorkspace) => { next.change_summary.reviewed_fact_count = 1; }],
      ["summary artifact version", (next: CompanyResearchWorkspace) => { next.change_summary.artifact_versions.valuation_set = 2; }],
      ["artifact version", (next: CompanyResearchWorkspace) => { next.artifacts[1].version = 2; }],
    ] as const;
    for (const [_label, regress] of regressions) {
      const next = clone();
      regress(next);
      expect(workspaceSnapshotIsMonotonic(base, next)).toBe(false);
    }
    const noEvidence = clone();
    noEvidence.artifacts = noEvidence.artifacts.filter((item) => item.kind !== "evidence_index");
    delete noEvidence.change_summary.artifact_versions.evidence_index;
    const staleBeforeEvidence = structuredClone(noEvidence);
    staleBeforeEvidence.draft.lock_version = 3;
    expect(workspaceSnapshotIsMonotonic(noEvidence, staleBeforeEvidence)).toBe(false);
  });

  it("accepts downstream staleness only across a strictly newer evidence head", () => {
    const current = {
      project_id: "project-1", preparation: { status: "awaiting_evidence_review", progress: 40 },
      artifacts: [{ kind: "evidence_index", id: "evidence-1", content_hash: "hash-1", version: 1 }],
      modules: [{ key: "overview", state: "ready", valuation_state: "not_applicable" }],
      draft: { lock_version: 1, base_revision_id: null }, selected_revision: null,
      change_summary: { reviewed_fact_count: 0, artifact_versions: { evidence_index: 1 } },
    } as unknown as CompanyResearchWorkspace;
    const successor = structuredClone(current);
    Object.assign(successor.artifacts[0], { id: "evidence-2", content_hash: "hash-2", version: 2 });
    successor.modules[0].state = "preparing";
    expect(workspaceSnapshotIsMonotonic(current, successor)).toBe(true);
    const predecessor = structuredClone(successor);
    predecessor.artifacts[0] = current.artifacts[0];
    expect(workspaceSnapshotIsMonotonic(successor, predecessor)).toBe(false);
  });

  it("does not let a newer evidence head hide independent workspace regressions", () => {
    const current = {
      project_id: "project-1",
      preparation: { id: "preparation-1", status: "awaiting_evidence_review", current_step: "research_gaps", progress: 25 },
      artifacts: [
        { kind: "evidence_index", id: "evidence-1", content_hash: "hash-1", version: 1 },
        { kind: "research_gaps", id: "gaps-2", content_hash: "gaps-hash-2", version: 2 },
      ],
      modules: [{ key: "evidence_and_gaps", state: "needs_review", valuation_state: "not_applicable" }],
      draft: { id: "draft-1", lock_version: 4, base_revision_id: null }, selected_revision: null,
      change_summary: { reviewed_fact_count: 1, artifact_versions: { evidence_index: 1, research_gaps: 2 } },
    } as unknown as CompanyResearchWorkspace;
    const successor = () => {
      const next = structuredClone(current);
      Object.assign(next.artifacts[0], { id: "evidence-2", content_hash: "hash-2", version: 2 });
      next.change_summary.artifact_versions.evidence_index = 2;
      next.change_summary.reviewed_fact_count = 2;
      next.draft.lock_version = 5;
      return next;
    };
    const regressions = [
      ["preparation progress", (next: CompanyResearchWorkspace) => { next.preparation.progress = 24; }],
      ["preparation identity", (next: CompanyResearchWorkspace) => { next.preparation.id = "preparation-2"; }],
      ["draft identity", (next: CompanyResearchWorkspace) => { next.draft.id = "draft-2"; }],
      ["draft version", (next: CompanyResearchWorkspace) => { next.draft.lock_version = 3; }],
      ["draft base without lock advance", (next: CompanyResearchWorkspace) => { next.draft.lock_version = 4; next.draft.base_revision_id = "revision-2"; }],
      ["review count", (next: CompanyResearchWorkspace) => { next.change_summary.reviewed_fact_count = 0; }],
      ["unrelated summary artifact", (next: CompanyResearchWorkspace) => { next.change_summary.artifact_versions.research_gaps = 1; }],
      ["unrelated artifact head", (next: CompanyResearchWorkspace) => { next.artifacts[1].version = 1; }],
    ] as const;
    for (const [_label, regress] of regressions) {
      const next = successor();
      regress(next);
      expect(workspaceSnapshotIsMonotonic(current, next)).toBe(false);
    }
    const beforeEvidence = structuredClone(current);
    beforeEvidence.artifacts = beforeEvidence.artifacts.filter((item) => item.kind !== "evidence_index");
    delete beforeEvidence.change_summary.artifact_versions.evidence_index;
    const firstEvidenceWithStaleDraft = successor();
    firstEvidenceWithStaleDraft.draft.lock_version = 3;
    expect(workspaceSnapshotIsMonotonic(beforeEvidence, firstEvidenceWithStaleDraft)).toBe(false);
  });

  it.each([
    ["evidence retry", "evidence_index", "queued", 0],
    ["model retry", "model_bundle", "building_model", 25],
  ] as const)("accepts the documented %s progress reset without relaxing other invariants", (_label, failedStep, status, progress) => {
    const failed = {
      project_id: "project-1",
      preparation: { id: "preparation-1", status: "recoverable_failure", current_step: failedStep, progress: 80 },
      artifacts: [{ kind: "research_gaps", id: "gaps-2", content_hash: "gaps-2", version: 2 }],
      modules: [{ key: "overview", state: "blocked", valuation_state: "not_applicable" }],
      draft: { id: "draft-1", lock_version: 4, base_revision_id: null }, selected_revision: null,
      change_summary: { reviewed_fact_count: 1, artifact_versions: { research_gaps: 2 } },
    } as unknown as CompanyResearchWorkspace;
    const resumed = structuredClone(failed);
    Object.assign(resumed.preparation, { status, current_step: failedStep, progress });
    resumed.modules[0].state = "preparing";

    expect(workspaceSnapshotIsMonotonic(failed, resumed, { allowRecovery: true })).toBe(true);
    resumed.draft.id = "draft-2";
    expect(workspaceSnapshotIsMonotonic(failed, resumed, { allowRecovery: true })).toBe(false);
  });
});
