import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
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

function source(factKey = "revenue_2025") {
  return { kind: "external", fact_key: factKey, source_role: "filing", source_url: "https://abc.xyz/investor/10-k", source_locator: "2025 10-K, p. 32", raw_hash: hash } as const;
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
    expect(screen.getByText(/YouTube 分部利润率未单独披露/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /概览与当前判断/ }));
    expect(screen.getByText("判断尚在准备")).toBeVisible();
    expect(screen.queryByText(/当前正式证据不足/)).not.toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  });

  it("shows formal not-answerable copy only when the memo explicitly assesses it", async () => {
    const formal = workspace();
    formal.artifacts.push(artifact("memo", {
      assessment_status: "not_answerable", business_map_ref: {}, driver_map_ref: {}, financial_bridge_ref: {}, scenario_set_ref: {}, valuation_set_ref: null,
      gap_keys: ["youtube_margin_gap"], strongest_counterevidence: [], next_verification_events: [], candidate_status: "machine_draft", _lineage: {},
    }) as CompanyResearchWorkspace["artifacts"][number]);
    formal.modules = formal.modules.map((module) => module.key === "overview" ? { ...module, state: "ready" } : module);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(formal);
    renderPage();

    expect(await screen.findByText("当前不可回答")).toBeVisible();
    expect(screen.getByText(/当前正式证据不足/)).toBeVisible();
    expect(screen.queryByText("判断尚在准备")).not.toBeInTheDocument();
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
    const derivedCard = screen.getByRole("article", { name: "当前价格隐含 FCFF 倍数 1.08" });
    const provenanceHref = within(derivedCard).getByRole("link").getAttribute("href");
    expect(provenanceHref).toMatch(/^#provenance-/);
    expect(document.querySelector(provenanceHref!)).toHaveTextContent("dcf.v1");
    expect(document.querySelector(provenanceHref!)).toHaveTextContent("valuation_set");
    expect(screen.getByText("base_search_ai")).toBeVisible();
    expect(screen.getByText("base case mechanism")).toBeVisible();
    expect(screen.getByText("NASDAQ:GOOGL")).toBeVisible();
    expect(screen.getByText("NASDAQ:GOOG")).toBeVisible();
    expect(screen.getByText("165")).toBeVisible();
    expect(screen.getByText("227")).toBeVisible();
    expect(screen.getByText(/ai_capex_risk/)).toBeVisible();
    expect(screen.queryByText("财务效果与价值")).not.toBeInTheDocument();
    expect(screen.getAllByText("逐情景财务效果：接口未提供（不可推断）")).toHaveLength(3);
    expect(screen.getAllByText("DCF 估值")).toHaveLength(3);
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

  it("surfaces a retry-command workspace regression as a synchronization error", async () => {
    const failed = workspace({ status: "recoverable_failure" });
    failed.draft.lock_version = 4;
    const stale = workspace({ status: "building_model" });
    stale.draft.lock_version = 3;
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(failed).mockResolvedValueOnce(stale);
    vi.spyOn(investmentResearchApi, "retryCompanyResearchProject").mockResolvedValue({ project_id: ids.project } as never);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "重试 valuation_set" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("重试已提交，但工作区同步响应无效");
    expect(screen.getByText("草稿版本 4")).toBeVisible();
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

  it("caps repeated preparation polling delays at eight seconds", async () => {
    vi.useFakeTimers();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    const workspaceRead = vi.spyOn(investmentResearchApi, "companyResearchWorkspace")
      .mockResolvedValueOnce(workspace())
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
    const initial = workspace();
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
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValueOnce(workspace()).mockImplementationOnce(() => poll.promise);
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
      .mockResolvedValueOnce(workspace())
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
    empty.artifacts = [];
    const states = ["preparing", "blocked", "not_started", "needs_review", "ready", "preparing", "needs_review", "blocked", "not_started"] as const;
    empty.modules = empty.modules.map((module, index) => ({ ...module, state: states[index] }));
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(empty);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    const assertions = [
      [/概览与当前判断/, "概览与当前判断正在准备"],
      [/Google 如何赚钱/, "Google 如何赚钱已阻塞"],
      [/关键经营变量/, "关键经营变量尚未开始"],
      [/来源、事实与缺口/, "来源、事实与缺口等待审核"],
      [/行业、竞争与监管/, "接口未提供行业、竞争与监管专属语义（不可推断）"],
      [/财务、现金流与资本配置/, "财务、现金流与资本配置正在准备"],
      [/情景、估值与当前价格隐含/, "情景、估值与当前价格隐含等待审核"],
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
    retained.modules = retained.modules.map((module) => module.key === "business_map" ? { ...module, state: "blocked" } : module.key === "versions_changes_memo" ? { ...module, state: "preparing" } : module);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(retained);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /Google 如何赚钱/ }));
    expect(screen.getByText(/Google 如何赚钱已阻塞/)).toBeVisible();
    expect(screen.queryByText("Search")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /版本、变化与研究备忘录/ }));
    expect(screen.getByText(/版本、变化与研究备忘录正在准备/)).toBeVisible();
    expect(screen.queryByText(/当前草稿版本/)).not.toBeInTheDocument();
  });

  it.each([
    ["pending", "估值仍在准备（服务器状态：pending）"],
    ["blocked", "估值已阻塞（服务器状态：blocked）"],
  ] as const)("renders valuation state %s without inferring a gap block", async (valuationState, copy) => {
    const rich = workspace({ status: "completed", rich: true });
    rich.modules = rich.modules.map((module) => module.key === "scenarios_valuation_implied_expectations" ? { ...module, valuation_state: valuationState } : module);
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /情景、估值与当前价格隐含/ }));
    expect(screen.getByText(copy)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "DCF 情景值" })).not.toBeInTheDocument();
    expect(screen.queryByText(/数据缺口阻塞/)).not.toBeInTheDocument();
  });

  it("exercises ready-state content for every supported research module", async () => {
    const rich = workspace({ status: "completed", rich: true });
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(rich);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
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
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { name: "Alphabet Inc." });
    await user.click(screen.getByRole("button", { name: /关键经营变量/ }));

    for (const [cardName, key] of [["search_growth 0.11", "assumption_search_growth"], ["missing_metric not available", "missing_metric_gap"]] as const) {
      const href = within(screen.getByRole("article", { name: cardName })).getByRole("link").getAttribute("href");
      expect(href).toMatch(/^#provenance-/);
      expect(document.querySelector(href!)).toHaveTextContent(key);
      expect(document.querySelector(href!)).toHaveTextContent("driver_map");
    }
  });

  it("shows explicit unavailable overview fields for contract-valid empty answerable and partial memos", async () => {
    const answerable = workspace({ status: "completed", rich: true });
    const valuation = answerable.artifacts.find((item) => item.kind === "valuation_set")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "valuation_set" }>;
    valuation.payload.security_value_ranges = [];
    const judgment = answerable.artifacts.find((item) => item.kind === "judgment_context")! as Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "judgment_context" }>;
    judgment.payload.strongest_counterevidence = [];
    judgment.payload.next_verification_events = [];
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(project());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(answerable);
    const rendered = renderPage();
    expect(await screen.findByText("价值与回报范围未提供或不一致。")).toBeVisible();
    expect(screen.getByText("最强反证未提供。")).toBeVisible();
    expect(screen.getByText("下一验证事件未提供。")).toBeVisible();
    rendered.unmount();

    const partial = workspace();
    partial.artifacts.push(artifact("memo", { assessment_status: "partially_answerable", business_map_ref: {}, driver_map_ref: {}, financial_bridge_ref: {}, scenario_set_ref: {}, valuation_set_ref: null, gap_keys: [], strongest_counterevidence: [], next_verification_events: [], candidate_status: "machine_draft", _lineage: {} }) as CompanyResearchWorkspace["artifacts"][number]);
    partial.modules = partial.modules.map((module) => module.key === "overview" ? { ...module, state: "ready" } : module);
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(partial);
    renderPage();
    expect(await screen.findByText("未提供阻塞项。")).toBeVisible();
  });
});
