import type { CompanyFinancialModelInputs, CompanyFinancialModelRecord, CompanyFinancialModelWorkspace } from "./companyFinancialModel";

export const modelIds = { project: "20000000-0000-4000-8000-000000000001", revision: "20000000-0000-4000-8000-000000000009", draft: "20000000-0000-4000-8000-000000000301" };
export const modelHash = "a".repeat(64);
export function modelInputs(): CompanyFinancialModelInputs {
  return { schema_version: "company-research.financial-model-inputs.v1", first_fiscal_year: 2026,
    scenarios: (["base", "bull", "bear"] as const).map((scenario_id) => ({ scenario_id, mechanism: `${scenario_id} 有条件的经营路径`,
      paths: { revenue: ["450000", "480000", "500000", "520000", "540000"], operating_margin: ["0.3", "0.3", "0.3", "0.3", "0.3"], cash_tax_rate: ["0.2", "0.2", "0.2", "0.2", "0.2"], depreciation: ["30000", "35000", "40000", "45000", "50000"], capex: ["180000", "160000", "140000", "120000", "100000"], working_capital_change: ["1000", "1000", "1000", "1000", "1000"] },
      rationales: { revenue: "需求增长的条件假设", operating_margin: "规模效应的条件假设", cash_tax_rate: "现金税率假设", depreciation: "资产折旧假设", capex: "投入回落假设", working_capital_change: "营运资金需求假设" } })),
    discount_rate: "0.1", discount_rate_rationale: "资本成本假设", terminal_growth: "0.03", terminal_roic: "0.15", terminal_rationale: "长期增长与再投资假设", first_year_cash_flow_fraction: "0.5", timing_rationale: "首年剩余现金流假设" };
}
export function modelWorkspace(): CompanyFinancialModelWorkspace {
  const ref = { fact_key: "cash", source_url: "https://example.test/report", source_locator: "p. 2", raw_hash: modelHash, source_role: "filing" };
  return { project_id: modelIds.project, parent_revision_id: modelIds.revision, parent_manifest_hash: modelHash, cutoff_at: "2026-08-28T00:00:00Z",
    baseline: { content_hash: modelHash, sources: [{ id: "annual", title: "官方年度披露", url: "https://example.test/report", raw_content_hash: modelHash, available_at: "2026-02-05T00:00:00Z" }], facts: [
      { fact_key: "annual_revenue", label: "年度集团收入", value: "402836", unit: "USD million", period_start: "2025-01-01", period_end: "2025-12-31", scope: "group", state: "reported", source_ids: ["annual"], source_locator: "p. 2", quote: "Group revenue 402836", input_fact_keys: [] },
      { fact_key: "h1_cloud", label: "上半年云收入", value: "30000", unit: "USD million", period_start: "2026-01-01", period_end: "2026-06-30", scope: "google_cloud", state: "reported", source_ids: ["annual"], source_locator: "p. 3", quote: "Cloud revenue 30000", input_fact_keys: [] }], research_gaps: [{ key: "cloud_cashflow", label: "云分部现金流未披露", detail: "不得用集团现金流代替分部现金流。" }] },
    market: { capital_structure: { cash: "100000", debt: "20000", minority_interest: "0", investments: "0", pension_liabilities: "0", other_adjustments: "0", basic_shares: "12000", diluted_shares: "12100", source_ref: ref, capital_bridge_policy_version: "capital.v1", policy_ref: ref, policy_excluded_adjustments: [] }, securities: [{ security_external_key: "NASDAQ:GOOGL", listed_class_economic_units: "6000", conversion_ratio: "1", adr_ratio: "1", dividend_rights_per_unit: "1", market_price_usd: "200", usd_cny_rate: "7", rights_ref: ref, price_ref: ref }], usd_cny_rate: "7", fx_ref: ref, market_at: "2026-08-27T20:00:00Z", snapshot_bindings: [] }, initial_inputs: modelInputs(), latest: null, history: [] };
}
export function modelRecord(sequence = 1): CompanyFinancialModelRecord {
  const workspace = modelWorkspace();
  return { id: sequence === 1 ? modelIds.draft : "20000000-0000-4000-8000-000000000302", project_id: workspace.project_id, parent_revision_id: workspace.parent_revision_id, parent_manifest_hash: modelHash, cutoff_at: workspace.cutoff_at,
    sequence, created_at: "2026-09-10T00:00:00Z", status: "unreviewed", input_hash: modelHash, content_hash: modelHash, baseline: workspace.baseline, market: workspace.market, inputs: modelInputs(),
    result: { schema_version: "company-research.financial-model-result.v1", status: "unreviewed", valuation_date: workspace.cutoff_at, scenarios: modelInputs().scenarios.map((scenario) => ({ scenario_id: scenario.scenario_id, mechanism: scenario.mechanism, rows: scenario.paths.revenue.map((revenue, index) => ({ fiscal_year: 2026 + index, revenue, operating_margin: "0.3", operating_income: "135000", cash_tax_rate: "0.2", depreciation: "30000", capex: "180000", working_capital_change: "1000", fcff: "-43000" })), enterprise_value_usd_million: "2500000", terminal_value_share: "0.8", securities: [{ security_external_key: "NASDAQ:GOOGL", value_usd_per_share: "213.22", value_cny_per_share: "1492.54", market_price_usd: "200", value_price_gap_ratio: "0.0661" }] })), warnings: ["尚未经人工复核"], policies: ["终值按增长与再投资共同约束"] } };
}
