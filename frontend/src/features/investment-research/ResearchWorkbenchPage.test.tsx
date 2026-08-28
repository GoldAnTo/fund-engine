import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  investmentResearchApi,
  type CompanyResearchWorkspace,
  type ProductProject,
} from "../../data/investmentResearchApi";
import ResearchWorkbenchPage from "./ResearchWorkbenchPage";

const hash = "a".repeat(64);
const uid = (value: number) => `20000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const ids = { project: uid(1), company: uid(2), googl: uid(3), goog: uid(4), preparation: uid(5), draft: uid(6), evidence: uid(7), gaps: uid(8) };

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

function source(factKey = "revenue_2025") {
  return { kind: "external", fact_key: factKey, source_role: "filing", source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash } as const;
}

function observation(key: string, value: string, state: "reported" | "derived" | "assumption" = "reported") {
  return {
    key, value, unit: key.includes("return") || key.includes("rate") ? "ratio" : "USD million", currency: key.includes("return") || key.includes("rate") ? "N/A" : "USD",
    period: "FY2025 / cutoff 2026-02-05", state,
    source_ref: state === "reported" ? source(key) : state === "derived" ? { kind: "artifact_computation", artifact_refs: [], market_snapshot_ids: [], equation_id: "dcf.v1" } : null,
    gap_key: null, assumption_key: state === "assumption" ? `assumption_${key}` : null,
  };
}

function artifact(kind: string, payload: object, version = 1) {
  return { schema_version: "underwriting.v1", id: uid(100 + version + kind.length), project_id: ids.project, kind, version, input_hash: hash, content_hash: hash, source_refs: [{ source_role: "filing", source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash }], payload };
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

function workspace(options: {
  status?: CompanyResearchWorkspace["preparation"]["status"];
  rich?: boolean;
  factDecision?: "confirmed" | "rejected";
  evidenceVersion?: number;
} = {}): CompanyResearchWorkspace {
  const status = options.status ?? "awaiting_evidence_review";
  const evidence = artifact("evidence_index", {
    fixture_content_hash: hash, cutoff: "2026-02-05T00:00:00Z", company_external_key: "ALPHABET:COMPANY",
    security_external_keys: ["NASDAQ:GOOGL", "NASDAQ:GOOG"], facts: [evidenceFact(options.factDecision)],
  }, options.evidenceVersion ?? 1);
  const gaps = artifact("research_gaps", {
    fixture_content_hash: hash, company_external_key: "ALPHABET:COMPANY",
    gaps: [{ gap_key: "youtube_margin_gap", business_module: "youtube", reason: "YouTube 分部利润率未单独披露" }],
  });
  const artifacts: ReturnType<typeof artifact>[] = [evidence, gaps];
  if (options.rich) {
    artifacts.push(
      artifact("business_map", { modules: [{ module_key: "google_services", revenue_sources: ["Search", "YouTube"], cost_structure: ["TAC", "基础设施"], capital_needs: ["AI 数据中心"], fact_refs: [source()], gap_refs: ["youtube_margin_gap"], classified_evidence: [] }], _lineage: {} }),
      artifact("driver_map", { drivers: [{ driver_key: "search_growth", module_key: "google_services", fact_refs: [source()], assumption_refs: [], equation: "revenue × growth", output_metric: "revenue", equation_id: "driver.v1", values: [observation("search_growth", "0.11", "assumption")], assumption_rationale: "查询量与变现率", assumption_equation: "volume × monetization" }], _lineage: {} }),
      artifact("financial_bridge", { rows: [{ period: "FY2025", revenue: observation("revenue", "350018"), operating_income: observation("operating_income", "112390"), cash_tax_rate: observation("cash_tax_rate", "0.17", "assumption"), depreciation: observation("depreciation", "21400"), capex: observation("capex", "52500"), working_capital_change: observation("working_capital_change", "2200"), fcff: observation("fcff", "79100", "derived"), fact_refs: [source()], assumption_refs: [] }], _lineage: {} }),
      artifact("scenario_set", { scenarios: ["base", "bull", "bear"].map((scenarioId, index) => ({ scenario_id: scenarioId, mechanism_id: `${scenarioId}_search_ai`, driver_overrides: [{ driver_key: "fcff_multiplier", observation: observation("fcff_multiplier", String([1, 1.2, 0.75][index]), "assumption"), rationale: `${scenarioId} case mechanism`, equation: "baseline × multiplier" }] })), _lineage: {} }),
      artifact("valuation_set", {
        scenario_dcf_values: ["base", "bull", "bear"].map((scenarioId, index) => ({ scenario_id: scenarioId, enterprise_value: observation(`${scenarioId}_dcf`, String([2400000, 2900000, 1700000][index]), "derived") })),
        reverse_dcf: { driver_key: "fcff_multiplier", implied_value: observation("implied_fcff_multiplier", "1.08", "derived"), achieved_residual: observation("residual", "0.0001", "derived"), iteration_count: observation("iterations", "7", "derived") },
        security_value_ranges: [
          { security_external_key: "NASDAQ:GOOGL", usd_per_share: { minimum: observation("googl_min", "165", "derived"), maximum: observation("googl_max", "225", "derived") }, cny_return: { minimum: observation("googl_return_min", "0.04", "derived"), maximum: observation("googl_return_max", "0.41", "derived") } },
          { security_external_key: "NASDAQ:GOOG", usd_per_share: { minimum: observation("goog_min", "166", "derived"), maximum: observation("goog_max", "227", "derived") }, cny_return: { minimum: observation("goog_return_min", "0.03", "derived"), maximum: observation("goog_return_max", "0.40", "derived") } },
        ],
        required_return: observation("required_return", "0.12", "assumption"), required_return_comparisons: [], _lineage: {},
      }),
      artifact("judgment_context", { operating_baseline_available: true, financial_bridge_closed: true, market_security_bridge_available: true, strongest_counterevidence: [{ ...source("ai_capex_risk"), fact_key: "ai_capex_risk" }], next_verification_events: ["Q3 Cloud backlog 与 AI capex 回报验证"], _lineage: {} }),
      artifact("memo", { assessment_status: "answerable", business_map_ref: {}, driver_map_ref: {}, financial_bridge_ref: {}, scenario_set_ref: {}, valuation_set_ref: {}, gap_keys: ["youtube_margin_gap"], strongest_counterevidence: [{ ...source("ai_capex_risk"), fact_key: "ai_capex_risk" }], next_verification_events: ["Q3 Cloud backlog 与 AI capex 回报验证"], candidate_status: "machine_draft", _lineage: {} }),
    );
  }
  const moduleStates = options.rich ? "ready" : "preparing";
  return {
    schema_version: "underwriting.v1", project_id: ids.project,
    company: { schema_version: "underwriting.v1", object_id: ids.company, id: ids.company, external_key: "ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    preparation: {
      schema_version: "underwriting.v1", id: ids.preparation, status,
      current_step: status === "completed" ? null : status === "recoverable_failure" ? "valuation_set" : "research_gaps",
      progress: options.rich ? 100 : 25,
      error: status === "recoverable_failure" ? { schema_version: "underwriting.v1", code: "valuation_temporarily_unavailable", failed_step: "valuation_set", retryable: true, next_attempt_at: "2026-08-28T01:00:00Z" } : null,
    },
    artifacts: artifacts as CompanyResearchWorkspace["artifacts"],
    modules: ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"].map((key) => ({ schema_version: "underwriting.v1", key, state: key === "evidence_and_gaps" && !options.rich ? "needs_review" : moduleStates, artifact_refs: [], valuation_state: key === "scenarios_valuation_implied_expectations" ? options.rich ? "ready" : "pending" : "not_applicable" })) as CompanyResearchWorkspace["modules"],
    source_count: 1, gap_count: 1,
    draft: { schema_version: "underwriting.v1", id: ids.draft, lock_version: options.factDecision ? 2 : 1, base_revision_id: null }, selected_revision: null,
    change_summary: { artifact_versions: Object.fromEntries(artifacts.map((item) => [item.kind, item.version])), reviewed_fact_count: options.factDecision ? 1 : 0 },
  };
}

function renderPage() {
  return render(<MemoryRouter initialEntries={[`/research/projects/${ids.project}`]}><Routes><Route path="/research/projects/:projectId" element={<ResearchWorkbenchPage />} /></Routes></MemoryRouter>);
}

describe("Alphabet company research workbench", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders all nine modules, live progress, gaps, source trace, and candidate review controls without publication preview", async () => {
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(workspace());
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
    expect(screen.getByText(/YouTube 分部利润率未单独披露/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /概览与当前判断/ }));
    expect(screen.getByText("当前不可回答")).toBeVisible();
    expect(preview).not.toHaveBeenCalled();
  });

  it("renders DCF, reverse-DCF, distinct GOOGL/GOOG ranges, scenario mechanisms, effects, and counterevidence without probabilities", async () => {
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(workspace({ status: "completed", rich: true }));
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    expect(screen.getByText("可回答")).toBeVisible();
    expect(screen.getByRole("heading", { name: "价值与回报范围" })).toBeVisible();
    expect(screen.getByText(/Q3 Cloud backlog 与 AI capex 回报验证/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /情景、估值与当前价格隐含/ }));

    expect(screen.getByRole("heading", { name: "DCF 情景值" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "反向 DCF" })).toBeVisible();
    expect(within(screen.getByRole("article", { name: "当前价格隐含 FCFF 倍数 1.08" })).getByRole("link")).toHaveAttribute("href", "#audit-details");
    expect(screen.getByText("base_search_ai")).toBeVisible();
    expect(screen.getByText("base case mechanism")).toBeVisible();
    expect(screen.getByText("NASDAQ:GOOGL")).toBeVisible();
    expect(screen.getByText("NASDAQ:GOOG")).toBeVisible();
    expect(screen.getByText("165")).toBeVisible();
    expect(screen.getByText("227")).toBeVisible();
    expect(screen.getByText(/ai_capex_risk/)).toBeVisible();
    expect(screen.getAllByText("财务效果与价值")).toHaveLength(3);
    expect(document.body).not.toHaveTextContent(/概率|加权目标价|probability/i);

    await user.click(screen.getByRole("button", { name: /版本、变化与研究备忘录/ }));
    const changes = screen.getByRole("heading", { name: "制品版本变化" }).closest("div");
    expect(changes).not.toBeNull();
    expect(changes).toHaveTextContent("valuation_set");
    expect(changes).toHaveTextContent("证据审核累计 0 项");
  });

  it.each(["confirmed", "rejected"] as const)("writes a %s review, displays the exact successor, and refreshes stale downstream modules", async (decision) => {
    const initial = workspace();
    const successorWorkspace = workspace({ status: "building_model", factDecision: decision, evidenceVersion: 2 });
    successorWorkspace.modules = successorWorkspace.modules.map((module) => module.key === "evidence_and_gaps" ? module : { ...module, state: "preparing" });
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

  it("keeps the old workspace fail-closed when review succeeds but successor refresh fails", async () => {
    const initial = workspace();
    const successor = workspace({ factDecision: "confirmed", evidenceVersion: 2 }).artifacts[0];
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error("后继工作区读取失败"));
    vi.spyOn(investmentResearchApi, "reviewCompanyEvidence").mockResolvedValue({ evidence_artifact: successor } as never);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    await user.click(screen.getByRole("button", { name: "确认事实 revenue_2025" }));

    expect(await screen.findByRole("status")).toHaveTextContent("证据版本 2");
    expect(screen.getByRole("alert")).toHaveTextContent("后继工作区读取失败");
    expect(screen.getByRole("article", { name: "事实 revenue_2025" })).toHaveTextContent("候选事实");
    expect(screen.getByRole("button", { name: /Google 如何赚钱/ })).toHaveTextContent("准备中");
  });

  it("retries only the server-declared recoverable preparation stage", async () => {
    const failed = workspace({ status: "recoverable_failure" });
    const resumed = workspace({ status: "building_model" });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(failed).mockResolvedValueOnce(resumed);
    const retry = vi.spyOn(investmentResearchApi, "retryCompanyResearchProject").mockResolvedValue({ project_id: ids.project } as never);
    const user = userEvent.setup();
    renderPage();

    const button = await screen.findByRole("button", { name: "重试 valuation_set" });
    await user.click(button);
    await waitFor(() => expect(retry).toHaveBeenCalledWith(ids.project));
    expect(await screen.findByRole("progressbar", { name: "研究准备进度" })).toHaveAttribute("aria-valuenow", "25");
  });

  it("polls active preparation with bounded backoff and stops after completion", async () => {
    vi.useFakeTimers();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(workspace())
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
});
