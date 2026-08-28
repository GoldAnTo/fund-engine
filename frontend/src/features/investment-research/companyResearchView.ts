import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";

type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type ArtifactKind = WorkspaceArtifact["kind"];
type PreparationStatus = CompanyResearchWorkspace["preparation"]["status"];
type NumericObservation = Extract<WorkspaceArtifact, { kind: "evidence_index" }>["payload"]["facts"][number]["observation"];

export const COMPANY_RESEARCH_MODULES = [
  { key: "overview", label: "概览与当前判断" },
  { key: "business_map", label: "Google 如何赚钱" },
  { key: "operating_drivers", label: "关键经营变量" },
  { key: "evidence_and_gaps", label: "来源、事实与缺口" },
  { key: "industry_competition_regulation", label: "行业、竞争与监管" },
  { key: "financials_cash_flow_capital_allocation", label: "财务、现金流与资本配置" },
  { key: "scenarios_valuation_implied_expectations", label: "情景、估值与当前价格隐含" },
  { key: "counterevidence_risks_next_checks", label: "反证、风险与下一验证" },
  { key: "versions_changes_memo", label: "版本、变化与研究备忘录" },
] as const;

export type CompanyResearchModuleKey = typeof COMPANY_RESEARCH_MODULES[number]["key"];

export function numericObservationView(observation: NumericObservation | null) {
  if (observation === null) return null;
  const source = observation.source_ref;
  const provenanceKind = source?.kind === "external"
    ? "external"
    : observation.gap_key
      ? "gap"
      : observation.assumption_key
        ? "assumption"
        : source?.equation_id
          ? "equation"
          : "unavailable";
  const provenanceKey = source?.kind === "external"
    ? source.fact_key
    : observation.gap_key ?? observation.assumption_key ?? source?.equation_id ?? "来源待补充";
  return {
    value: observation.value,
    unit: observation.unit,
    currency: observation.currency,
    period: observation.period,
    state: observation.state,
    traceLabel: source?.kind === "external"
      ? source.source_locator
      : observation.gap_key ?? observation.assumption_key ?? source?.equation_id ?? "来源待补充",
    traceUrl: source?.kind === "external" ? source.source_url : null,
    provenanceKind,
    provenanceKey,
  };
}

export function answerabilityView(workspace: CompanyResearchWorkspace) {
  const memo = artifactByKind(workspace, "memo");
  if (memo === null) {
    return { status: "preparing" as const, label: "判断尚在准备", blockers: [] as string[] };
  }
  const status = memo.payload.assessment_status;
  const labels = {
    not_answerable: "当前不可回答",
    partially_answerable: "暂定判断",
    answerable: "可回答",
  } as const;
  return {
    status,
    label: labels[status],
    blockers: memo.payload.gap_keys,
  };
}

export function artifactByKind<K extends ArtifactKind>(workspace: CompanyResearchWorkspace, kind: K): Extract<WorkspaceArtifact, { kind: K }> | null {
  return (workspace.artifacts.find((artifact) => artifact.kind === kind) as Extract<WorkspaceArtifact, { kind: K }> | undefined) ?? null;
}

export function preparationIsActive(status: PreparationStatus): boolean {
  return status !== "recoverable_failure" && status !== "blocked" && status !== "completed";
}
