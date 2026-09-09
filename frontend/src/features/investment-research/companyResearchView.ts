import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";

type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type ArtifactKind = WorkspaceArtifact["kind"];
type PreparationStatus = CompanyResearchWorkspace["preparation"]["status"];
type NumericObservation = Extract<WorkspaceArtifact, { kind: "evidence_index" }>["payload"]["facts"][number]["observation"];
type WorkspaceMonotonicOptions = { allowRecovery?: boolean };

const PREPARATION_RANK: Record<PreparationStatus, number> = {
  queued: 0, preparing_sources: 1, awaiting_evidence_review: 2, building_model: 3, awaiting_judgment_review: 4,
  ready_to_freeze: 5, recoverable_failure: 6, blocked: 7, completed: 8,
};
const MODULE_RANK: Record<CompanyResearchWorkspace["modules"][number]["state"], number> = {
  not_started: 0, preparing: 1, needs_review: 2, ready: 3, blocked: 4,
};
const VALUATION_RANK: Record<CompanyResearchWorkspace["modules"][number]["valuation_state"], number> = {
  not_applicable: 0, pending: 1, ready: 2, blocked: 3,
};
const EVIDENCE_DOWNSTREAM_MODULES = new Set<CompanyResearchWorkspace["modules"][number]["key"]>([
  "overview", "business_map", "operating_drivers", "industry_competition_regulation",
  "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations",
  "counterevidence_risks_next_checks", "versions_changes_memo",
]);
const RETRYABLE_ARTIFACT_STEPS = new Set([
  "evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set",
  "valuation_set", "research_gaps", "judgment_context", "memo",
]);

export const COMPANY_RESEARCH_MODULES = [
  { key: "overview", label: "概览与当前判断" },
  { key: "business_map", label: "公司如何赚钱" },
  { key: "operating_drivers", label: "关键经营变量" },
  { key: "evidence_and_gaps", label: "来源、事实与缺口" },
  { key: "industry_competition_regulation", label: "行业、竞争与监管" },
  { key: "financials_cash_flow_capital_allocation", label: "财务、现金流与资本配置" },
  { key: "scenarios_valuation_implied_expectations", label: "情景、估值与当前价格隐含" },
  { key: "counterevidence_risks_next_checks", label: "反证、风险与下一验证" },
  { key: "versions_changes_memo", label: "版本、变化与研究备忘录" },
] as const;

export const COMPANY_RESEARCH_PAGES = [
  { key: "overview", label: "研究概览", description: "先看研究结论、证据边界与下一步。", modules: ["overview"] },
  { key: "business", label: "商业模式", description: "理解收入来源、成本与资本需求，核对竞争环境和反证。", modules: ["business_map", "industry_competition_regulation", "counterevidence_risks_next_checks"] },
  { key: "forecast", label: "预测与情景", description: "从经营驱动到财务预测，查看不同情景的变化机制。", modules: ["operating_drivers", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations"] },
  { key: "valuation", label: "价值判断", description: "结合情景价值与当前价格隐含预期，保留证据和估值边界。", modules: ["scenarios_valuation_implied_expectations"] },
  { key: "evidence", label: "证据中心", description: "核对来源、审核事实，并追踪尚未关闭的研究缺口。", modules: ["evidence_and_gaps"] },
  { key: "versions", label: "备忘录与版本", description: "确认研究判断，保存可回放的冻结版本。", modules: ["versions_changes_memo"] },
] as const;

export type CompanyResearchPageKey = typeof COMPANY_RESEARCH_PAGES[number]["key"];

export type CompanyResearchModuleKey = typeof COMPANY_RESEARCH_MODULES[number]["key"];

export type CompanyResearchPublicationAction =
  | { kind: "confirm_judgment"; label: "确认当前判断" }
  | { kind: "preview_freeze"; label: "预览冻结版本" }
  | { kind: "replay_export"; label: "查看冻结版本" };

export function publicationAction(workspace: CompanyResearchWorkspace): CompanyResearchPublicationAction | null {
  const { current_step: currentStep, progress, status } = workspace.preparation;
  if (status === "awaiting_judgment_review") {
    if (currentStep !== "judgment_context" || progress !== 85) throw new Error("研究发布状态不一致");
    return { kind: "confirm_judgment", label: "确认当前判断" };
  }
  if (status === "ready_to_freeze") {
    if (currentStep !== "memo" || progress !== 95) throw new Error("研究发布状态不一致");
    return { kind: "preview_freeze", label: "预览冻结版本" };
  }
  if (status === "completed") {
    if (currentStep !== null || progress !== 100 || workspace.selected_revision === null) throw new Error("研究发布状态不一致");
    return { kind: "replay_export", label: "查看冻结版本" };
  }
  return null;
}

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

// Labels cover the existing operating metric vocabulary. Unrecognized codes stay opaque.
const OPERATING_GAP_LABELS: Readonly<Record<string, string>> = {
  search_query_intensity: "搜索查询强度",
  search_ad_monetization: "搜索广告变现",
  traffic_acquisition_cost: "流量获取成本",
  youtube_usage: "YouTube 使用量",
  youtube_ad_monetization: "YouTube 广告变现",
  youtube_subscription_growth: "YouTube 订阅增长",
  cloud_workload: "云业务工作负载",
  cloud_revenue_growth: "云业务收入增长",
  cloud_operating_margin: "云业务营业利润率",
  ai_data_center_capex: "AI 数据中心资本开支",
  infrastructure_depreciation: "基础设施折旧",
  infrastructure_opex: "基础设施运营支出",
  free_cash_flow: "自由现金流",
  stock_based_compensation: "股份薪酬",
  share_repurchases: "股份回购",
  dilution: "股份摊薄",
  consolidated_revenue: "合并收入",
};

export function researchBlockerGroups(codes: readonly string[]): { label: string; codes: string[] }[] {
  const groups = new Map<string, { label: string; codes: string[] }>();
  for (const code of codes) {
    const metric = /^builder_generated_operating_(?:baseline|driver)_missing_(.+)$/.exec(code)?.[1];
    const metricLabel = metric !== undefined && Object.prototype.hasOwnProperty.call(OPERATING_GAP_LABELS, metric)
      ? OPERATING_GAP_LABELS[metric] : undefined;
    const key = metricLabel === undefined ? "unknown" : metric!;
    const label = metricLabel === undefined
      ? "其他研究缺口仍需核对，具体记录见审计详情。"
      : `${metricLabel}的经营基线或驱动依据尚不完整。`;
    const group = groups.get(key);
    if (group) group.codes.push(code);
    else groups.set(key, { label, codes: [code] });
  }
  return [...groups.values()];
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

function versionsAreMonotonic(current: Readonly<Record<string, number>>, next: Readonly<Record<string, number>>): boolean {
  return Object.entries(current).every(([kind, version]) => next[kind] !== undefined && next[kind] >= version);
}

function isDocumentedRecovery(current: CompanyResearchWorkspace, next: CompanyResearchWorkspace): boolean {
  if (current.preparation.status !== "recoverable_failure" || next.preparation.current_step !== current.preparation.current_step) return false;
  return (current.preparation.current_step !== null && RETRYABLE_ARTIFACT_STEPS.has(current.preparation.current_step)
      && next.preparation.status === "queued" && next.preparation.progress === 0)
    || (current.preparation.current_step === "model_bundle" && next.preparation.status === "building_model" && next.preparation.progress === 25);
}

function artifactHeadsAreMonotonic(current: CompanyResearchWorkspace, next: CompanyResearchWorkspace): boolean {
  return current.artifacts.every((artifact) => {
    const candidate = next.artifacts.find((item) => item.kind === artifact.kind);
    return candidate !== undefined && candidate.version >= artifact.version
      && (candidate.version !== artifact.version || (candidate.id === artifact.id && candidate.content_hash === artifact.content_hash));
  });
}

export function workspaceSnapshotIsMonotonic(current: CompanyResearchWorkspace, next: CompanyResearchWorkspace, options: WorkspaceMonotonicOptions = {}): boolean {
  if (current.project_id !== next.project_id) return false;
  if (current.company.id !== next.company.id) return false;
  if (current.preparation.id !== next.preparation.id || current.draft.id !== next.draft.id) return false;
  const currentEvidence = artifactByKind(current, "evidence_index");
  const nextEvidence = artifactByKind(next, "evidence_index");
  const evidenceAdvanced = nextEvidence !== null && (currentEvidence === null || nextEvidence.version > currentEvidence.version);
  if (currentEvidence !== null) {
    if (nextEvidence === null || nextEvidence.version < currentEvidence.version) return false;
    if (nextEvidence.version === currentEvidence.version && (nextEvidence.id !== currentEvidence.id || nextEvidence.content_hash !== currentEvidence.content_hash)) return false;
  }

  const recovery = options.allowRecovery === true && isDocumentedRecovery(current, next);
  if (options.allowRecovery === true && current.preparation.status === "recoverable_failure" && !recovery) return false;
  if (!recovery && (next.preparation.progress < current.preparation.progress || PREPARATION_RANK[next.preparation.status] < PREPARATION_RANK[current.preparation.status])) return false;
  if (!recovery && next.preparation.progress === current.preparation.progress && next.preparation.status === current.preparation.status && next.preparation.current_step !== current.preparation.current_step) return false;
  if (next.draft.lock_version < current.draft.lock_version) return false;
  if (next.draft.lock_version === current.draft.lock_version && next.draft.base_revision_id !== current.draft.base_revision_id) return false;
  if (current.selected_revision !== null && next.selected_revision !== current.selected_revision) return false;
  if (next.change_summary.reviewed_fact_count < current.change_summary.reviewed_fact_count) return false;
  if (!versionsAreMonotonic(current.change_summary.artifact_versions, next.change_summary.artifact_versions)) return false;
  if (!artifactHeadsAreMonotonic(current, next)) return false;
  return current.modules.every((module) => {
    const candidate = next.modules.find((item) => item.key === module.key);
    if (!candidate) return false;
    if (recovery && module.state === "blocked") {
      const expectedState = next.preparation.current_step === "model_bundle" ? "preparing" : "not_started";
      return candidate.state === expectedState;
    }
    if (evidenceAdvanced && EVIDENCE_DOWNSTREAM_MODULES.has(module.key) && candidate.state === "preparing") return true;
    return MODULE_RANK[candidate.state] >= MODULE_RANK[module.state] && VALUATION_RANK[candidate.valuation_state] >= VALUATION_RANK[module.valuation_state];
  });
}
