import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  InvestmentResearchApi,
  investmentResearchApi,
  type CompanyResearchCriticalInput,
  type CompanyResearchFrozenRevision,
  type CompanyResearchMarkdownExport,
  type CompanyResearchPublicationPreview,
  type CompanyResearchRun,
  type CompanyResearchWorkspace,
  type ProductProject,
} from "../../data/investmentResearchApi";
import ResearchWorkbenchPage from "./ResearchWorkbenchPage";

const hash = "a".repeat(64);
const uid = (value: number) => `20000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const ids = { project: uid(1), company: uid(2), googl: uid(3), goog: uid(4), preparation: uid(5), draft: uid(6), evidence: uid(7), gaps: uid(8), revision: uid(9) };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function forProject(base: CompanyResearchWorkspace, projectId: string): CompanyResearchWorkspace {
  return { ...base, project_id: projectId, artifacts: base.artifacts.map((item) => ({ ...item, project_id: projectId })) };
}

function project(): ProductProject {
  return {
    schema_version: "underwriting.v1",
    id: ids.project,
    primary_company_id: ids.company,
    target_security_ids: [ids.googl, ids.goog],
    company_identity: { schema_version: "underwriting.v1", object_id: ids.company, identity_version_id: uid(20), canonical_name: "Alphabet Inc." },
    security_identities: [
      { schema_version: "underwriting.v1", object_id: ids.googl, identity_version_id: uid(21), canonical_name: "Alphabet Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
      { schema_version: "underwriting.v1", object_id: ids.goog, identity_version_id: uid(22), canonical_name: "Alphabet Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
    ],
    content_hash: hash,
    created_at: "2026-08-28T00:00:00Z",
  };
}

function companyResearchRun(
  status: CompanyResearchRun["status"] = "completed",
  options: { fullProcess?: boolean; frozen?: boolean; updatedAt?: string } = {},
): CompanyResearchRun {
  const runWorkspace = workspace({ status: "completed", rich: true });
  const preparationByStatus: Record<CompanyResearchRun["status"], Pick<CompanyResearchWorkspace["preparation"], "status" | "current_step" | "progress" | "error">> = {
    queued: { status: "queued", current_step: "evidence_index", progress: 0, error: null },
    collecting_sources: { status: "preparing_sources", current_step: "evidence_index", progress: 10, error: null },
    analyzing_company: { status: "building_model", current_step: "model_bundle", progress: 25, error: null },
    building_forecast: { status: "building_model", current_step: "financial_bridge", progress: 60, error: null },
    generating_report: { status: "building_model", current_step: "memo", progress: 80, error: null },
    completed: options.frozen
      ? { status: "completed", current_step: null, progress: 100, error: null }
      : { status: "ready_to_freeze", current_step: "memo", progress: 95, error: null },
    needs_input: { status: "awaiting_judgment_review", current_step: "judgment_context", progress: 85, error: null },
    failed: {
      status: "recoverable_failure", current_step: "model_bundle", progress: 30,
      error: {
        schema_version: "underwriting.v1", code: "model_provider_failed", failed_step: "model_bundle",
        retryable: true, next_attempt_at: "2026-08-28T00:10:00Z",
      },
    },
  };
  Object.assign(runWorkspace.preparation, preparationByStatus[status]);
  runWorkspace.selected_revision = options.frozen ? ids.revision : null;
  runWorkspace.draft.base_revision_id = options.frozen ? ids.revision : null;
  const driver = runWorkspace.artifacts.find((item) => item.kind === "driver_map") as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "driver_map" }>;
  driver.payload.drivers = [
    driver.payload.drivers[0],
    { ...driver.payload.drivers[0], driver_key: "cloud_growth", output_metric: "cloud_revenue", assumption_rationale: "云业务增速与订单兑现" },
    { ...driver.payload.drivers[0], driver_key: "ai_capex_return", output_metric: "fcff", assumption_rationale: "AI 资本开支回报" },
  ];
  const stageOrder: CompanyResearchRun["stages"][number]["key"][] = ["identity", "sources", "analysis", "forecast", "report"];
  const activeIndex = {
    queued: 0,
    collecting_sources: 1,
    analyzing_company: 2,
    building_forecast: 3,
    generating_report: 4,
    completed: 5,
    needs_input: 4,
    failed: 3,
  }[status];
  const stages = stageOrder.map((key, index) => ({
    schema_version: "underwriting.v1" as const,
    key,
    status: status === "needs_input" && index === 4
      ? "needs_input" as const
      : status === "failed" && index === 3
        ? "failed" as const
        : index < activeIndex
          ? "completed" as const
          : index === activeIndex
            ? "active" as const
            : "pending" as const,
  }));
  const process = [
    ["initialized", "已确认 Alphabet 公司和证券身份。"],
    ["sources_frozen", "监管披露与公司材料已冻结。"],
    ["analysis_ready", "经营模型和关键驱动已建立。"],
    ["forecast_retry", status === "failed" ? "估值服务暂时不可用，已保留经营分析。" : "三种情景预测已完成。"],
    ["report_ready", "AI 研究初稿已生成。"],
  ].map(([code, message], index) => ({
    schema_version: "underwriting.v1" as const,
    code,
    message,
    occurred_at: `2026-08-28T00:0${index}:00Z`,
    retry: code === "forecast_retry" && status === "failed"
      ? { schema_version: "underwriting.v1" as const, retryable: true, next_attempt_at: "2026-08-28T00:10:00Z" }
      : null,
  }));
  return {
    project_id: ids.project,
    company: {
      schema_version: "underwriting.v1", object_id: ids.company, external_key: "ALPHABET:COMPANY", canonical_name: "Alphabet Inc.",
    },
    securities: project().security_identities.map((security) => ({
      schema_version: "underwriting.v1", object_id: security.object_id, external_key: `NASDAQ:${security.symbol}`,
      canonical_name: security.canonical_name, symbol: security.symbol, exchange: security.exchange,
      share_class: security.share_class, trading_currency: security.trading_currency, company_id: ids.company,
    })),
    status,
    progress: runWorkspace.preparation.progress,
    started_at: "2026-08-28T00:00:00Z",
    updated_at: options.updatedAt ?? "2026-08-28T00:05:00Z",
    stages,
    recent_process: options.fullProcess ? process : process.slice(-3),
    workspace: runWorkspace,
    critical_inputs: null,
    selected_revision: options.frozen ? ids.revision : null,
  };
}

function companyResearchRunWithNarrative(): CompanyResearchRun {
  const run = companyResearchRun();
  const memo = run.workspace.artifacts.find((item) => item.kind === "memo");
  const driverMap = run.workspace.artifacts.find((item) => item.kind === "driver_map");
  if (!memo) throw new Error("memo fixture missing");
  if (!driverMap) throw new Error("driver fixture missing");
  driverMap.payload.drivers.unshift({ ...driverMap.payload.drivers[0], driver_key: "decoy_driver", output_metric: "decoy" });
  const artifactCitation = (kind: "business_map" | "driver_map" | "financial_bridge") => `artifact:${kind}:${hash}`;
  memo.payload.narrative = {
    schema_version: "company-research-memo-narrative.v2",
    summary: { text: "Search 现金流仍可支撑 AI 投资。", citations: [artifactCitation("business_map"), artifactCitation("financial_bridge")] },
    business_explanation: { text: "广告与云业务共同形成现金流。", citations: [artifactCitation("business_map"), artifactCitation("driver_map")] },
    driver_explanations: [
      { driver_key: "search_growth", text: "搜索增长由查询量和变现率驱动。", citations: [artifactCitation("driver_map")] },
      { driver_key: "cloud_growth", text: "云增长取决于订单兑现。", citations: [artifactCitation("driver_map")] },
      { driver_key: "ai_capex_return", text: "资本开支回报需要继续验证。", citations: [artifactCitation("driver_map")] },
    ],
    counterevidence: [{ text: "AI 资本开支可能先压低自由现金流。", citations: ["ai_capex_risk"] }],
    gaps: [{ text: "YouTube 利润率仍未单独披露。", citations: ["youtube_margin_gap"] }],
    next_checks: [{ text: "核验 Q3 云订单和 AI 资本开支回报。", citations: ["youtube_margin_gap"] }],
    generator_kind: "authenticated_ai", prompt_version: "company-research.v2", input_hash: hash, output_hash: hash,
    provider: "openai", model: "gpt-5", prompt_hash: hash, provider_model_identifier: "openai/gpt-5",
  };
  return run;
}

function pendingCriticalInput(overrides: Partial<CompanyResearchCriticalInput> = {}): CompanyResearchCriticalInput {
  return {
    key: "fact:fy2025_revenue",
    kind: "source_fact",
    value: "350018",
    value_type: "decimal",
    period: "FY2025",
    unit: "USD million",
    currency: "USD",
    source_ref: {
      fact_key: "fy2025_revenue",
      raw_hash: hash,
      source_locator: "2025 10-K, p. 32",
      source_role: "filing",
      source_url: "https://abc.xyz/investor/10-k",
    },
    provider: null,
    available_at: null,
    coverage: null,
    rationale: null,
    assumption_key: null,
    equation_id: null,
    parent_input_keys: [],
    unknown_reason: null,
    gap_key: null,
    impact: {
      surfaces: ["revenue", "security_value"],
      dependency_paths: [
        ["fact:fy2025_revenue", "surface:revenue"],
        ["fact:fy2025_revenue", "calculation:dcf", "surface:security_value"],
      ],
    },
    decision: "pending",
    replacement: null,
    input_fingerprint: hash,
    ...overrides,
  };
}

function withCriticalInputs(
  run: CompanyResearchRun,
  inputs: CompanyResearchCriticalInput[],
  options: { artifactId?: string; version?: number; contentHash?: string } = {},
): CompanyResearchRun {
  const artifactId = options.artifactId ?? uid(70);
  const version = options.version ?? 1;
  run.critical_inputs = {
    schema_version: "underwriting.v1",
    artifact_id: artifactId,
    version,
    input_hash: hash,
    content_hash: options.contentHash ?? hash,
    inputs,
  };
  run.workspace.change_summary.artifact_versions.critical_inputs = version;
  return run;
}

function runForWorkspace(candidate: CompanyResearchWorkspace, fullProcess = false): CompanyResearchRun {
  const preparation = candidate.preparation;
  const status: CompanyResearchRun["status"] = preparation.status === "queued"
    ? "queued"
    : preparation.status === "preparing_sources"
      ? "collecting_sources"
      : preparation.status === "building_model"
        ? preparation.progress >= 80 ? "generating_report" : preparation.progress >= 60 ? "building_forecast" : "analyzing_company"
        : preparation.status === "awaiting_evidence_review" || preparation.status === "awaiting_judgment_review" || preparation.status === "blocked"
          ? "needs_input"
          : preparation.status === "recoverable_failure"
            ? "failed"
            : "completed";
  const projected = companyResearchRun(status, {
    fullProcess,
    frozen: candidate.selected_revision !== null,
    updatedAt: new Date(Date.UTC(2026, 7, 28, 0, 0, preparation.progress)).toISOString(),
  });
  return {
    ...projected,
    project_id: candidate.project_id,
    company: {
      schema_version: "underwriting.v1",
      object_id: candidate.company.id,
      external_key: candidate.company.external_key,
      canonical_name: candidate.company.canonical_name,
    },
    progress: preparation.progress,
    workspace: candidate,
    selected_revision: candidate.selected_revision,
  };
}

function source(factKey = "revenue_2025") {
  return { kind: "external", fact_key: factKey, source_role: "filing", source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash } as const;
}

function lineageSource(factKey = "revenue_2025") {
  const { kind: _kind, ...ref } = source(factKey);
  return ref;
}

function observation(key: string, value: string, state: "reported" | "derived" | "assumption" = "reported") {
  return {
    key, value, unit: key.includes("return") || key.includes("rate") ? "ratio" : "USD million", currency: key.includes("return") || key.includes("rate") ? "N/A" : "USD",
    period: "FY2025 / cutoff 2026-02-05", state,
    source_ref: state === "reported" ? source(key) : state === "derived" ? { kind: "artifact_computation" as const, artifact_refs: [], market_snapshot_ids: [], equation_id: "dcf.v1" } : null,
    gap_key: null, assumption_key: state === "assumption" ? `assumption_${key}` : null,
  };
}

function artifact(kind: string, payload: object, version = 1) {
  const offset = ["evidence_index", "research_gaps", "business_map", "driver_map", "financial_bridge", "scenario_set", "valuation_set", "judgment_context", "memo"].indexOf(kind);
  return { schema_version: "underwriting.v1", id: uid(100 + version * 10 + offset), project_id: ids.project, kind, version, input_hash: hash, content_hash: hash, source_refs: [{ source_role: "filing", source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash }], payload };
}

function registryRef(item: ReturnType<typeof artifact>) {
  return { id: item.id, kind: item.kind as CompanyResearchWorkspace["modules"][number]["artifact_refs"][number]["kind"], content_hash: item.content_hash };
}

function parentRef(item: ReturnType<typeof artifact>) {
  return { artifact_id: item.id, artifact_kind: item.kind, content_hash: item.content_hash };
}

function lineage(parents: ReturnType<typeof artifact>[], marketSnapshotIds: string[] = [], marketSnapshotBindings: object[] = []) {
  return { artifact_refs: parents.map(parentRef), market_snapshot_ids: marketSnapshotIds, market_snapshot_bindings: marketSnapshotBindings };
}

function computedObservation(key: string, value: string, parents: ReturnType<typeof artifact>[], marketSnapshotIds: string[]) {
  return {
    key, value, unit: key.includes("return") || key.includes("rate") || key === "residual" ? "ratio" : "USD million",
    currency: key.includes("return") || key.includes("rate") || key === "residual" ? "N/A" : "USD",
    period: "FY2025 / cutoff 2026-02-05", state: "derived" as const,
    source_ref: { kind: "artifact_computation" as const, artifact_refs: parents.map(parentRef), market_snapshot_ids: marketSnapshotIds, equation_id: "dcf.v1" },
    gap_key: null, assumption_key: null,
  };
}

function evidenceFact(reviewDecision?: "confirmed" | "rejected") {
  return {
    fact_key: "revenue_2025", company_external_key: "ALPHABET:COMPANY", business_module: "google_services", metric_key: "revenue",
    observation: observation("revenue_2025", "350018"), period_start: "2025-01-01", period_end: "2025-12-31",
    published_at: "2026-02-04T21:00:00Z", available_at: "2026-02-05T00:00:00Z", source_role: "filing",
    source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash,
    ...(reviewDecision ? { review_decision: reviewDecision } : {}),
  };
}

type PreparationStep = NonNullable<CompanyResearchWorkspace["preparation"]["error"]>["failed_step"];

function workspace(options: {
  status?: CompanyResearchWorkspace["preparation"]["status"];
  rich?: boolean;
  factDecision?: "confirmed" | "rejected";
  evidenceVersion?: number;
  currentStep?: PreparationStep;
} = {}): CompanyResearchWorkspace {
  const status = options.status ?? "awaiting_evidence_review";
  const impliedDecision = options.factDecision ?? (options.rich || ((status === "building_model" || status === "recoverable_failure") && (options.currentStep ?? "model_bundle") === "model_bundle") ? "confirmed" : undefined);
  const evidence = artifact("evidence_index", {
    fixture_content_hash: hash, cutoff: "2026-02-05T00:00:00Z", company_external_key: "ALPHABET:COMPANY",
    security_external_keys: ["NASDAQ:GOOGL", "NASDAQ:GOOG"], facts: [evidenceFact(impliedDecision)],
  }, options.evidenceVersion ?? 1);
  const gaps = artifact("research_gaps", {
    fixture_content_hash: hash, company_external_key: "ALPHABET:COMPANY",
    gaps: [{ gap_key: "youtube_margin_gap", business_module: "youtube", reason: "YouTube 分部利润率未单独披露" }],
  });
  const artifacts: ReturnType<typeof artifact>[] = [evidence, gaps];
  if (options.rich) {
    const richFacts = (evidence.payload as { facts: ReturnType<typeof evidenceFact>[] }).facts;
    richFacts[0] = evidenceFact(impliedDecision ?? "confirmed");
    richFacts.push({ ...evidenceFact("confirmed"), fact_key: "ai_capex_risk", observation: observation("ai_capex_risk", "1") });
    const business = artifact("business_map", { modules: [{ module_key: "google_services", revenue_sources: ["Search", "YouTube"], cost_structure: ["TAC", "基础设施"], capital_needs: ["AI 数据中心"], fact_refs: [lineageSource()], gap_refs: ["youtube_margin_gap"], classified_evidence: [] }], _lineage: lineage([evidence]) });
    const driver = artifact("driver_map", { drivers: [{ driver_key: "search_growth", module_key: "google_services", fact_refs: [lineageSource()], assumption_refs: [], equation: "revenue × growth", output_metric: "revenue", equation_id: "driver.v1", values: [observation("search_growth", "0.11", "assumption")], assumption_rationale: "查询量与变现率", assumption_equation: "volume × monetization" }], _lineage: lineage([business]) });
    const financial = artifact("financial_bridge", {
      rows: Array.from({ length: 5 }, (_, index) => ({
        period: `FY${2025 + index}`,
        revenue: index === 0 ? observation("revenue_2025", "350018") : observation(`revenue_${2025 + index}`, String(350018 + index * 20000), "assumption"),
        operating_income: observation(`operating_income_${2025 + index}`, String(112390 + index * 8000), "assumption"),
        cash_tax_rate: observation(`cash_tax_rate_${2025 + index}`, "0.17", "assumption"),
        depreciation: observation(`depreciation_${2025 + index}`, "21400", "assumption"), capex: observation(`capex_${2025 + index}`, "52500", "assumption"),
        working_capital_change: observation(`working_capital_change_${2025 + index}`, "2200", "assumption"), fcff: observation(`fcff_${2025 + index}`, "79100", "assumption"),
        fact_refs: [lineageSource()], assumption_refs: [],
      })), _lineage: lineage([driver]),
    });
    const scenario = artifact("scenario_set", { scenarios: ["base", "bull", "bear"].map((scenarioId, index) => ({ scenario_id: scenarioId, mechanism_id: `${scenarioId}_search_ai`, driver_overrides: [{ driver_key: "fcff_multiplier", observation: { ...observation("fcff_multiplier", String([1, 1.2, 0.75][index]), "assumption"), unit: "multiplier", currency: "N/A" }, rationale: `${scenarioId} case mechanism`, equation: "baseline × multiplier" }] })), _lineage: lineage([driver]) });
    const marketIds = [uid(30), uid(31), uid(32), uid(33)];
    const marketBindings = (["price", "fx", "capital_structure", "security_rights"] as const).map((snapshotKind, index) => ({
      snapshot_id: marketIds[index], snapshot_kind: snapshotKind, snapshot_content_hash: hash,
      security_external_key: snapshotKind === "fx" || snapshotKind === "capital_structure" ? null : "NASDAQ:GOOGL",
      source_ref: lineageSource(), capture_envelope_id: uid(40 + index), capture_content_hash: hash,
      provenance_role: "primary", provider_policy_version: "fixture.v1",
      raw_components: [{ raw_file: `${snapshotKind}.json`, raw_hash: hash, source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32" }],
    }));
    const valuationParents = [scenario, financial];
    const derived = (key: string, value: string) => computedObservation(key, value, valuationParents, marketIds);
    const range = (prefix: string, min: string, max: string) => ({ minimum: derived(`${prefix}_min`, min), maximum: derived(`${prefix}_max`, max) });
    const valuation = artifact("valuation_set", {
      scenario_dcf_values: ["base", "bull", "bear"].map((scenarioId, index) => ({ scenario_id: scenarioId, enterprise_value: derived(`${scenarioId}_dcf`, String([2400000, 2900000, 1700000][index])) })),
      reverse_dcf: { driver_key: "fcff_multiplier", implied_value: derived("implied_fcff_multiplier", "1.08"), achieved_residual: derived("residual", "0.0001"), iteration_count: derived("iterations", "7") },
      security_value_ranges: [
        { security_external_key: "NASDAQ:GOOGL", value_per_share: range("googl", "165", "225"), value_currency: "USD", base_currency_return: range("googl_return", "0.04", "0.41") },
        { security_external_key: "NASDAQ:GOOG", value_per_share: range("goog", "166", "227"), value_currency: "USD", base_currency_return: range("goog_return", "0.03", "0.4") },
      ],
      required_return: observation("required_return", "0.12", "assumption"),
      required_return_comparisons: [
        { security_external_key: "NASDAQ:GOOGL", required_return: observation("googl_required_return", "0.12", "assumption"), achieved_return_range: range("googl_return", "0.04", "0.41"), meets_required_return: true },
        { security_external_key: "NASDAQ:GOOG", required_return: observation("goog_required_return", "0.12", "assumption"), achieved_return_range: range("goog_return", "0.03", "0.4"), meets_required_return: true },
      ], sensitivity_analyses: [], _lineage: lineage(valuationParents, marketIds, marketBindings),
    });
    const counterevidence = lineageSource("ai_capex_risk");
    const judgment = artifact("judgment_context", { operating_baseline_available: true, financial_bridge_closed: true, market_security_bridge_available: true, strongest_counterevidence: [counterevidence], next_verification_events: ["Q3 Cloud backlog 与 AI capex 回报验证"], _lineage: lineage([evidence, business, driver, financial, scenario, valuation, gaps]) });
    const memoRef = (item: ReturnType<typeof artifact>) => ({ artifact_kind: item.kind, content_hash: item.content_hash });
    const memo = artifact("memo", { assessment_status: "answerable", business_map_ref: memoRef(business), driver_map_ref: memoRef(driver), financial_bridge_ref: memoRef(financial), scenario_set_ref: memoRef(scenario), valuation_set_ref: memoRef(valuation), gap_keys: ["youtube_margin_gap"], strongest_counterevidence: [counterevidence], next_verification_events: ["Q3 Cloud backlog 与 AI capex 回报验证"], candidate_status: "machine_draft", _lineage: lineage([judgment]) });
    artifacts.push(business, driver, financial, scenario, valuation, judgment, memo);
  }
  const currentStep = options.currentStep ?? (status === "completed" ? null : status === "recoverable_failure" || status === "building_model" || status === "blocked" ? "model_bundle" : status === "queued" || status === "preparing_sources" ? "evidence_index" : status === "awaiting_judgment_review" ? "judgment_context" : status === "ready_to_freeze" ? "memo" : "research_gaps");
  const progress = options.rich || status === "completed" ? 100 : status === "queued" ? 0 : status === "preparing_sources" ? 10 : status === "awaiting_judgment_review" ? 85 : status === "ready_to_freeze" ? 95 : 25;
  const defaultModuleState = status === "recoverable_failure" || status === "blocked" ? "blocked" : status === "queued" ? "not_started" : "preparing";
  return {
    schema_version: "underwriting.v1", project_id: ids.project,
    company: { schema_version: "underwriting.v1", object_id: ids.company, id: ids.company, external_key: "ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    preparation: {
      schema_version: "underwriting.v1", id: ids.preparation, status,
      current_step: currentStep,
      progress,
      error: status === "recoverable_failure" || status === "blocked" ? { schema_version: "underwriting.v1", code: "model_temporarily_unavailable", failed_step: currentStep!, retryable: status === "recoverable_failure", next_attempt_at: status === "recoverable_failure" ? "2026-08-28T01:00:00Z" : null } : null,
    },
    artifacts: artifacts as CompanyResearchWorkspace["artifacts"],
    modules: ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"].map((key) => {
      const refsByModule: Record<string, string[]> = {
        overview: ["judgment_context"], business_map: ["business_map"], operating_drivers: ["driver_map"], evidence_and_gaps: ["evidence_index", "research_gaps"],
        industry_competition_regulation: ["business_map"], financials_cash_flow_capital_allocation: ["financial_bridge"],
        scenarios_valuation_implied_expectations: ["scenario_set", "valuation_set"], counterevidence_risks_next_checks: ["research_gaps", "judgment_context"], versions_changes_memo: ["memo"],
      };
      const evidenceReady = key === "evidence_and_gaps" && impliedDecision !== undefined && (status !== "queued" || currentStep !== "evidence_index");
      const state = options.rich ? "ready" : key === "evidence_and_gaps" && status === "awaiting_evidence_review" ? "needs_review" : evidenceReady ? "ready" : defaultModuleState;
      const artifactRefs = (options.rich || key === "evidence_and_gaps" && (status === "awaiting_evidence_review" || evidenceReady))
        ? refsByModule[key].map((kind) => registryRef(artifacts.find((item) => item.kind === kind)!)) : [];
      return { schema_version: "underwriting.v1", key, state, artifact_refs: artifactRefs, valuation_state: key === "scenarios_valuation_implied_expectations" ? options.rich ? "ready" : "pending" : "not_applicable" };
    }) as CompanyResearchWorkspace["modules"],
    source_count: 1, gap_count: 1,
    draft: { schema_version: "underwriting.v1", id: ids.draft, lock_version: impliedDecision ? 2 : 1, base_revision_id: status === "completed" ? ids.revision : null }, selected_revision: status === "completed" ? ids.revision : null,
    change_summary: { artifact_versions: Object.fromEntries(artifacts.map((item) => [item.kind, item.version])), reviewed_fact_count: options.rich ? 2 : impliedDecision ? 1 : 0 },
  };
}

function publicationWorkspace(stage: 85 | 95 | 100): CompanyResearchWorkspace {
  const candidate = assessmentFixture("not_answerable");
  candidate.artifacts = candidate.artifacts.filter((item) => item.kind !== "valuation_set");
  delete candidate.change_summary.artifact_versions.valuation_set;
  candidate.modules = candidate.modules.map((module) => module.key === "scenarios_valuation_implied_expectations"
    ? { ...module, valuation_state: "blocked", artifact_refs: module.artifact_refs.filter((ref) => ref.kind !== "valuation_set") }
    : module);
  const judgment = candidate.artifacts.find((item) => item.kind === "judgment_context")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "judgment_context" }>;
  judgment.payload._lineage.artifact_refs = judgment.payload._lineage.artifact_refs.filter((ref) => ref.artifact_kind !== "valuation_set");
  Object.assign(candidate.preparation, stage === 85
    ? { status: "awaiting_judgment_review", current_step: "judgment_context", progress: 85 }
    : stage === 95
      ? { status: "ready_to_freeze", current_step: "memo", progress: 95 }
      : { status: "completed", current_step: null, progress: 100 });
  candidate.selected_revision = stage === 100 ? ids.revision : null;
  candidate.draft.lock_version = stage === 85 ? 2 : stage === 95 ? 3 : 4;
  candidate.draft.base_revision_id = stage === 100 ? ids.revision : null;
  const memo = candidate.artifacts.find((item) => item.kind === "memo")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "memo" }>;
  memo.payload.valuation_set_ref = null;
  if (stage >= 95) {
    memo.id = uid(129);
    memo.version = 2;
    memo.payload = {
      ...memo.payload,
      candidate_status: "human_confirmed",
      reviewer: "human:local-user",
      markdown: "当前正式证据不足，不能形成投资方向、置信度、目标价或预期回报。",
    };
    candidate.change_summary.artifact_versions.memo = 2;
  }
  return candidate;
}

function publicationPreview(): CompanyResearchPublicationPreview {
  const sourceRef = lineageSource("ai_capex_risk");
  return {
    schema_version: "underwriting.v1",
    project_id: ids.project,
    expected_lock_version: 3,
    company: { schema_version: "underwriting.v1", object_id: ids.company, external_key: "ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    securities: project().security_identities.map((security) => ({
      schema_version: "underwriting.v1", object_id: security.object_id,
      external_key: `NASDAQ:${security.symbol}`, canonical_name: security.canonical_name,
      symbol: security.symbol, exchange: security.exchange, share_class: security.share_class,
      trading_currency: security.trading_currency, company_id: ids.company,
    })),
    cutoff_at: "2026-02-05T00:00:00Z",
    historical_basis_id: uid(50), historical_basis_content_hash: hash,
    strategy_version: "company-research-mainline.v1", model_version: "company-research-model.v1",
    assessment: { schema_version: "underwriting.v1", answerability: "not_answerable", direction: null, confidence: null, content_hash: hash },
    value_range: null, return_range: null, blockers: Array.from({ length: 31 }, (_, index) => `research_gap_${String(index + 1).padStart(2, "0")}`),
    strongest_counterevidence: [sourceRef], next_verification_events: ["Q3 Cloud backlog 与 AI capex 回报验证"],
    memo_markdown: "当前正式证据不足，不能形成投资方向、置信度、目标价或预期回报。",
    artifacts: publicationWorkspace(95).artifacts.filter((item) => item.kind !== "valuation_set").map((item) => ({
      schema_version: "underwriting.v1", kind: item.kind, id: item.id, version: item.version,
      input_hash: item.input_hash, content_hash: item.content_hash,
    })),
    manifest_hash: hash,
  };
}

function frozenRevision(): CompanyResearchFrozenRevision {
  const preview = publicationPreview();
  const { expected_lock_version: _expectedLockVersion, ...projection } = preview;
  return {
    ...projection,
    id: ids.revision,
    sequence: 1,
    published_at: "2026-08-30T02:03:04Z",
    boundary_id: uid(51), manifest_id: uid(52),
    preparation_status: "completed", current_step: null, progress: 100,
  };
}

function frozenRevisionFor(candidate: CompanyResearchWorkspace): CompanyResearchFrozenRevision {
  const memo = candidate.artifacts.find((item) => item.kind === "memo") as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "memo" }> | undefined;
  return {
    ...frozenRevision(),
    assessment: {
      schema_version: "underwriting.v1",
      answerability: memo?.payload.assessment_status ?? "not_answerable",
      direction: null,
      confidence: null,
      content_hash: hash,
    },
    memo_markdown: memo?.payload.candidate_status === "human_confirmed" ? memo.payload.markdown : "冻结研究备忘录\n\n保留换行。",
    artifacts: candidate.artifacts.map((item) => ({
      schema_version: "underwriting.v1", kind: item.kind, id: item.id, version: item.version,
      input_hash: item.input_hash, content_hash: item.content_hash,
    })),
  };
}

function criticalDescriptor(run: CompanyResearchRun) {
  if (run.critical_inputs === null) throw new Error("critical input fixture missing");
  return {
    schema_version: "underwriting.v1" as const,
    kind: "critical_inputs" as const,
    id: run.critical_inputs.artifact_id,
    version: run.critical_inputs.version,
    input_hash: run.critical_inputs.input_hash,
    content_hash: run.critical_inputs.content_hash,
  };
}

function publicationPreviewForRun(run: CompanyResearchRun): CompanyResearchPublicationPreview {
  const preview = publicationPreview();
  return {
    ...preview,
    artifacts: [...preview.artifacts, criticalDescriptor(run)],
  };
}

function frozenRevisionForRun(run: CompanyResearchRun): CompanyResearchFrozenRevision {
  const revision = frozenRevisionFor(run.workspace);
  return {
    ...revision,
    artifacts: [...revision.artifacts, criticalDescriptor(run)],
  };
}

function markdownExport(): CompanyResearchMarkdownExport {
  return {
    schema_version: "underwriting.v1",
    filename: `alphabet-company-research-${ids.revision}.md`,
    media_type: "text/markdown",
    content: `# Alphabet\n\nRevision: ${ids.revision}`,
    content_hash: hash,
  };
}

function renderPage() {
  return render(<MemoryRouter initialEntries={[`/research/projects/${ids.project}`]}><Routes><Route path="/research/projects/:projectId" element={<ResearchWorkbenchPage />} /></Routes></MemoryRouter>);
}

function mockFrozenRevision(candidate: CompanyResearchWorkspace) {
  return vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozenRevisionFor(candidate));
}

async function bindFrozenWorkspace(_user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("冻结版本已自动验证并载入。");
}

async function decodeWorkspaceFixture(candidate: CompanyResearchWorkspace) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(candidate), {
    status: 200, headers: { "content-type": "application/json" },
  })));
  try {
    return await new InvestmentResearchApi().companyResearchWorkspace(ids.project);
  } finally {
    vi.unstubAllGlobals();
  }
}

function assessmentFixture(status: "not_answerable" | "partially_answerable" | "answerable") {
  const candidate = workspace({ status: "completed", rich: true });
  const judgment = candidate.artifacts.find((item) => item.kind === "judgment_context")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "judgment_context" }>;
  judgment.payload.operating_baseline_available = status === "answerable";
  judgment.payload.financial_bridge_closed = status === "answerable";
  judgment.payload.market_security_bridge_available = status === "answerable";
  const memo = candidate.artifacts.find((item) => item.kind === "memo")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "memo" }>;
  memo.payload.assessment_status = status;
  memo.payload.gap_keys = status === "not_answerable" ? ["youtube_margin_gap"] : [];
  candidate.gap_count = memo.payload.gap_keys.length;
  return candidate;
}

function answerableWithoutValuationFixture() {
  const candidate = assessmentFixture("answerable");
  candidate.artifacts = candidate.artifacts.filter((item) => item.kind !== "valuation_set");
  delete candidate.change_summary.artifact_versions.valuation_set;
  const scenario = candidate.artifacts.find((item) => item.kind === "scenario_set")!;
  candidate.modules = candidate.modules.map((module) => module.key === "scenarios_valuation_implied_expectations"
    ? { ...module, state: "ready", valuation_state: "blocked", artifact_refs: [registryRef(scenario)] } : module);
  const judgment = candidate.artifacts.find((item) => item.kind === "judgment_context")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "judgment_context" }>;
  judgment.payload._lineage.artifact_refs = judgment.payload._lineage.artifact_refs.filter((ref) => ref.artifact_kind !== "valuation_set");
  const memo = candidate.artifacts.find((item) => item.kind === "memo")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "memo" }>;
  memo.payload.valuation_set_ref = null;
  return candidate;
}

const RETRY_FIXTURE_CASES = [
  ["evidence_index", "queued", 0], ["business_map", "queued", 0], ["driver_map", "queued", 0],
  ["financial_bridge", "queued", 0], ["scenario_set", "queued", 0], ["valuation_set", "queued", 0],
  ["research_gaps", "queued", 0], ["judgment_context", "queued", 0], ["memo", "queued", 0],
  ["model_bundle", "building_model", 25],
] as const;

function retryWorkspaceFixture(step: typeof RETRY_FIXTURE_CASES[number][0], resumed: boolean) {
  const status = resumed ? RETRY_FIXTURE_CASES.find(([candidate]) => candidate === step)![1] : "recoverable_failure";
  return workspace({ status, currentStep: step, factDecision: step === "evidence_index" ? undefined : "confirmed" });
}

function activeReviewWorkspace(): CompanyResearchWorkspace {
  const candidate = workspace();
  Object.assign(candidate.preparation, { status: "building_model", current_step: "model_bundle", progress: 25, error: null });
  return candidate;
}

describe("Alphabet company research workbench", () => {
  beforeEach(() => {
    Object.defineProperties(HTMLDialogElement.prototype, {
      showModal: { configurable: true, value(this: HTMLDialogElement) { this.setAttribute("open", ""); } },
      close: { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute("open"); } },
    });
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockImplementation(async (projectId, includeProcess = false) => (
      runForWorkspace(await investmentResearchApi.companyResearchWorkspace(projectId), includeProcess)
    ));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("presents the authenticated company result before the always-visible recent process", async () => {
    const run = companyResearchRun();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(run.workspace);
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(run);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Alphabet Inc." })).toBeVisible();
    const result = screen.getByRole("region", { name: "公司研究结果" });
    const process = screen.getByRole("region", { name: "研究过程" });
    expect(result.compareDocumentPosition(process) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const heading of [
      "当前判断", "公司如何赚钱", "三个最重要的经营驱动", "情景与价值范围", "最强反证",
      "关键缺口", "下一验证事件", "来源", "版本",
    ]) expect(within(result).getByRole("heading", { name: heading })).toBeVisible();
    for (const scenario of ["Base", "Bull", "Bear"]) expect(within(result).getByText(scenario)).toBeVisible();
    for (const driver of ["search_growth", "cloud_growth", "ai_capex_return"]) expect(within(result).getByText(driver)).toBeVisible();
    expect(within(result).getAllByText(/fcff_multiplier/)).toHaveLength(3);
    expect(within(process).getAllByRole("listitem")).toHaveLength(3);
    const rail = screen.getByRole("region", { name: "研究阶段" });
    for (const stage of ["公司识别", "资料收集", "经营分析", "建立预测", "生成报告"]) expect(within(rail).getByText(stage)).toBeVisible();
    expect(screen.getByRole("button", { name: "查看来源 revenue_2025" })).toHaveAttribute("aria-expanded", "false");
    const basis = screen.getByRole("group", { name: "研究依据" });
    expect(within(basis).getAllByRole("button", { name: /未开始|准备中|待审核|可查看|已阻塞/ })).toHaveLength(9);
    expect(screen.queryByRole("navigation", { name: "研究模块" })).not.toBeInTheDocument();
  });

  it("expands narrative conclusions to their cited authenticated source facts", async () => {
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(companyResearchRunWithNarrative());
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Search 现金流仍可支撑 AI 投资。")).toBeVisible();
    expect(screen.queryByText("decoy_driver")).not.toBeInTheDocument();
    await user.click(screen.getByText("查看当前判断来源（2）"));
    const citation = screen.getByRole("group", { name: "当前判断来源" });
    expect(within(citation).getByRole("button", { name: /打开研究依据 business_map/ })).toBeVisible();
    await user.click(within(citation).getByRole("button", { name: /打开研究依据 business_map/ }));
    expect(screen.getByRole("button", { name: /Google 如何赚钱/ })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByText("查看最强反证来源（1）"));
    expect(within(screen.getByRole("group", { name: "最强反证来源" })).getByRole("link", { name: /ai_capex_risk/ })).toHaveAttribute("href", "https://abc.xyz/investor/10-k");
    await user.click(screen.getByText("查看关键缺口来源（1）"));
    expect(screen.getByRole("group", { name: "关键缺口来源" })).toHaveTextContent("YouTube 分部利润率未单独披露");
  });

  it.each([
    ["collecting_sources", false, "收集资料"],
    ["analyzing_company", false, "分析公司"],
    ["building_forecast", false, "建立预测"],
    ["generating_report", false, "生成报告"],
    ["failed", false, "可恢复失败"],
    ["needs_input", false, "需要补充"],
    ["completed", false, "AI 初稿已完成"],
    ["completed", true, "已冻结版本"],
  ] as const)("renders the %s product state without hiding completed result content", async (status, frozen, label) => {
    const run = companyResearchRun(status, { frozen });
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(run);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(run.workspace);
    if (frozen) mockFrozenRevision(run.workspace);
    renderPage();

    await screen.findByRole("heading", { name: "Alphabet Inc." });
    if (frozen) await screen.findByText(label, { selector: ".ir-run-status strong" });
    else expect(document.querySelector(".ir-run-status")).toHaveTextContent(label);
    expect(screen.getByRole("region", { name: "公司研究结果" })).toHaveTextContent("公司如何赚钱");
    if (status === "failed") {
      expect(screen.getByRole("button", { name: "重试建立预测" })).toBeEnabled();
      expect(screen.getByText("估值服务暂时不可用，已保留经营分析。")).toBeVisible();
    }
  });

  it("loads only three recent process entries by default, expands the bounded history, and retains focus across polling", async () => {
    vi.useFakeTimers();
    const initial = companyResearchRun("analyzing_company", { updatedAt: "2026-08-28T00:05:00Z" });
    const successor = companyResearchRun("building_forecast", { updatedAt: "2026-08-28T00:06:00Z" });
    const full = companyResearchRun("building_forecast", { fullProcess: true, updatedAt: "2026-08-28T00:06:00Z" });
    const fullRequest = deferred<CompanyResearchRun>();
    const runRead = vi.spyOn(investmentResearchApi, "companyResearchRun")
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(successor)
      .mockImplementationOnce(() => fullRequest.promise)
      .mockResolvedValue(successor);
    const projectRead = vi.spyOn(investmentResearchApi, "project");
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace");
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    const sourceDisclosure = screen.getByRole("button", { name: "查看来源 revenue_2025" });
    sourceDisclosure.focus();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(runRead).toHaveBeenNthCalledWith(1, ids.project);
    expect(runRead).toHaveBeenNthCalledWith(2, ids.project);
    expect(sourceDisclosure).toHaveFocus();
    expect(projectRead).not.toHaveBeenCalled();
    expect(workspaceRead).not.toHaveBeenCalled();

    const processDisclosure = screen.getByRole("button", { name: "查看完整过程" });
    expect(processDisclosure).toHaveAttribute("aria-controls", "company-research-process-history");
    fireEvent.click(processDisclosure);
    fireEvent.click(screen.getByRole("button", { name: "正在读取过程…" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(runRead).toHaveBeenNthCalledWith(3, ids.project, true);
    expect(runRead).toHaveBeenCalledTimes(3);
    await act(async () => { fullRequest.resolve(full); await Promise.resolve(); });
    expect(screen.getByRole("region", { name: "研究过程" }).querySelectorAll("li")).toHaveLength(5);
  });

  it.each([
    ["awaiting evidence review", () => workspace()],
    ["rich answerable", () => workspace({ status: "completed", rich: true })],
    ["sync-pending reviewed successor", () => workspace({ status: "building_model", factDecision: "confirmed", evidenceVersion: 2 })],
    ["recoverable retry", () => workspace({ status: "recoverable_failure" })],
    ["explicit not-answerable", () => assessmentFixture("not_answerable")],
    ["partial answerability", () => assessmentFixture("partially_answerable")],
    ["answerable without valuation", () => answerableWithoutValuationFixture()],
  ] as const)("keeps the %s fixture valid under the production decoder", async (_label, fixture) => {
    await expect(decodeWorkspaceFixture(fixture())).resolves.toMatchObject({ project_id: ids.project });
  });

  it.each(RETRY_FIXTURE_CASES)("decodes the failed and resumed %s retry fixtures", async (step, resumedStatus, progress) => {
    const failed = await decodeWorkspaceFixture(retryWorkspaceFixture(step, false));
    expect(failed.preparation).toMatchObject({ status: "recoverable_failure", current_step: step });
    const resumed = await decodeWorkspaceFixture(retryWorkspaceFixture(step, true));
    expect(resumed.preparation).toMatchObject({ status: resumedStatus, current_step: step, progress });
  });

  it("decodes the closed frozen artifact set by strategy without weakening legacy revisions", async () => {
    const terminalRun = withCriticalInputs(runForWorkspace(publicationWorkspace(95)), [
      { ...pendingCriticalInput(), decision: "confirmed" },
    ], { artifactId: uid(71), version: 2, contentHash: "b".repeat(64) });
    const mainline = publicationPreviewForRun(terminalRun) as unknown as Record<string, unknown>;
    const missingCritical = structuredClone(mainline) as { artifacts: Array<{ kind: string }> };
    missingCritical.artifacts = missingCritical.artifacts.filter((item) => item.kind !== "critical_inputs");
    const legacy = { ...structuredClone(missingCritical), strategy_version: "company-research-default.v1" };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(mainline), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(missingCritical), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(legacy), { status: 200, headers: { "content-type": "application/json" } })));
    const api = new InvestmentResearchApi();

    await expect(api.previewCompanyResearchPublication(ids.project, {
      schema_version: "underwriting.v1", expected_lock_version: 3,
    })).resolves.toMatchObject({ artifacts: expect.arrayContaining([expect.objectContaining({ kind: "critical_inputs" })]) });
    await expect(api.previewCompanyResearchPublication(ids.project, {
      schema_version: "underwriting.v1", expected_lock_version: 3,
    })).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.previewCompanyResearchPublication(ids.project, {
      schema_version: "underwriting.v1", expected_lock_version: 3,
    })).resolves.toMatchObject({ strategy_version: "company-research-default.v1" });
  });

  it("continues from the last critical input through preview, publish, replay, and verified Markdown export", async () => {
    const selected = pendingCriticalInput();
    const initial = withCriticalInputs(runForWorkspace(publicationWorkspace(85)), [selected]);
    const successorInput = { ...selected, decision: "confirmed" as const };
    const successor = withCriticalInputs(runForWorkspace(publicationWorkspace(95)), [successorInput], {
      artifactId: uid(71), version: 2, contentHash: "b".repeat(64),
    });
    const completed = withCriticalInputs(runForWorkspace(publicationWorkspace(100)), [successorInput], {
      artifactId: uid(71), version: 2, contentHash: "b".repeat(64),
    });
    const previewResponse = publicationPreviewForRun(successor);
    const frozen = frozenRevisionForRun(completed);
    vi.spyOn(investmentResearchApi, "companyResearchRun")
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(completed);
    const decide = vi.spyOn(investmentResearchApi, "decideCompanyResearchCriticalInput").mockResolvedValue(successor);
    const confirmJudgment = vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment");
    const previewRequest = vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(previewResponse);
    const publish = vi.spyOn(investmentResearchApi, "publishCompanyResearch").mockResolvedValue(frozen);
    const replay = vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozen);
    const exported = vi.spyOn(investmentResearchApi, "exportCompanyResearchRevision").mockResolvedValue(markdownExport());
    const createObjectURL = vi.fn((_blob: Blob) => "blob:critical-input-export");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderPage();

    const save = await screen.findByRole("button", { name: "保存研究版本" });
    await user.click(save);
    const drawer = await screen.findByRole("dialog", { name: "确认关键输入" });
    expect(drawer).toHaveTextContent("已处理 0 / 1，剩余 1");
    await user.click(within(drawer).getByRole("radio", { name: "确认当前输入" }));
    await user.click(within(drawer).getByRole("button", { name: "提交本项决定" }));

    await waitFor(() => expect(decide).toHaveBeenCalledWith(ids.project, {
      schema_version: "underwriting.v1",
      critical_input_key: selected.key,
      expected_artifact_id: uid(70),
      expected_input_fingerprint: selected.input_fingerprint,
      decision: "confirmed",
    }));
    expect(screen.queryByRole("dialog", { name: "确认关键输入" })).not.toBeInTheDocument();
    const preview = await screen.findByRole("button", { name: "预览冻结版本" });
    await waitFor(() => expect(preview).toHaveFocus());
    expect(confirmJudgment).not.toHaveBeenCalled();

    await user.click(preview);
    expect(previewRequest).toHaveBeenCalledWith(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 3 });
    const dialog = await screen.findByRole("dialog", { name: "确认冻结版本" });
    expect(within(dialog).getByRole("list", { name: "冻结制品清单" })).toHaveTextContent(`critical_inputs ${uid(71)} v2`);
    await user.click(within(dialog).getByRole("button", { name: "冻结并发布" }));
    expect(await screen.findByRole("status")).toHaveTextContent("冻结版本已发布");
    expect(publish).toHaveBeenCalledTimes(1);
    expect(replay).toHaveBeenCalledWith(ids.project, ids.revision);

    await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
    await waitFor(() => expect(exported).toHaveBeenCalledWith(ids.project, ids.revision));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:critical-input-export");
  });

  it("replaces a stale drawer with the fresh run, focuses the changed input, and never retries the old fingerprint", async () => {
    const initialInput = pendingCriticalInput();
    const changedInput = pendingCriticalInput({ value: "351000", input_fingerprint: "b".repeat(64) });
    const initial = withCriticalInputs(runForWorkspace(publicationWorkspace(85)), [initialInput]);
    const fresh = withCriticalInputs(runForWorkspace(publicationWorkspace(85)), [changedInput], {
      artifactId: uid(71), version: 2, contentHash: "b".repeat(64),
    });
    const runRead = vi.spyOn(investmentResearchApi, "companyResearchRun")
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(fresh);
    const decide = vi.spyOn(investmentResearchApi, "decideCompanyResearchCriticalInput")
      .mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "保存研究版本" }));
    const staleDrawer = await screen.findByRole("dialog", { name: "确认关键输入" });
    await user.click(within(staleDrawer).getByRole("radio", { name: "确认当前输入" }));
    await user.click(within(staleDrawer).getByRole("button", { name: "提交本项决定" }));

    const freshDrawer = await screen.findByRole("dialog", { name: "确认关键输入" });
    await waitFor(() => expect(freshDrawer).not.toBe(staleDrawer));
    expect(freshDrawer).toHaveTextContent("351000");
    const changedHeading = within(freshDrawer).getByRole("heading", { name: "fact:fy2025_revenue" });
    await waitFor(() => expect(changedHeading).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent("关键输入已更新，请基于新值重新确认");
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide.mock.calls[0][1]).toMatchObject({ expected_input_fingerprint: hash });
    expect(runRead).toHaveBeenCalledTimes(2);
  });

  it("accepts the governed rebuild run after replacing one critical source with a user assumption", async () => {
    const selected = pendingCriticalInput();
    const initial = withCriticalInputs(runForWorkspace(publicationWorkspace(85)), [selected]);
    const replacement = {
      key: selected.key,
      kind: "user_assumption" as const,
      value: "360000",
      value_type: "decimal" as const,
      period: selected.period,
      unit: selected.unit,
      currency: selected.currency,
      source_ref: null,
      provider: null,
      available_at: null,
      coverage: null,
      rationale: "采用已授权补充材料中的口径",
      assumption_key: "company-research-mainline.v1:user_assumption_input_aaaaaaaaaaaaaaaa",
      equation_id: null,
      parent_input_keys: [],
      unknown_reason: null,
      gap_key: null,
    };
    const rebuilt = structuredClone(initial);
    rebuilt.status = "analyzing_company";
    rebuilt.progress = 25;
    rebuilt.updated_at = "2026-08-28T00:08:00Z";
    rebuilt.stages = rebuilt.stages.map((stage) => ({
      ...stage,
      status: stage.key === "identity" || stage.key === "sources"
        ? "completed" as const
        : stage.key === "analysis" ? "active" as const : "pending" as const,
    }));
    Object.assign(rebuilt.workspace.preparation, { status: "building_model", current_step: "model_bundle", progress: 25, error: null });
    withCriticalInputs(rebuilt, [{ ...selected, decision: "replaced_with_user_assumption", replacement }], {
      artifactId: uid(71), version: 2, contentHash: "b".repeat(64),
    });
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(initial);
    vi.spyOn(investmentResearchApi, "decideCompanyResearchCriticalInput").mockResolvedValue(rebuilt);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "保存研究版本" }));
    const drawer = await screen.findByRole("dialog", { name: "确认关键输入" });
    await user.click(within(drawer).getByRole("radio", { name: "改为用户假设" }));
    await user.type(within(drawer).getByRole("textbox", { name: "替代值" }), "360000");
    await user.type(within(drawer).getByRole("textbox", { name: "单位" }), "USD million");
    await user.type(within(drawer).getByRole("textbox", { name: "修改理由" }), "采用已授权补充材料中的口径");
    await user.click(within(drawer).getByRole("button", { name: "提交本项决定" }));

    expect(await screen.findByText("关键输入决定已保存，研究模型将基于新边界继续计算。")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "确认关键输入" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "研究准备进度" })).toHaveAttribute("aria-valuenow", "25");
  });

  it("completes the publication interaction from judgment through frozen replay and one verified export download", async () => {
    const at85 = publicationWorkspace(85);
    const at95 = publicationWorkspace(95);
    const at100 = publicationWorkspace(100);
    const confirmation = deferred<never>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(at85)
      .mockResolvedValueOnce(at95)
      .mockResolvedValueOnce(at100);
    const confirm = vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment").mockImplementation(() => confirmation.promise);
    const preview = vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(publicationPreview());
    const publish = vi.spyOn(investmentResearchApi, "publishCompanyResearch").mockResolvedValue(frozenRevision());
    const replay = vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozenRevision());
    const exported = vi.spyOn(investmentResearchApi, "exportCompanyResearchRevision").mockResolvedValue(markdownExport());
    const createObjectURL = vi.fn((_blob: Blob) => "blob:alphabet-export");
    const revokeObjectURL = vi.fn();
    const TestURL = Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL });
    vi.stubGlobal("URL", TestURL);
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "Alphabet Inc." });
    const editor = screen.getByRole("textbox", { name: "研究备忘录 Markdown" });
    expect((editor as HTMLTextAreaElement).value).toContain("当前正式证据不足");
    await user.clear(editor);
    await user.type(editor, "当前正式证据不足。\r\n\r\n保留全部明确缺口。", { skipClick: true });
    const confirmButton = screen.getByRole("button", { name: "确认当前判断" });
    await user.click(confirmButton);
    expect(confirmButton).toBeDisabled();
    expect(confirmButton).toHaveTextContent("正在确认研究判断…");
    expect(confirm).toHaveBeenCalledWith(ids.project, {
      schema_version: "underwriting.v1",
      expected_lock_version: 2,
      expected_memo_id: expect.any(String),
      expected_memo_content_hash: hash,
      markdown: "当前正式证据不足。\n\n保留全部明确缺口。",
    });
    await act(async () => { confirmation.resolve({} as never); await Promise.resolve(); await Promise.resolve(); });
    expect(await screen.findByRole("status")).toHaveTextContent("判断已确认，可以冻结版本。");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "95");

    const previewButton = screen.getByRole("button", { name: "预览冻结版本" });
    await user.click(previewButton);
    expect(preview).toHaveBeenCalledWith(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 3 });
    let dialog = await screen.findByRole("dialog", { name: "确认冻结版本" });
    for (const copy of [
      "Alphabet Inc.", "ALPHABET:COMPANY", ids.company, "GOOGL", "GOOG", "NASDAQ:GOOGL", "NASDAQ:GOOG",
      ids.googl, ids.goog, "USD", "2026-02-05T00:00:00Z", uid(50), "company-research-mainline.v1", "company-research-model.v1",
      "not_answerable", "未建立方向", "未建立置信度", "ai_capex_risk", "filing", "2025 10-K, p. 32",
      "Q3 Cloud backlog 与 AI capex 回报验证", "当前正式证据不足", "冻结后不可修改", "未建立价值范围",
      "未建立回报范围", "research_gap_31", "expected lock 3", "manifest hash", hash,
    ]) {
      expect(dialog).toHaveTextContent(copy);
    }
    const frozenBlockers = within(dialog).getByRole("list", { name: "冻结阻塞项" });
    expect(frozenBlockers.children).toHaveLength(31);
    expect(frozenBlockers).toHaveClass("ir-publication-blockers");
    const frozenArtifacts = within(dialog).getByRole("list", { name: "冻结制品清单" });
    expect(frozenArtifacts).toHaveTextContent(`evidence_index ${publicationWorkspace(95).artifacts[0].id} v1 input ${hash} content ${hash}`);
    expect(dialog).toHaveAttribute("open");
    expect(within(dialog).getByRole("button", { name: "返回检查" })).toHaveFocus();
    fireEvent(dialog, new Event("cancel", { bubbles: false, cancelable: true }));
    expect(screen.queryByRole("dialog", { name: "确认冻结版本" })).not.toBeInTheDocument();
    await waitFor(() => expect(previewButton).toHaveFocus());
    await user.click(previewButton);
    dialog = await screen.findByRole("dialog", { name: "确认冻结版本" });
    await user.click(within(dialog).getByRole("button", { name: "返回检查" }));
    expect(screen.queryByRole("dialog", { name: "确认冻结版本" })).not.toBeInTheDocument();
    await waitFor(() => expect(previewButton).toHaveFocus());
    await user.click(previewButton);
    dialog = await screen.findByRole("dialog", { name: "确认冻结版本" });
    const publishButton = within(dialog).getByRole("button", { name: "冻结并发布" });
    await user.click(publishButton);
    await waitFor(() => expect(publish).toHaveBeenCalledTimes(1));
    expect(publish.mock.calls[0][0]).toBe(ids.project);
    expect(publish.mock.calls[0][1]).toEqual({ schema_version: "underwriting.v1", expected_lock_version: 3, expected_manifest_hash: hash });
    expect(publish.mock.calls[0][2]).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByRole("status")).toHaveTextContent("冻结版本已发布");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText(ids.revision, { selector: ".ir-publication-revision-id" })).toBeVisible();
    expect(screen.getByText("2026-08-30T02:03:04Z")).toBeVisible();
    expect(within(screen.getByLabelText("冻结版本回放")).getByText("evidence_index v1")).toBeVisible();
    expect(screen.getByLabelText("冻结研究备忘录")).toHaveTextContent("当前正式证据不足，不能形成投资方向、置信度、目标价或预期回报。");
    await waitFor(() => expect(screen.getByRole("button", { name: "查看冻结版本" })).toHaveFocus());
    expect(replay).toHaveBeenCalledWith(ids.project, ids.revision);

    await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
    await waitFor(() => expect(exported).toHaveBeenCalledTimes(1));
    expect(exported).toHaveBeenCalledWith(ids.project, ids.revision);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:alphabet-export");
  }, 15_000);

  it("loads a selected frozen revision before exposing completed module payloads", async () => {
    const completed = publicationWorkspace(100);
    const revision = frozenRevisionFor(completed);
    const revisionRequest = deferred<CompanyResearchFrozenRevision>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(completed);
    const replay = vi.spyOn(investmentResearchApi, "companyResearchRevision").mockImplementation(() => revisionRequest.promise);
    renderPage();

    await screen.findByRole("heading", { name: "Alphabet Inc." });
    const moduleContent = document.querySelector(".ir-module-content")!;
    expect(moduleContent).toHaveTextContent("冻结版本尚未验证");
    expect(moduleContent).not.toHaveTextContent("当前不可回答");
    expect(screen.queryByRole("region", { name: "公司研究结果" })).not.toBeInTheDocument();
    expect(document.querySelector(".ir-run-status")).toHaveTextContent("正在验证冻结版本");
    expect(await screen.findByRole("button", { name: "正在载入冻结版本…" })).toBeDisabled();
    expect(replay).toHaveBeenCalledWith(ids.project, ids.revision);
    await act(async () => { revisionRequest.resolve(revision); await Promise.resolve(); });
    expect(await within(moduleContent as HTMLElement).findByText(`冻结版本 ${ids.revision}`)).toBeVisible();
    expect(within(moduleContent as HTMLElement).getByText("当前不可回答")).toBeVisible();
    expect(screen.getByRole("region", { name: "公司研究结果" })).toHaveTextContent(ids.revision);
    expect(document.querySelector(".ir-run-status")).toHaveTextContent("已冻结版本");
  });

  it.each([
    ["id", uid(999)],
    ["version", 99],
    ["input_hash", "b".repeat(64)],
    ["content_hash", "b".repeat(64)],
  ] as const)("fails closed when a frozen descriptor %s does not match the completed workspace", async (field, value) => {
    const completed = publicationWorkspace(100);
    const mismatched = frozenRevisionFor(completed);
    mismatched.artifacts[0] = { ...mismatched.artifacts[0], [field]: value };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(completed);
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(mismatched);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结版本与工作区制品不一致");
    expect(screen.queryByRole("region", { name: "公司研究结果" })).not.toBeInTheDocument();
    const replayButton = screen.getByRole("button", { name: "查看冻结版本" });
    await user.click(replayButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("冻结版本与工作区制品不一致");
    expect(document.querySelector(".ir-module-content")).toHaveTextContent("冻结版本尚未验证");
    await waitFor(() => expect(replayButton).toHaveFocus());
  });

  it.each([
    ["id", uid(998)],
    ["version", 99],
    ["input_hash", "b".repeat(64)],
    ["content_hash", "b".repeat(64)],
  ] as const)("fails closed when the frozen critical-input descriptor %s is not the authenticated head", async (field, value) => {
    const completed = withCriticalInputs(runForWorkspace(publicationWorkspace(100)), [
      { ...pendingCriticalInput(), decision: "confirmed" },
    ], { artifactId: uid(71), version: 2, contentHash: "c".repeat(64) });
    const mismatched = frozenRevisionForRun(completed);
    const descriptorIndex = mismatched.artifacts.findIndex((item) => item.kind === "critical_inputs");
    mismatched.artifacts[descriptorIndex] = { ...mismatched.artifacts[descriptorIndex], [field]: value };
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(completed);
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(mismatched);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结版本与工作区制品不一致");
    expect(screen.queryByRole("region", { name: "公司研究结果" })).not.toBeInTheDocument();
  });

  it.each([
    ["company", (revision: CompanyResearchFrozenRevision) => { revision.company = { ...revision.company, canonical_name: "Wrong Company" }; }],
    ["securities", (revision: CompanyResearchFrozenRevision) => { revision.securities = [...revision.securities].reverse(); }],
    ["cutoff", (revision: CompanyResearchFrozenRevision) => { revision.cutoff_at = "2026-02-06T00:00:00Z"; }],
    ["strategy", (revision: CompanyResearchFrozenRevision) => { revision.strategy_version = "wrong-strategy.v1"; }],
    ["model", (revision: CompanyResearchFrozenRevision) => { revision.model_version = "wrong-model.v1"; }],
  ] as const)("fails closed when frozen %s identity differs from the authenticated run", async (_field, mutate) => {
    const completed = publicationWorkspace(100);
    const revision = frozenRevisionFor(completed);
    mutate(revision);
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(completed);
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(revision);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结版本与研究运行身份或制品不一致");
    expect(screen.queryByRole("region", { name: "公司研究结果" })).not.toBeInTheDocument();
  });

  it("renders non-null preview ranges with their exact currencies", async () => {
    const ready = publicationWorkspace(95);
    const answerable = {
      ...publicationPreview(),
      assessment: { schema_version: "underwriting.v1" as const, answerability: "answerable" as const, direction: "provisional_neutral" as const, confidence: "medium" as const, content_hash: "c".repeat(64) },
      value_range: { schema_version: "underwriting.v1" as const, minimum: "120", maximum: "180", currency: "USD" as const },
      return_range: { schema_version: "underwriting.v1" as const, minimum: "-0.10", maximum: "0.25" },
    };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(ready);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(answerable);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    const dialog = await screen.findByRole("dialog", { name: "确认冻结版本" });
    expect(dialog).toHaveTextContent("120–180 USD");
    expect(dialog).toHaveTextContent("-0.10–0.25");
    expect(dialog).toHaveTextContent("provisional_neutral");
    expect(dialog).toHaveTextContent("medium");
    expect(dialog).toHaveTextContent("c".repeat(64));
  });

  it("serializes preview and publish while showing loading copy and rejecting a double publish", async () => {
    const ready = publicationWorkspace(95);
    const previewRequest = deferred<CompanyResearchPublicationPreview>();
    const publishRequest = deferred<CompanyResearchFrozenRevision>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(ready);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockImplementation(() => previewRequest.promise);
    const publish = vi.spyOn(investmentResearchApi, "publishCompanyResearch").mockImplementation(() => publishRequest.promise);
    const user = userEvent.setup();
    renderPage();

    const previewButton = await screen.findByRole("button", { name: "预览冻结版本" });
    await user.click(previewButton);
    expect(screen.getByRole("button", { name: "正在生成冻结预览…" })).toBeDisabled();
    await act(async () => { previewRequest.resolve(publicationPreview()); await Promise.resolve(); });
    const publishButton = await screen.findByRole("button", { name: "冻结并发布" });
    await user.dblClick(publishButton);
    expect(publish).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "正在冻结并发布…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "返回检查" })).toBeDisabled();
    expect(previewButton).toBeDisabled();
    const busyDialog = screen.getByRole("dialog", { name: "确认冻结版本" });
    fireEvent(busyDialog, new Event("cancel", { bubbles: false, cancelable: true }));
    expect(busyDialog).toBeInTheDocument();
    await act(async () => { publishRequest.reject(new Error("stop")); await Promise.resolve(); });
    expect(await screen.findByRole("alert")).toHaveTextContent("stop");
    await waitFor(() => expect(publishButton).toHaveFocus());
  });

  it("serializes replay and export with loading copy and preserves Markdown newlines", async () => {
    const completed = publicationWorkspace(100);
    const revision = frozenRevisionFor(completed);
    revision.memo_markdown = "第一段\n\n第二段\n- 缺口";
    const replayRequest = deferred<CompanyResearchFrozenRevision>();
    const exportRequest = deferred<CompanyResearchMarkdownExport>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(completed);
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockImplementation(() => replayRequest.promise);
    vi.spyOn(investmentResearchApi, "exportCompanyResearchRevision").mockImplementation(() => exportRequest.promise);
    const user = userEvent.setup();
    renderPage();

    const replayButton = await screen.findByRole("button", { name: "查看冻结版本" });
    await user.click(replayButton);
    expect(screen.getByRole("button", { name: "正在载入冻结版本…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出 Markdown" })).toBeDisabled();
    await act(async () => { replayRequest.resolve(revision); await Promise.resolve(); });
    expect(screen.getByLabelText("冻结研究备忘录").textContent).toBe("第一段\n\n第二段\n- 缺口");
    const exportButton = screen.getByRole("button", { name: "导出 Markdown" });
    await user.click(exportButton);
    expect(screen.getByRole("button", { name: "正在验证导出…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "查看冻结版本" })).toBeDisabled();
    await act(async () => { exportRequest.reject(new Error("stop")); await Promise.resolve(); });
  });

  it("returns focus on publication failures and reuses the preview idempotency key after response loss", async () => {
    const at95 = publicationWorkspace(95);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(at95)
      .mockResolvedValueOnce(publicationWorkspace(100));
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(publicationPreview());
    const publish = vi.spyOn(investmentResearchApi, "publishCompanyResearch")
      .mockRejectedValueOnce(new Error("发布响应丢失"))
      .mockResolvedValueOnce(frozenRevision());
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozenRevision());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    const publishButton = await screen.findByRole("button", { name: "冻结并发布" });
    await user.click(publishButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("发布响应丢失");
    await waitFor(() => expect(publishButton).toHaveFocus());
    const firstKey = publish.mock.calls[0][2];
    await user.click(publishButton);
    await waitFor(() => expect(publish).toHaveBeenCalledTimes(2));
    expect(publish.mock.calls[1][2]).toBe(firstKey);
    expect(await screen.findByRole("status")).toHaveTextContent("冻结版本已发布");
  });

  it("returns focus to judgment confirmation after a failed request", async () => {
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(publicationWorkspace(85));
    vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment").mockRejectedValue(new Error("判断确认失败"));
    const user = userEvent.setup();
    renderPage();

    const confirmButton = await screen.findByRole("button", { name: "确认当前判断" });
    await user.click(confirmButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("判断确认失败");
    await waitFor(() => expect(confirmButton).toHaveFocus());
  });

  it("refreshes and invalidates the preview after a publication conflict", async () => {
    const ready = publicationWorkspace(95);
    const changed = structuredClone(ready);
    changed.draft.lock_version += 1;
    const changedMemo = changed.artifacts.find((item) => item.kind === "memo")!;
    changedMemo.id = uid(130); changedMemo.version += 1;
    changed.change_summary.artifact_versions.memo += 1;
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(ready).mockResolvedValueOnce(changed);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(publicationPreview());
    vi.spyOn(investmentResearchApi, "publishCompanyResearch").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    await user.click(await screen.findByRole("button", { name: "冻结并发布" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("工作区已更新，请重新生成冻结预览");
    expect(workspaceRead).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog", { name: "确认冻结版本" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "预览冻结版本" })).toHaveFocus());
  });

  it("refreshes a preview conflict that remains at 95 percent and restores preview focus", async () => {
    const at95 = publicationWorkspace(95);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at95).mockResolvedValueOnce(at95);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请重新生成冻结预览");
    await waitFor(() => expect(screen.getByRole("button", { name: "预览冻结版本" })).toHaveFocus());
  });

  it("refreshes a preview conflict to 100 percent and binds the concurrent frozen revision", async () => {
    const at95 = publicationWorkspace(95);
    const at100 = publicationWorkspace(100);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at95).mockResolvedValueOnce(at100);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const replay = vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozenRevisionFor(at100));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    expect(await screen.findByRole("status")).toHaveTextContent("冻结版本已由并发请求发布");
    expect(replay).toHaveBeenCalledWith(ids.project, ids.revision);
    expect(document.querySelector(".ir-module-content")).toHaveTextContent("当前不可回答");
    await waitFor(() => expect(screen.getByRole("button", { name: "查看冻结版本" })).toHaveFocus());
  });

  it("focuses the persistent replay control when a 409 reaches 100 percent but binding fails", async () => {
    const at95 = publicationWorkspace(95);
    const at100 = publicationWorkspace(100);
    const mismatched = frozenRevisionFor(at100);
    mismatched.artifacts[0] = { ...mismatched.artifacts[0], input_hash: "b".repeat(64) };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at95).mockResolvedValueOnce(at100);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(mismatched);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("冻结版本与工作区制品不一致");
    expect(document.querySelector(".ir-module-content")).toHaveTextContent("冻结版本尚未验证");
    await waitFor(() => expect(screen.getByRole("button", { name: "查看冻结版本" })).toHaveFocus());
  });

  it("keeps a concurrent 85 percent judgment editable and restores confirmation focus", async () => {
    const at85 = publicationWorkspace(85);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at85).mockResolvedValueOnce(at85);
    vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "确认当前判断" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请审核最新判断后再确认");
    await waitFor(() => expect(screen.getByRole("button", { name: "确认当前判断" })).toHaveFocus());
  });

  it("recognizes a concurrent confirmation and advances from 85 to 95 percent", async () => {
    const at85 = publicationWorkspace(85);
    const at95 = publicationWorkspace(95);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at85).mockResolvedValueOnce(at95);
    vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "确认当前判断" }));
    expect(await screen.findByRole("status")).toHaveTextContent("判断已由并发请求确认");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "95");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "预览冻结版本" })).toHaveFocus());
  });

  it("recognizes a concurrent publication, binds the selected revision, and advances to 100 percent", async () => {
    const at95 = publicationWorkspace(95);
    const at100 = publicationWorkspace(100);
    const revision = frozenRevisionFor(at100);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at95).mockResolvedValueOnce(at100);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(publicationPreview());
    vi.spyOn(investmentResearchApi, "publishCompanyResearch").mockRejectedValue(Object.assign(new Error("stale"), { status: 409 }));
    const replay = vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(revision);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "预览冻结版本" }));
    await user.click(await screen.findByRole("button", { name: "冻结并发布" }));
    expect(await screen.findByRole("status")).toHaveTextContent("冻结版本已由并发请求发布");
    expect(replay).toHaveBeenCalledWith(ids.project, ids.revision);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getAllByText(`冻结版本 ${ids.revision}`)).toHaveLength(2);
    await waitFor(() => expect(screen.getByRole("button", { name: "查看冻结版本" })).toHaveFocus());
  });

  it("does not poll a completed draft while its publication preview is open", async () => {
    vi.useFakeTimers();
    const ready = publicationWorkspace(95);
    const changed = structuredClone(ready);
    changed.draft.lock_version += 1;
    const changedMemo = changed.artifacts.find((item) => item.kind === "memo")!;
    changedMemo.id = uid(130); changedMemo.version += 1;
    changed.change_summary.artifact_versions.memo += 1;
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(ready).mockResolvedValueOnce(changed);
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockResolvedValue(publicationPreview());
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "预览冻结版本" }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("dialog", { name: "确认冻结版本" })).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("dialog", { name: "确认冻结版本" })).toBeVisible();
    expect(investmentResearchApi.companyResearchWorkspace).toHaveBeenCalledTimes(1);
  });

  it("does not start background polling while a run needs judgment input", async () => {
    vi.useFakeTimers();
    const at85 = publicationWorkspace(85);
    const at95 = publicationWorkspace(95);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(at85)
      .mockResolvedValueOnce(at95);
    vi.spyOn(investmentResearchApi, "confirmCompanyResearchJudgment").mockResolvedValue({} as never);
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(investmentResearchApi.companyResearchWorkspace).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "确认当前判断" }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "95");
  });

  it("keeps completed-run polling stopped after a publication preview failure", async () => {
    vi.useFakeTimers();
    const at95 = publicationWorkspace(95);
    const previewRequest = deferred<CompanyResearchPublicationPreview>();
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(at95).mockResolvedValueOnce(at95);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "previewCompanyResearchPublication").mockImplementation(() => previewRequest.promise);
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    fireEvent.click(screen.getByRole("button", { name: "预览冻结版本" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(workspaceRead).toHaveBeenCalledTimes(1);
    await act(async () => { previewRequest.reject(new Error("stop")); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(workspaceRead).toHaveBeenCalledTimes(1);
  });

  it("renders all nine modules, live progress, gaps, source trace, and candidate review controls without publication preview", async () => {
    const awaitingReview = workspace();
    await decodeWorkspaceFixture(awaitingReview);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(awaitingReview);
    const preview = vi.spyOn(investmentResearchApi, "preview");
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { name: "Alphabet Inc." })).toBeVisible();
    expect(screen.getByText(/GOOGL · Class A/)).toBeVisible();
    expect(screen.getByText(/GOOG · Class C/)).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "研究准备进度" })).toHaveAttribute("aria-valuenow", "25");
    for (const label of ["概览与当前判断", "Google 如何赚钱", "关键经营变量", "来源、事实与缺口", "行业、竞争与监管", "财务、现金流与资本配置", "情景、估值与当前价格隐含", "反证、风险与下一验证", "版本、变化与研究备忘录"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeEnabled();
    }
    expect(screen.getByRole("button", { name: /概览与当前判断/ })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByText(/^方向$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^置信度$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^目标价$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^预期回报$/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    const fact = screen.getByRole("article", { name: "事实 revenue_2025" });
    expect(fact).toHaveTextContent("候选事实");
    expect(fact).toHaveTextContent("2025 10-K, p. 32");
    expect(fact).toHaveTextContent("FY2025 / cutoff 2026-02-05");
    expect(fact).toHaveTextContent("USD million");
    expect(fact).toHaveTextContent("USD");
    expect(within(fact).getByText("冲突组")).toBeVisible();
    expect(within(fact).getByText("接口未提供（不可推断）")).toBeVisible();
    expect(within(fact).getByRole("link", { name: /查看来源/ })).toHaveAttribute("href", "https://abc.xyz/investor/10-k");
    expect(within(fact).getByRole("button", { name: "确认事实 revenue_2025" })).toBeEnabled();
    expect(within(fact).getByRole("button", { name: "驳回事实 revenue_2025" })).toBeEnabled();
    expect(screen.getAllByText(/YouTube 分部利润率未单独披露/)[0]).toBeVisible();
    await user.click(screen.getByRole("button", { name: /概览与当前判断/ }));
    expect(screen.getByText("判断尚在准备")).toBeVisible();
    expect(screen.queryByText(/当前正式证据不足/)).not.toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  }, 10_000);

  it("rejects an initial workspace for a different company without rendering mixed identities", async () => {
    const mixed = workspace();
    mixed.company = { ...mixed.company, id: uid(90), canonical_name: "Other Company" };
    const inconsistentRun = runForWorkspace(mixed);
    inconsistentRun.company = { ...inconsistentRun.company, object_id: ids.company, canonical_name: "Alphabet Inc." };
    vi.spyOn(investmentResearchApi, "companyResearchRun").mockResolvedValue(inconsistentRun);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("研究运行公司身份不一致");
    expect(screen.queryByRole("heading", { name: "Other Company" })).not.toBeInTheDocument();
    expect(screen.queryByText(/GOOGL · Class A/)).not.toBeInTheDocument();
  });

  it("shows formal not-answerable copy only when the memo explicitly assesses it", async () => {
    const formal = assessmentFixture("not_answerable");
    await decodeWorkspaceFixture(formal);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(formal);
    mockFrozenRevision(formal);
    const user = userEvent.setup();
    renderPage();

    await bindFrozenWorkspace(user);
    expect(await screen.findByText("当前不可回答")).toBeVisible();
    expect(screen.getByText(/当前正式证据不足/)).toBeVisible();
    expect(screen.queryByText("判断尚在准备")).not.toBeInTheDocument();
  });

  it("renders DCF, reverse-DCF, distinct GOOGL/GOOG ranges, scenario mechanisms, effects, and counterevidence without probabilities", async () => {
    const rich = workspace({ status: "completed", rich: true });
    await decodeWorkspaceFixture(rich);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    mockFrozenRevision(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await bindFrozenWorkspace(user);
    expect(screen.getByText("可回答")).toBeVisible();
    expect(screen.getByRole("heading", { name: "价值与回报范围" })).toBeVisible();
    expect(screen.getAllByText(/Q3 Cloud backlog 与 AI capex 回报验证/)[0]).toBeVisible();
    await user.click(screen.getByRole("button", { name: /情景、估值与当前价格隐含/ }));

    expect(screen.getByRole("heading", { name: "DCF 情景值" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "反向 DCF" })).toBeVisible();
    const scenarioModule = screen.getByRole("heading", { name: "情景、估值与当前价格隐含" }).closest("section");
    expect(scenarioModule).not.toBeNull();
    const derivedCard = screen.getByRole("article", { name: "当前价格隐含 FCFF 倍数 1.08" });
    const provenanceHref = within(derivedCard).getByRole("link").getAttribute("href");
    expect(provenanceHref).toMatch(/^#provenance-/);
    expect(document.querySelector(provenanceHref!)).toHaveTextContent("dcf.v1");
    expect(document.querySelector(provenanceHref!)).toHaveTextContent("valuation_set");
    expect(within(scenarioModule!).getByText("base_search_ai")).toBeVisible();
    expect(within(scenarioModule!).getByText("base case mechanism")).toBeVisible();
    expect(within(scenarioModule!).getByText("NASDAQ:GOOGL")).toBeVisible();
    expect(within(scenarioModule!).getByText("NASDAQ:GOOG")).toBeVisible();
    expect(within(scenarioModule!).getByText("165")).toBeVisible();
    expect(within(scenarioModule!).getByText("227")).toBeVisible();
    expect(within(scenarioModule!).getByText(/ai_capex_risk/)).toBeVisible();
    expect(screen.queryByText("财务效果与价值")).not.toBeInTheDocument();
    expect(screen.getAllByText("逐情景财务效果：接口未提供（不可推断）")).toHaveLength(3);
    expect(screen.getAllByText("DCF 估值")).toHaveLength(3);
    expect(document.body).not.toHaveTextContent(/概率|加权目标价|probability/i);

    await user.click(screen.getByRole("button", { name: /版本、变化与研究备忘录/ }));
    const changes = screen.getByRole("heading", { name: "制品版本变化" }).closest("div");
    expect(changes).not.toBeNull();
    expect(changes).toHaveTextContent("valuation_set");
    expect(changes).toHaveTextContent("证据审核累计 2 项");
  });

  it.each(["confirmed", "rejected"] as const)("writes a %s review, displays the exact successor, and refreshes stale downstream modules", async (decision) => {
    const initial = workspace();
    const successorWorkspace = workspace({ status: "building_model", factDecision: decision, evidenceVersion: 2 });
    successorWorkspace.modules = successorWorkspace.modules.map((module) => module.key === "evidence_and_gaps" ? module : { ...module, state: "preparing" });
    await decodeWorkspaceFixture(initial);
    await decodeWorkspaceFixture(successorWorkspace);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(initial).mockResolvedValueOnce(successorWorkspace);
    const review = vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: successorWorkspace.artifacts[0] } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    await user.click(screen.getByRole("button", { name: `${decision === "confirmed" ? "确认" : "驳回"}事实 revenue_2025` }));

    expect(await screen.findByRole("status")).toHaveTextContent(`证据版本 2`);
    expect(review).toHaveBeenCalledWith(ids.project, { schema_version: "underwriting.v1", evidence_artifact_id: initial.artifacts[0].id, fact_key: "revenue_2025", decision, expected_head_id: initial.artifacts[0].id });
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent(decision === "confirmed" ? "已确认" : "已驳回");
    expect(screen.getByRole("button", { name: /Google 如何赚钱/ })).toHaveTextContent("准备中");
  });

  it("retains review focus and candidate state when review fails", async () => {
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(workspace());
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockRejectedValue(new Error("审核写入失败"));
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    const confirm = screen.getByRole("button", { name: "确认事实 revenue_2025" });
    await user.click(confirm);

    expect(await screen.findByRole("alert")).toHaveTextContent("审核写入失败");
    expect(confirm).toHaveFocus();
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("候选事实");
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).not.toHaveTextContent("已确认");
  });

  it("locks a committed review after sync failure and retries only the workspace GET", async () => {
    const initial = workspace();
    const successorWorkspace = workspace({ status: "building_model", factDecision: "confirmed", evidenceVersion: 2 });
    const successor = successorWorkspace.artifacts[0];
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error("后继工作区读取失败")).mockResolvedValueOnce(successorWorkspace);
    const review = vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: successor } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    const confirm = screen.getByRole("button", { name: "确认事实 revenue_2025" });
    await user.click(confirm);

    expect(screen.getByRole("alert")).toHaveTextContent("审核已提交");
    expect(screen.getByRole("alert")).toHaveTextContent("后继工作区读取失败");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("button", { name: "重试同步已提交审核" })).toBeEnabled();
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("候选事实");
    await user.click(screen.getByRole("button", { name: "重试同步已提交审核" }));
    expect(await screen.findByRole("status")).toHaveTextContent("证据版本 2");
    expect(review).toHaveBeenCalledTimes(1);
    expect(workspaceRead).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("已确认");
  });

  it("rejects a refreshed workspace whose evidence head differs from the exact reviewed successor", async () => {
    const initial = workspace();
    const exactSuccessor = workspace({ status: "building_model", factDecision: "confirmed", evidenceVersion: 2 });
    const mismatched = workspace({ status: "building_model", factDecision: "confirmed", evidenceVersion: 3 });
    mismatched.artifacts[0] = { ...mismatched.artifacts[0], content_hash: "b".repeat(64) };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(initial).mockResolvedValueOnce(mismatched);
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: exactSuccessor.artifacts[0] } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    const confirm = screen.getByRole("button", { name: "确认事实 revenue_2025" });
    await user.click(confirm);

    expect(await screen.findByRole("alert")).toHaveTextContent("审核已提交，工作区同步失败");
    expect(screen.getByRole("alert")).toHaveTextContent("后继工作区与已提交审核版本不一致");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("button", { name: "重试同步已提交审核" })).toBeEnabled();
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("候选事实");
    expect(screen.getByRole("button", { name: /Google 如何赚钱/ })).toHaveTextContent("准备中");
  });

  it("retries only the server-declared recoverable preparation stage", async () => {
    const failed = retryWorkspaceFixture("model_bundle", false);
    const resumed = retryWorkspaceFixture("model_bundle", true);
    await decodeWorkspaceFixture(failed);
    await decodeWorkspaceFixture(resumed);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(failed).mockResolvedValueOnce(resumed);
    const retry = vi.spyOn(investmentResearchApi, "retryCompanyResearchProject").mockResolvedValue({ project_id: ids.project } as never);
    const user = userEvent.setup();
    renderPage();

    const button = await screen.findByRole("button", { name: "重试建立预测" });
    await user.click(button);
    await waitFor(() => expect(retry).toHaveBeenCalledWith(ids.project));
    expect(await screen.findByRole("progressbar", { name: "研究准备进度" })).toHaveAttribute("aria-valuenow", "25");
  });

  it("surfaces a retry-command workspace regression as a synchronization error", async () => {
    const failed = retryWorkspaceFixture("model_bundle", false);
    failed.draft.lock_version = 4;
    const stale = retryWorkspaceFixture("model_bundle", true);
    stale.draft.lock_version = 3;
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(failed).mockResolvedValueOnce(stale);
    vi.spyOn(investmentResearchApi, "retryCompanyResearchProject").mockResolvedValue({ project_id: ids.project } as never);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "重试建立预测" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("重试已提交，但工作区同步响应无效");
    expect(document.querySelector(".ir-result__versions")).toHaveTextContent("v4");
  });

  it("retains the current company when retry synchronization returns another company", async () => {
    const failed = retryWorkspaceFixture("model_bundle", false);
    const mixed = retryWorkspaceFixture("model_bundle", true);
    mixed.company = { ...mixed.company, id: uid(90), canonical_name: "Other Company" };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(failed).mockResolvedValueOnce(mixed);
    vi.spyOn(investmentResearchApi, "retryCompanyResearchProject").mockResolvedValue({ project_id: ids.project } as never);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "重试建立预测" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("工作区公司身份不一致");
    expect(screen.getByRole("heading", { name: "Alphabet Inc." })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Other Company" })).not.toBeInTheDocument();
  });

  it("polls active preparation with bounded backoff and stops after completion", async () => {
    vi.useFakeTimers();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(activeReviewWorkspace())
      .mockRejectedValueOnce(new Error("temporary poll failure"))
      .mockResolvedValueOnce(workspace({ status: "completed", rich: true }));
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("heading", { name: "Alphabet Inc." })).toBeVisible();

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(workspaceRead).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(workspaceRead).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(workspaceRead).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(workspaceRead).toHaveBeenCalledTimes(3);
  });

  it("rejects a polled workspace for another company and preserves the accepted identity", async () => {
    vi.useFakeTimers();
    const mixed = activeReviewWorkspace();
    mixed.company = { ...mixed.company, id: uid(90), canonical_name: "Other Company" };
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(activeReviewWorkspace()).mockResolvedValueOnce(mixed);
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

    expect(screen.getByRole("alert")).toHaveTextContent("研究运行同步失败：公司身份不一致");
    expect(screen.getByRole("heading", { name: "Alphabet Inc." })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Other Company" })).not.toBeInTheDocument();
  });

  it("keeps the rich ready-state fixture aligned with the production response decoder", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(workspace({ status: "completed", rich: true })), {
      status: 200, headers: { "content-type": "application/json" },
    })));
    await expect(new InvestmentResearchApi().companyResearchWorkspace(ids.project)).resolves.toMatchObject({ project_id: ids.project });
  });

  it("caps repeated preparation polling delays at eight seconds", async () => {
    vi.useFakeTimers();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(activeReviewWorkspace())
      .mockRejectedValue(new Error("temporary poll failure"));
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    for (const delay of [1000, 2000, 4000, 8000, 8000]) {
      const before = workspaceRead.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1); });
      expect(workspaceRead).toHaveBeenCalledTimes(before);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(workspaceRead).toHaveBeenCalledTimes(before + 1);
    }
  });

  it("does not let an older in-flight poll overwrite an exact reviewed successor", async () => {
    vi.useFakeTimers();
    const initial = activeReviewWorkspace();
    const successor = workspace({ status: "building_model", factDecision: "confirmed", evidenceVersion: 2 });
    const poll = deferred<CompanyResearchWorkspace>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(initial)
      .mockImplementationOnce(() => poll.promise)
      .mockResolvedValueOnce(successor);
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: successor.artifacts[0] } as never);
    renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    fireEvent.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认事实 revenue_2025" }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("status")).toHaveTextContent("证据版本 2");
    await act(async () => { poll.resolve(initial); await Promise.resolve(); });
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("已确认");
  });

  it.each(["hidden", "unmount"] as const)("invalidates an in-flight poll on %s", async (mode) => {
    vi.useFakeTimers();
    const poll = deferred<CompanyResearchWorkspace>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(activeReviewWorkspace()).mockImplementationOnce(() => poll.promise);
    const rendered = renderPage();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    if (mode === "hidden") {
      await act(async () => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
    } else {
      rendered.unmount();
    }
    await act(async () => { poll.resolve(workspace({ status: "completed", rich: true })); await Promise.resolve(); });
    if (mode === "hidden") expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  it("invalidates an old project's in-flight poll after a project switch", async () => {
    vi.useFakeTimers();
    const nextProjectId = uid(99);
    const poll = deferred<CompanyResearchWorkspace>();
    vi.spyOn(investmentResearchApi, "project").mockImplementation(async (projectId) => ({ ...project(), id: projectId }));
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(activeReviewWorkspace())
      .mockImplementationOnce(() => poll.promise)
      .mockResolvedValueOnce(forProject(workspace({ status: "completed", rich: true }), nextProjectId));
    const router = createMemoryRouter([{ path: "/research/projects/:projectId", element: <ResearchWorkbenchPage /> }], { initialEntries: [`/research/projects/${ids.project}`] });
    render(<RouterProvider router={router} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await act(async () => { void router.navigate(`/research/projects/${nextProjectId}`); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    await act(async () => { poll.resolve(workspace()); await Promise.resolve(); });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it.each([
    ["wrong project", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, project_id: uid(98) })],
    ["non-advancing version", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, version: 1 })],
    ["jumped version", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, version: 3 })],
    ["missing decision", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, payload: { ...successor.payload, facts: [evidenceFact()] } })],
    ["wrong decision", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, payload: { ...successor.payload, facts: [evidenceFact("rejected")] } })],
    ["added fact", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, payload: { ...successor.payload, facts: [...(successor.payload as { facts: object[] }).facts, { ...evidenceFact(), fact_key: "unexpected_fact" }] } })],
    ["top-level payload mutation", (successor: CompanyResearchWorkspace["artifacts"][number]) => ({ ...successor, payload: { ...successor.payload, cutoff: "2026-02-06T00:00:00Z" } })],
  ] as const)("rejects a semantically invalid review successor: %s", async (_label, mutate) => {
    const successorWorkspace = workspace({ factDecision: "confirmed", evidenceVersion: 2 });
    const invalid = mutate(successorWorkspace.artifacts[0]) as never;
    const refresh = vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(workspace()).mockResolvedValueOnce(successorWorkspace);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: invalid } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    const confirm = screen.getByRole("button", { name: "确认事实 revenue_2025" });
    await user.click(confirm);
    expect(await screen.findByRole("alert")).toHaveTextContent("审核已提交");
    expect(screen.getByRole("alert")).toHaveTextContent("后继工作区与已提交审核版本不一致");
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it("rejects a successor that drops an unaffected evidence fact", async () => {
    const initial = workspace();
    const initialEvidence = initial.artifacts[0] as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "evidence_index" }>;
    initialEvidence.payload.facts.push({ ...evidenceFact(), fact_key: "revenue_2024", metric_key: "revenue prior" });
    const successorWorkspace = workspace({ factDecision: "confirmed", evidenceVersion: 2 });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(initial).mockResolvedValueOnce(successorWorkspace);
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: successorWorkspace.artifacts[0] } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    await user.click(screen.getByRole("button", { name: "确认事实 revenue_2025" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("审核已提交");
    expect(screen.getByRole("button", { name: "确认事实 revenue_2025" })).toBeDisabled();
  });

  it("disables every evidence review action while one review is active", async () => {
    const initial = workspace();
    const evidence = initial.artifacts[0] as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "evidence_index" }>;
    evidence.payload.facts.push({ ...evidenceFact(), fact_key: "revenue_2024", metric_key: "revenue prior" });
    const review = deferred<never>();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(initial);
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockImplementation(() => review.promise);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    await user.click(screen.getByRole("button", { name: "确认事实 revenue_2025" }));
    for (const button of screen.getAllByRole("button", { name: /^(确认|驳回)事实/ })) expect(button).toBeDisabled();
    await act(async () => { review.reject(new Error("stop")); await Promise.resolve(); });
  });

  it("renders state-specific honest empty content for all nine modules and never reuses business facts as industry evidence", async () => {
    const empty = workspace();
    const business = artifact("business_map", { modules: [], _lineage: {} });
    empty.artifacts.push(business as CompanyResearchWorkspace["artifacts"][number]);
    empty.change_summary.artifact_versions.business_map = business.version;
    const states = ["preparing", "blocked", "not_started", "needs_review", "ready", "preparing", "not_started", "blocked", "not_started"] as const;
    empty.modules = empty.modules.map((module, index) => module.key === "industry_competition_regulation"
      ? { ...module, state: states[index], artifact_refs: [registryRef(business)] }
      : { ...module, state: states[index], artifact_refs: module.key === "evidence_and_gaps" ? module.artifact_refs : [] });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(empty);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    const assertions = [
      [/概览与当前判断/, "概览与当前判断正在准备"],
      [/Google 如何赚钱/, "Google 如何赚钱已阻塞"],
      [/关键经营变量/, "关键经营变量尚未开始"],
      [/来源、事实与缺口/, "候选事实"],
      [/行业、竞争与监管/, "接口未提供行业、竞争与监管专属语义（不可推断）"],
      [/财务、现金流与资本配置/, "财务、现金流与资本配置正在准备"],
      [/情景、估值与当前价格隐含/, "情景、估值与当前价格隐含尚未开始"],
      [/反证、风险与下一验证/, "反证、风险与下一验证已阻塞"],
      [/版本、变化与研究备忘录/, "版本、变化与研究备忘录尚未开始"],
    ] as const;
    for (const [buttonName, copy] of assertions) {
      await user.click(screen.getByRole("button", { name: buttonName }));
      expect(screen.getByText(new RegExp(copy))).toBeVisible();
    }
  });

  it("treats server module state as authoritative even when retained artifacts exist", async () => {
    const retained = workspace({ status: "completed", rich: true });
    retained.modules = retained.modules.map((module) => module.key === "business_map" ? { ...module, state: "blocked", artifact_refs: [] } : module.key === "versions_changes_memo" ? { ...module, state: "preparing", artifact_refs: [] } : module);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(retained);
    mockFrozenRevision(retained);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await bindFrozenWorkspace(user);
    await user.click(screen.getByRole("button", { name: /Google 如何赚钱/ }));
    expect(screen.getByText(/Google 如何赚钱已阻塞/)).toBeVisible();
    expect(screen.queryByText("Search")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /版本、变化与研究备忘录/ }));
    expect(screen.getByText(/版本、变化与研究备忘录正在准备/)).toBeVisible();
    expect(screen.queryByText(/当前草稿版本/)).not.toBeInTheDocument();
  });

  it.each([
    ["pending", "情景、估值与当前价格隐含正在准备"],
    ["blocked", "估值已阻塞（服务器状态：blocked）"],
  ] as const)("renders valuation state %s without inferring a gap block", async (valuationState, copy) => {
    const rich = valuationState === "pending" ? workspace() : answerableWithoutValuationFixture();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    if (rich.preparation.status === "completed") mockFrozenRevision(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    if (rich.preparation.status === "completed") await bindFrozenWorkspace(user);
    await user.click(screen.getByRole("button", { name: /情景、估值与当前价格隐含/ }));
    expect(screen.getByText(copy, { exact: false })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "DCF 情景值" })).not.toBeInTheDocument();
    expect(screen.queryByText(/数据缺口阻塞/)).not.toBeInTheDocument();
  });

  it("exercises ready-state content for every supported research module", async () => {
    const rich = workspace({ status: "completed", rich: true });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    mockFrozenRevision(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await bindFrozenWorkspace(user);
    const assertions = [
      [/概览与当前判断/, "可回答"], [/Google 如何赚钱/, "Search；YouTube"], [/关键经营变量/, "revenue × growth"],
      [/来源、事实与缺口/, "2025 10-K, p. 32"], [/行业、竞争与监管/, "接口未提供行业、竞争与监管专属语义（不可推断）"],
      [/财务、现金流与资本配置/, "FY2025"], [/情景、估值与当前价格隐含/, "base_search_ai"],
      [/反证、风险与下一验证/, "Q3 Cloud backlog 与 AI capex 回报验证"], [/版本、变化与研究备忘录/, "machine_draft · answerable"],
    ] as const;
    for (const [buttonName, copy] of assertions) {
      await user.click(screen.getByRole("button", { name: buttonName }));
      expect(screen.getAllByText(copy, { exact: false })[0]).toBeVisible();
    }
  });

  it("resolves every non-external numeric link to concrete equation, assumption, or gap provenance", async () => {
    const rich = workspace({ status: "completed", rich: true });
    const driver = rich.artifacts.find((item) => item.kind === "driver_map")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "driver_map" }>;
    driver.payload.drivers[0].values.push({ ...observation("missing_metric", "not available"), state: "gap", source_ref: null, gap_key: "missing_metric_gap", assumption_key: null });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    mockFrozenRevision(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await bindFrozenWorkspace(user);
    await user.click(screen.getByRole("button", { name: /关键经营变量/ }));

    for (const [cardName, key] of [["search_growth 0.11", "assumption_search_growth"], ["missing_metric not available", "missing_metric_gap"]] as const) {
      const href = within(screen.getByRole("article", { name: cardName })).getByRole("link").getAttribute("href");
      expect(href).toMatch(/^#provenance-/);
      expect(document.querySelector(href!)).toHaveTextContent(key);
      expect(document.querySelector(href!)).toHaveTextContent("driver_map");
    }
  });

  it("shows explicit unavailable overview fields for contract-valid empty answerable and partial memos", async () => {
    const answerable = answerableWithoutValuationFixture();
    const judgment = answerable.artifacts.find((item) => item.kind === "judgment_context")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "judgment_context" }>;
    judgment.payload.strongest_counterevidence = [];
    judgment.payload.next_verification_events = [];
    await decodeWorkspaceFixture(answerable);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(answerable);
    vi.spyOn(investmentResearchApi, "companyResearchRevision")
      .mockResolvedValueOnce(frozenRevisionFor(answerable));
    const user = userEvent.setup();
    const rendered = renderPage();
    await bindFrozenWorkspace(user);
    expect(await screen.findByText("价值与回报范围未提供或不一致。")).toBeVisible();
    expect(screen.getByText("最强反证未提供。")).toBeVisible();
    expect(screen.getByText("下一验证事件未提供。")).toBeVisible();
    rendered.unmount();

    const partial = assessmentFixture("partially_answerable");
    await decodeWorkspaceFixture(partial);
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(partial);
    vi.spyOn(investmentResearchApi, "companyResearchRevision").mockResolvedValue(frozenRevisionFor(partial));
    renderPage();
    await bindFrozenWorkspace(user);
    expect(await screen.findByText("未提供阻塞项。")).toBeVisible();
  });

  it("renders a ready overview from judgment_context even when no memo exists", async () => {
    const ready = workspace({ status: "completed", rich: true });
    ready.artifacts = ready.artifacts.filter((item) => item.kind !== "memo");
    delete ready.change_summary.artifact_versions.memo;
    ready.modules = ready.modules.map((module) => module.key === "versions_changes_memo" ? { ...module, state: "not_started", artifact_refs: [] } : module);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(ready);
    mockFrozenRevision(ready);
    const user = userEvent.setup();
    renderPage();

    await bindFrozenWorkspace(user);
    expect(await screen.findByText("判断尚在准备")).toBeVisible();
    expect(screen.getAllByText("Q3 Cloud backlog 与 AI capex 回报验证")[0]).toBeVisible();
    expect(screen.queryByText(/概览与当前判断标记为可查看，但所需制品缺失/)).not.toBeInTheDocument();
  });
});
