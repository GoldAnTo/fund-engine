import { afterEach, describe, expect, it, vi } from "vitest";
import { liveResearchDraftFixture } from "./companyResearchDraft.test-fixtures";

import {
  InvestmentResearchApi,
  InvestmentResearchRequestError,
} from "./investmentResearchApi";

const ids = {
  project: "00000000-0000-4000-8000-000000000001",
  company: "00000000-0000-4000-8000-000000000002",
  securityA: "00000000-0000-4000-8000-000000000003",
  securityB: "00000000-0000-4000-8000-000000000004",
  draft: "00000000-0000-4000-8000-000000000005",
  mandate: "00000000-0000-4000-8000-000000000006",
  scope: "00000000-0000-4000-8000-000000000007",
  agenda: "00000000-0000-4000-8000-000000000008",
  basis: "00000000-0000-4000-8000-000000000009",
  priceA: "00000000-0000-4000-8000-000000000010",
  priceB: "00000000-0000-4000-8000-000000000011",
  fx: "00000000-0000-4000-8000-000000000012",
  capital: "00000000-0000-4000-8000-000000000013",
  rightsA: "00000000-0000-4000-8000-000000000014",
  rightsB: "00000000-0000-4000-8000-000000000015",
  revision: "00000000-0000-4000-8000-000000000016",
  boundary: "00000000-0000-4000-8000-000000000017",
  manifest: "00000000-0000-4000-8000-000000000018",
  membershipA: "00000000-0000-4000-8000-000000000019",
  membershipB: "00000000-0000-4000-8000-000000000020",
};
const hash = "a".repeat(64);
const agendaHash = "57e7c6fda6962645939bf3b4ac9ece70be170d1170a2571252593c42a50ece73";
const now = "2026-08-24T00:00:00Z";
const companyResearchHash = "c".repeat(64);
const frozenMemoContent = "冻结判断 🧭\n";
const frozenMemoContentHash = "e57b3c088060c3f291f537ceb844947011577bbe81a3e2f3a6ba1f5f5ec0cc11";

function response(body: object, status = 200, requestId = "req-product"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": requestId },
  });
}

function projectBody(targets = [ids.securityA, ids.securityB]) {
  return { schema_version: "underwriting.v1", id: ids.project, primary_company_id: ids.company, target_security_ids: targets, company_identity: { schema_version: "underwriting.v1", object_id: ids.company, identity_version_id: ids.membershipA, canonical_name: "Company" }, security_identities: targets.map((objectId, index) => ({ schema_version: "underwriting.v1", object_id: objectId, identity_version_id: index === 0 ? ids.priceA : ids.priceB, canonical_name: index === 0 ? "Security A" : "Security B", symbol: index === 0 ? "AAA" : "BBB", exchange: "EX", share_class: "ordinary", trading_currency: "CNY" })), content_hash: hash, created_at: now };
}

function companyResearchPreviewBody() {
  return {
    schema_version: "underwriting.v1",
    company: { schema_version: "underwriting.v1", object_id: ids.company, external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    securities: [
      { schema_version: "underwriting.v1", object_id: ids.securityA, external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
      { schema_version: "underwriting.v1", object_id: ids.securityB, external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
    ],
    strategy_version: "company-research-default.v1",
    horizon_years: 5,
    base_currency: "CNY",
    required_return: "0.12",
    permanent_loss_limit: "0.25",
    cutoff_at: now,
    agenda: ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"].map((key) => ({ schema_version: "underwriting.v1", key, label: key })),
    preview_hash: companyResearchHash,
  };
}

function companyResearchProjectBody(status = "queued", currentStep: string | null = "evidence_index", progress = 0) {
  return {
    schema_version: "underwriting.v1",
    project_id: ids.project,
    company_id: ids.company,
    preparation: {
      schema_version: "underwriting.v1",
      id: ids.draft,
      project_id: ids.project,
      request_hash: companyResearchHash,
      strategy_version: "company-research-default.v1",
      status,
      current_step: currentStep,
      progress,
      attempt: 1,
      next_attempt_at: null,
      last_error_code: null,
    },
  };
}

function companyResearchJudgmentConfirmationBody() {
  return {
    schema_version: "underwriting.v1",
    project_id: ids.project,
    preparation: {
      schema_version: "underwriting.v1",
      id: ids.draft,
      status: "ready_to_freeze",
      current_step: "memo",
      progress: 95,
    },
    draft: { schema_version: "underwriting.v1", id: ids.draft, lock_version: 4 },
    machine_memo: { schema_version: "underwriting.v1", id: ids.rightsA, content_hash: companyResearchHash },
    confirmed_memo: { schema_version: "underwriting.v1", id: ids.rightsB, content_hash: hash },
    assessment_status: "not_answerable",
    reviewer: "human:local-user",
    markdown: "Frozen memo",
    confirmed_at: now,
  };
}

const frozenArtifactKinds = [
  "evidence_index",
  "research_gaps",
  "business_map",
  "driver_map",
  "financial_bridge",
  "scenario_set",
  "judgment_context",
  "memo",
] as const;

function companyResearchPublicationPreviewBody() {
  const artifactIds = [ids.agenda, ids.mandate, ids.scope, ids.basis, ids.fx, ids.boundary, ids.capital, ids.rightsB];
  return {
    schema_version: "underwriting.v1",
    project_id: ids.project,
    expected_lock_version: 4,
    company: {
      schema_version: "underwriting.v1",
      object_id: ids.company,
      external_key: "US:ALPHABET:COMPANY",
      canonical_name: "Alphabet Inc.",
    },
    securities: [
      { schema_version: "underwriting.v1", object_id: ids.securityA, external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD", company_id: ids.company },
      { schema_version: "underwriting.v1", object_id: ids.securityB, external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD", company_id: ids.company },
    ],
    cutoff_at: now,
    historical_basis_id: ids.basis,
    historical_basis_content_hash: hash,
    strategy_version: "company-research-default.v1",
    model_version: "company-research-model.v1",
    assessment: {
      schema_version: "underwriting.v1",
      answerability: "not_answerable",
      direction: null,
      confidence: null,
      content_hash: companyResearchHash,
    },
    value_range: null,
    return_range: null,
    blockers: ["missing_market_bridge"],
    strongest_counterevidence: [{ fact_key: "search_share", raw_hash: hash, source_locator: "p. 1", source_role: "regulatory_filing", source_url: "https://example.test/source" }],
    next_verification_events: ["Verify search share."],
    memo_markdown: "Frozen memo",
    artifacts: frozenArtifactKinds.map((kind, index) => ({
      schema_version: "underwriting.v1",
      kind,
      id: artifactIds[index],
      version: 1,
      input_hash: hash,
      content_hash: index === frozenArtifactKinds.length - 1 ? companyResearchHash : hash,
    })),
    manifest_hash: companyResearchHash,
  };
}

function companyResearchFrozenRevisionBody() {
  const preview = companyResearchPublicationPreviewBody();
  const { expected_lock_version: _expectedLockVersion, ...projection } = preview;
  return {
    ...projection,
    id: ids.revision,
    sequence: 1,
    published_at: now,
    boundary_id: ids.boundary,
    manifest_id: ids.manifest,
    preparation_status: "completed",
    current_step: null,
    progress: 100,
  };
}

function companyResearchMarkdownExportBody() {
  return {
    schema_version: "underwriting.v1",
    filename: `alphabet-company-research-${ids.revision}.md`,
    media_type: "text/markdown",
    content: frozenMemoContent,
    content_hash: frozenMemoContentHash,
  };
}

function companyResearchWorkspaceBody(): any {
  const artifact = {
    schema_version: "underwriting.v1", id: ids.agenda, project_id: ids.project, kind: "evidence_index", version: 1,
    input_hash: hash, content_hash: hash,
    payload: { fixture_content_hash: hash, cutoff: now, company_external_key: "US:ALPHABET:COMPANY", security_external_keys: ["NASDAQ:GOOG", "NASDAQ:GOOGL"], facts: [{ fact_key: "reported_revenue", company_external_key: "US:ALPHABET:COMPANY", business_module: "search", metric_key: "revenue", observation: { key: "revenue", value: "1", unit: "USD_million", currency: "USD", period: "2025-01-01/2025-12-31", state: "reported", source_ref: { kind: "external", fact_key: "reported_revenue", source_role: "regulatory_filing", source_url: "https://example.test/source", source_locator: "p. 1", raw_hash: hash }, gap_key: null, assumption_key: null }, period_start: "2025-01-01", period_end: "2025-12-31", published_at: now, available_at: now, source_role: "regulatory_filing", source_url: "https://example.test/source", source_locator: "p. 1", raw_hash: hash }] },
    source_refs: [{ source_url: "https://example.test/source", raw_hash: hash, source_locator: "p. 1", source_role: "regulatory_filing" }],
  };
  const gaps = {
    schema_version: "underwriting.v1", id: ids.mandate, project_id: ids.project, kind: "research_gaps", version: 1,
    input_hash: hash, content_hash: companyResearchHash,
    payload: { fixture_content_hash: hash, company_external_key: "US:ALPHABET:COMPANY", gaps: [] },
    source_refs: [{ source_url: "https://example.test/source", raw_hash: hash, source_locator: "p. 1", source_role: "regulatory_filing" }],
  };
  const evidenceRefs = [
    { id: artifact.id, kind: artifact.kind, content_hash: artifact.content_hash },
    { id: gaps.id, kind: gaps.kind, content_hash: gaps.content_hash },
  ];
  const keys = ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"];
  return {
    schema_version: "underwriting.v1", project_id: ids.project,
    company: { schema_version: "underwriting.v1", id: ids.company, object_id: ids.company, external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    preparation: { schema_version: "underwriting.v1", id: ids.draft, status: "awaiting_evidence_review", current_step: "research_gaps", progress: 25, error: null },
    artifacts: [artifact, gaps],
    modules: keys.map((key) => ({ schema_version: "underwriting.v1", key, state: key === "evidence_and_gaps" ? "needs_review" : "not_started", artifact_refs: key === "evidence_and_gaps" ? evidenceRefs : [], valuation_state: key === "scenarios_valuation_implied_expectations" ? "pending" : "not_applicable" })),
    source_count: 1, gap_count: 0,
    draft: { schema_version: "underwriting.v1", id: ids.draft, lock_version: 1, base_revision_id: null },
    selected_revision: null, change_summary: { artifact_versions: { evidence_index: 1, research_gaps: 1 }, reviewed_fact_count: 0 },
  };
}

function addReportedBusinessArtifact(workspace: any): any {
  const evidence = workspace.artifacts.find((item: any) => item.kind === "evidence_index");
  const fact = evidence.payload.facts[0]!;
  fact.review_decision = "confirmed";
  workspace.change_summary.reviewed_fact_count = 1;
  const source = { ...fact.observation.source_ref };
  delete source.kind;
  const business = {
    schema_version: "underwriting.v1", id: ids.scope, project_id: ids.project, kind: "business_map", version: 1,
    input_hash: hash, content_hash: agendaHash,
    payload: {
      modules: [{
        module_key: "search", revenue_sources: ["ads"], cost_structure: ["traffic"], capital_needs: ["data_centers"],
        fact_refs: [source], gap_refs: [],
        classified_evidence: [{ fact_ref: source, metric_key: "revenue", category: "revenue", observation: { ...fact.observation }, period_start: fact.period_start, period_end: fact.period_end }],
      }],
      _lineage: { artifact_refs: [{ artifact_id: evidence.id, artifact_kind: evidence.kind, content_hash: evidence.content_hash }], market_snapshot_ids: [], market_snapshot_bindings: [] },
    },
    source_refs: [{ source_url: "https://example.test/source", raw_hash: hash, source_locator: "p. 1", source_role: "regulatory_filing" }],
  };
  workspace.artifacts.push(business);
  workspace.change_summary.artifact_versions.business_map = 1;
  for (const module of workspace.modules.filter((item: any) => item.key === "business_map" || item.key === "industry_competition_regulation")) {
    module.state = "ready";
    module.artifact_refs = [{ id: business.id, kind: business.kind, content_hash: business.content_hash }];
  }
  return workspace;
}

function addScenarioArtifact(workspace: any): any {
  addReportedBusinessArtifact(workspace);
  const evidence = workspace.artifacts.find((item: any) => item.kind === "evidence_index");
  const business = workspace.artifacts.find((item: any) => item.kind === "business_map");
  const fact = evidence.payload.facts[0]!;
  const factRef = { ...fact.observation.source_ref };
  delete factRef.kind;
  const driver = {
    schema_version: "underwriting.v1", id: ids.basis, project_id: ids.project, kind: "driver_map", version: 1,
    input_hash: hash, content_hash: companyResearchHash,
    payload: {
      drivers: [{
        driver_key: "revenue", module_key: "search", fact_refs: [factRef], assumption_refs: [],
        equation: "revenue", output_metric: "revenue", equation_id: null,
        values: [{ ...fact.observation }], assumption_rationale: null, assumption_equation: null,
      }],
      _lineage: { artifact_refs: [{ artifact_id: business.id, artifact_kind: business.kind, content_hash: business.content_hash }], market_snapshot_ids: [], market_snapshot_bindings: [] },
    },
    source_refs: [...business.source_refs],
  };
  const scenario = {
    schema_version: "underwriting.v1", id: ids.boundary, project_id: ids.project, kind: "scenario_set", version: 1,
    input_hash: hash, content_hash: hash,
    payload: {
      scenarios: ["base", "bull", "bear"].map((scenarioId) => ({
        scenario_id: scenarioId, mechanism_id: `${scenarioId}.v1`,
        driver_overrides: ["revenue", "capex"].map((driverKey) => ({
          driver_key: driverKey,
          observation: { key: driverKey, value: "1", unit: "multiplier", currency: "N/A", period: "FY2026/FY2030", state: "assumption", source_ref: null, gap_key: null, assumption_key: `${scenarioId}.${driverKey}` },
          rationale: "scenario input", equation: null,
        })),
      })),
      _lineage: { artifact_refs: [{ artifact_id: driver.id, artifact_kind: driver.kind, content_hash: driver.content_hash }], market_snapshot_ids: [], market_snapshot_bindings: [] },
    },
    source_refs: [...driver.source_refs],
  };
  workspace.artifacts.push(driver, scenario);
  workspace.change_summary.artifact_versions.driver_map = 1;
  workspace.change_summary.artifact_versions.scenario_set = 1;
  Object.assign(workspace.modules.find((item: any) => item.key === "operating_drivers"), { state: "ready", artifact_refs: [{ id: driver.id, kind: driver.kind, content_hash: driver.content_hash }] });
  Object.assign(workspace.modules.find((item: any) => item.key === "scenarios_valuation_implied_expectations"), { state: "ready", valuation_state: "blocked", artifact_refs: [{ id: scenario.id, kind: scenario.kind, content_hash: scenario.content_hash }] });
  return workspace;
}

function addClosedModelArtifacts(workspace: any, includeDerivedGaps = true): any {
  addScenarioArtifact(workspace);
  const byKind = (kind: string) => workspace.artifacts.find((item: any) => item.kind === kind);
  const evidence = byKind("evidence_index");
  const sourceGaps = byKind("research_gaps");
  const business = byKind("business_map");
  const driver = byKind("driver_map");
  const scenario = byKind("scenario_set");
  const sourceRefs = [...evidence.source_refs];
  const assumption = (key: string) => ({
    key, value: "1", unit: "USD_million", currency: "USD", period: "FY2026",
    state: "assumption", source_ref: null, gap_key: null, assumption_key: `fixture.${key}`,
  });
  const financial = {
    schema_version: "underwriting.v1", id: ids.fx, project_id: ids.project, kind: "financial_bridge", version: 1,
    input_hash: hash, content_hash: hash,
    payload: {
      rows: Array.from({ length: 5 }, (_, index) => ({
        period: `FY${2026 + index}`,
        revenue: assumption("revenue"), operating_income: assumption("operating_income"),
        cash_tax_rate: { ...assumption("cash_tax_rate"), unit: "ratio", currency: "N/A" },
        depreciation: assumption("depreciation"), capex: assumption("capex"),
        working_capital_change: assumption("working_capital_change"), fcff: assumption("fcff"),
        fact_refs: [], assumption_refs: [],
      })),
      _lineage: { artifact_refs: [{ artifact_id: driver.id, artifact_kind: driver.kind, content_hash: driver.content_hash }], market_snapshot_ids: [], market_snapshot_bindings: [] },
    },
    source_refs: sourceRefs,
  };
  const parentArtifacts = [evidence, business, driver, financial, scenario, sourceGaps];
  const derivedGaps = [{ code: "verify_search_share", module_key: "search", severity: "high", message: "Verify search share." }];
  if (!includeDerivedGaps) {
    sourceGaps.payload = {
      gaps: derivedGaps,
      _lineage: {
        artifact_refs: parentArtifacts.slice(0, -1).map((item) => ({ artifact_id: item.id, artifact_kind: item.kind, content_hash: item.content_hash })),
        market_snapshot_ids: [], market_snapshot_bindings: [],
      },
    };
  }
  const judgment = {
    schema_version: "underwriting.v1", id: ids.capital, project_id: ids.project, kind: "judgment_context", version: 1,
    input_hash: hash, content_hash: agendaHash,
    payload: {
      operating_baseline_available: true, financial_bridge_closed: true, market_security_bridge_available: false,
      strongest_counterevidence: [], next_verification_events: [derivedGaps[0]!.message],
      _lineage: { artifact_refs: parentArtifacts.map((item) => ({ artifact_id: item.id, artifact_kind: item.kind, content_hash: item.content_hash })), market_snapshot_ids: [], market_snapshot_bindings: [] },
    },
    source_refs: sourceRefs,
  };
  const memoPayload: any = {
    assessment_status: "not_answerable",
    business_map_ref: { artifact_kind: "business_map", content_hash: business.content_hash },
    driver_map_ref: { artifact_kind: "driver_map", content_hash: driver.content_hash },
    financial_bridge_ref: { artifact_kind: "financial_bridge", content_hash: financial.content_hash },
    scenario_set_ref: { artifact_kind: "scenario_set", content_hash: scenario.content_hash },
    valuation_set_ref: null, gap_keys: [derivedGaps[0]!.code], strongest_counterevidence: [],
    next_verification_events: [derivedGaps[0]!.message], candidate_status: "machine_draft",
    _lineage: { artifact_refs: [{ artifact_id: judgment.id, artifact_kind: judgment.kind, content_hash: judgment.content_hash }], market_snapshot_ids: [], market_snapshot_bindings: [] },
  };
  const memo = {
    schema_version: "underwriting.v1", id: ids.rightsA, project_id: ids.project, kind: "memo", version: 1,
    input_hash: hash, content_hash: companyResearchHash, payload: memoPayload, source_refs: sourceRefs,
  };
  workspace.artifacts.push(financial, judgment, memo);
  Object.assign(workspace.change_summary.artifact_versions, { financial_bridge: 1, judgment_context: 1, memo: 1 });
  Object.assign(workspace.preparation, { status: "awaiting_judgment_review", current_step: "judgment_context", progress: 85 });
  const ref = (item: any) => ({ id: item.id, kind: item.kind, content_hash: item.content_hash });
  Object.assign(workspace.modules.find((item: any) => item.key === "overview"), { state: "ready", artifact_refs: [ref(judgment)] });
  Object.assign(workspace.modules.find((item: any) => item.key === "evidence_and_gaps"), { state: "ready" });
  Object.assign(workspace.modules.find((item: any) => item.key === "financials_cash_flow_capital_allocation"), { state: "ready", artifact_refs: [ref(financial)] });
  Object.assign(workspace.modules.find((item: any) => item.key === "counterevidence_risks_next_checks"), { state: "ready", artifact_refs: [ref(sourceGaps), ref(judgment)] });
  Object.assign(workspace.modules.find((item: any) => item.key === "versions_changes_memo"), { state: "ready", artifact_refs: [ref(memo)] });
  workspace.gap_count = derivedGaps.length;
  return workspace;
}

function confirmedCompanyResearchWorkspaceBody(markdown = "Frozen memo"): any {
  const workspace = addClosedModelArtifacts(companyResearchWorkspaceBody());
  const memo = workspace.artifacts.find((item: any) => item.kind === "memo");
  Object.assign(memo, { id: ids.rightsB, version: 2, content_hash: hash });
  Object.assign(memo.payload, {
    candidate_status: "human_confirmed",
    reviewer: "human:local-user",
    markdown,
  });
  Object.assign(workspace.preparation, { status: "ready_to_freeze", current_step: "memo", progress: 95 });
  workspace.draft.lock_version = 4;
  workspace.change_summary.artifact_versions.memo = 2;
  const memoModule = workspace.modules.find((item: any) => item.key === "versions_changes_memo");
  memoModule.artifact_refs = [{ id: memo.id, kind: memo.kind, content_hash: memo.content_hash }];
  return workspace;
}

function industryCompanyBrowseBody(items: object[] = [
  { schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null },
  { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
  { schema_version: "underwriting.v1", object_id: ids.securityB, kind: "security", external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
], industryId = ids.company) {
  return { schema_version: "underwriting.v1", industry_id: industryId, items };
}

function mandateBody() {
  return {
    schema_version: "underwriting.v1", id: ids.mandate, project_id: ids.project,
    mandate_key: `product.project:${ids.project}`, horizon_years: 3, base_currency: "CNY",
    required_return: "0.12", permanent_loss_limit: "0.25", comparison_set: ["peer"],
    benchmark_key: null, required_excess_return: null, effective_at: now, expires_at: null,
    version: 1, supersedes_id: null, content_hash: hash, created_at: now,
  };
}

function scopeBody(targets = [ids.securityA, ids.securityB]) {
  return {
    schema_version: "underwriting.v1", id: ids.scope, project_id: ids.project, version: 1,
    payload: { schema_version: "underwriting.v1", primary_company_id: ids.company, target_security_ids: targets, industry_ids: [], covered_segments: [], user_focus: null, exclusions: [] },
    supersedes_id: null, content_hash: hash, created_at: now,
  };
}

function agendaBody() {
  return {
    schema_version: "underwriting.v1", id: ids.agenda, project_id: ids.project, version: 1,
    scope_id: ids.scope, payload: { schema_version: "underwriting.v1", items: ["核验公司边界"] },
    generator_provenance: { schema_version: "underwriting.v1", method: "deterministic_template", template_key: "product.foundation.agenda", template_version: "1.0.0", model_name: null, prompt_template_version: null, input_summary_hash: hash, output_hash: agendaHash },
    supersedes_id: null, content_hash: hash, created_at: now,
  };
}

function basisBody() {
  return { schema_version: "underwriting.v1", id: ids.basis, cutoff_at: now, price_as_of: null, source_manifest_hash: hash, definition_bundle_hash: hash, parser_bundle_hash: hash, boundary_schema_version: "product.historical-basis.v1", content_hash: hash, created_at: now };
}

function effectiveRightsBody(effective = rightsBody()) {
  return {
    schema_version: "underwriting.v1",
    security_identity_id: ids.securityA,
    as_of: now,
    effective,
    head: {
      schema_version: "underwriting.v1",
      id: ids.rightsA,
      effective_from: effective.effective_from,
      effective_to: effective.effective_to,
    },
    append_allowed: false,
    expected_parent_id: null,
    minimum_effective_from: null,
    reason: {
      schema_version: "underwriting.v1",
      code: "effective_version_found",
      action: "reuse_effective",
    },
  };
}

function successorRightsBody() {
  return {
    ...effectiveRightsBody(),
    effective: null,
    head: {
      schema_version: "underwriting.v1",
      id: ids.rightsA,
      effective_from: "2026-08-20T00:00:00Z",
      effective_to: "2026-08-23T00:00:00Z",
    },
    append_allowed: true,
    expected_parent_id: ids.rightsA,
    minimum_effective_from: "2026-08-23T00:00:00Z",
    reason: {
      schema_version: "underwriting.v1",
      code: "successor_required",
      action: "append_successor",
    },
  };
}

function priceBody(id = ids.priceA, securityId = ids.securityA) {
  return { schema_version: "underwriting.v1", id, security_identity_id: securityId, price: "100", currency: "CNY", price_type: "close", adjustment_basis: "unadjusted", market_at: now, available_at: now, source_id: "source", raw_hash: hash, content_hash: hash, created_at: now };
}

function fxBody() {
  return { schema_version: "underwriting.v1", id: ids.fx, base_currency: "CNY", quote_currency: "USD", rate: "0.14", quote_direction: "quote_per_base", market_at: now, available_at: now, source_id: "source", raw_hash: hash, content_hash: hash, created_at: now };
}

function capitalBody() {
  return { schema_version: "underwriting.v1", id: ids.capital, company_id: ids.company, currency: "CNY", cash: "100", debt: "40", minority_interest: "5", investments: "10", pension_liabilities: "0", other_adjustments: "0", basic_shares: "1000", diluted_shares: "1010", potential_dilution_descriptors: [], report_period_start: now, report_period_end: now, market_at: now, available_at: now, source_id: "source", raw_hash: hash, content_hash: hash, created_at: now };
}

function rightsBody(id = ids.rightsA, securityId = ids.securityA) {
  return { schema_version: "underwriting.v1", id, security_identity_id: securityId, version: 1, economic_units: "1", votes_per_unit: "1", conversion_ratio: "1", adr_ratio: "1", dividend_rights_per_unit: "1", effective_from: now, effective_to: null, source_id: "source", raw_hash: hash, supersedes_id: null, content_hash: hash, created_at: now };
}

function draftBody(overrides: Record<string, object | string | number | null> = {}) {
  return {
    schema_version: "underwriting.v1", id: ids.draft, project_id: ids.project, base_revision_id: null, lock_version: 2,
    content: { schema_version: "underwriting.v1", publication_status: "draft", mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, historical_basis_id: ids.basis, price_snapshot_ids: [ids.priceB, ids.priceA], fx_snapshot_ids: [ids.fx], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsB, ids.rightsA], user_focus: null },
    created_at: now, updated_at: now, ...overrides,
  };
}

function previewBody() {
  return {
    schema_version: "underwriting.v1", project_id: ids.project, expected_lock_version: 2, boundary_as_of: now,
    assessment: { schema_version: "underwriting.v1", answerability: "not_answerable", direction: null, confidence: null, publication_status: "user_frozen", blockers: ["missing_key_baseline"], resolution_requirements: ["补齐关键基线"], next_review_at: null, parent_assessment_id: null, content_hash: hash },
    boundary: { schema_version: "underwriting.v1", historical_basis_id: ids.basis, mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, price_snapshot_ids: [ids.priceA, ids.priceB], fx_snapshot_ids: [ids.fx], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsA, ids.rightsB], parent_revision_id: null },
    boundary_hash: hash,
    manifest: {
      schema_version: "underwriting.research-revision-manifest.v1", project_id: ids.project,
      project_ref: { project_id: ids.project, content_hash: hash },
      project_membership_refs: [
        { membership_id: ids.membershipA, security_id: ids.securityA, content_hash: hash },
        { membership_id: ids.membershipB, security_id: ids.securityB, content_hash: hash },
      ],
      primary_object_id: ids.company, boundary_ref: "$boundary", mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, historical_basis_id: ids.basis,
      price_snapshot_ids: [ids.priceA, ids.priceB], fx_snapshot_ids: [ids.fx], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsA, ids.rightsB], market_snapshot_refs: [`price:${ids.priceA}`, `price:${ids.priceB}`, `fx:${ids.fx}`, `capital_structure:${ids.capital}`, `security_rights:${ids.rightsA}`, `security_rights:${ids.rightsB}`], model_refs: [], assessment_ref: "$assessment", memo_ref: null, parent_revision_id: null,
    },
    manifest_hash: hash,
  };
}

function revisionBody() {
  return { schema_version: "underwriting.v1", id: ids.revision, project_id: ids.project, object_id: ids.company, basis_id: ids.basis, boundary_id: ids.boundary, manifest_id: ids.manifest, version_kind: "independent_research", sequence: 1, content_hash: hash, cutoff: now, source_manifest_hash: hash, manifest_hash: hash, parent_revision_id: null, price_snapshot_ids: [ids.priceA, ids.priceB], fx_snapshot_ids: [ids.fx], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsA, ids.rightsB], market_snapshot_ids: [ids.priceB, ids.rightsA, ids.fx, ids.priceA, ids.capital, ids.rightsB], answerability: "not_answerable", direction: null, confidence: null, publication_status: "user_frozen" };
}

const projectRequest = { schema_version: "underwriting.v1" as const, primary_company_id: ids.company, target_security_ids: [ids.securityA, ids.securityB] };
const mandateRequest = { schema_version: "underwriting.v1" as const, horizon_years: 3, base_currency: "CNY" as const, required_return: "0.12", permanent_loss_limit: "0.25", comparison_set: ["peer"], benchmark_key: null, required_excess_return: null, effective_at: now, expires_at: null, expected_parent_id: null };
const scopeRequest = { schema_version: "underwriting.v1" as const, primary_company_id: ids.company, target_security_ids: [ids.securityA, ids.securityB], industry_ids: [], covered_segments: [], user_focus: null, exclusions: [], expected_parent_id: null };
const agendaRequest = { schema_version: "underwriting.v1" as const, scope_id: ids.scope, items: ["核验公司边界"], generator: { schema_version: "underwriting.v1" as const, method: "deterministic_template" as const, template_key: "product.foundation.agenda", template_version: "1.0.0", model_name: null, prompt_template_version: null, input_summary_hash: hash, output_hash: agendaHash }, expected_parent_id: null };
const aiAgendaRequest = { schema_version: "underwriting.v1" as const, scope_id: ids.scope, items: ["核验公司边界"], generator: { schema_version: "underwriting.v1" as const, method: "ai_generated" as const, template_key: null, template_version: null, model_name: "agenda-model", prompt_template_version: "prompt-v1", input_summary_hash: hash, output_hash: agendaHash }, expected_parent_id: null };
const basisRequest = { schema_version: "underwriting.v1" as const, cutoff_at: now, source_manifest_hash: hash, definition_bundle_hash: hash, parser_bundle_hash: hash };
const priceRequest = { schema_version: "underwriting.v1" as const, security_identity_id: ids.securityA, price: "100", currency: "CNY" as const, price_type: "close", adjustment_basis: "unadjusted", market_at: now, available_at: now, source_id: "source", raw_hash: hash };
const fxRequest = { schema_version: "underwriting.v1" as const, base_currency: "CNY" as const, quote_currency: "USD" as const, rate: "0.14", quote_direction: "quote_per_base" as const, market_at: now, available_at: now, source_id: "source", raw_hash: hash };
const capitalRequest = { schema_version: "underwriting.v1" as const, company_id: ids.company, currency: "CNY" as const, cash: "100", debt: "40", minority_interest: "5", investments: "10", pension_liabilities: "0", other_adjustments: "0", basic_shares: "1000", diluted_shares: "1010", potential_dilution_descriptors: [], report_period_start: now, report_period_end: now, market_at: now, available_at: now, source_id: "source", raw_hash: hash };
const rightsRequest = { schema_version: "underwriting.v1" as const, security_identity_id: ids.securityA, economic_units: "1", votes_per_unit: "1", conversion_ratio: "1", adr_ratio: "1", dividend_rights_per_unit: "1", effective_from: now, effective_to: null, source_id: "source", raw_hash: hash, expected_parent_id: null };
const patchRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 1, mandate_id: ids.mandate, scope_id: ids.scope, agenda_id: ids.agenda, historical_basis_id: ids.basis, price_snapshot_ids: [ids.priceA, ids.priceB], fx_snapshot_ids: [ids.fx], capital_structure_snapshot_id: ids.capital, security_rights_ids: [ids.rightsA, ids.rightsB], user_focus: null };

type OperationCase = {
  name: string;
  status: number;
  body: object;
  method: "GET" | "POST" | "PATCH";
  url: string;
  run: (api: InvestmentResearchApi) => Promise<unknown>;
  idempotencyKey?: string;
};

const operationCases: OperationCase[] = [
  { name: "search objects", status: 200, body: { schema_version: "underwriting.v1", items: [] }, method: "GET", url: "/api/underwriting/v1/product/objects?query=CATL&limit=20", run: (api) => api.searchObjects("CATL") },
  { name: "list projects", status: 200, body: { schema_version: "underwriting.v1", items: [projectBody()] }, method: "GET", url: "/api/underwriting/v1/product/projects?limit=20", run: (api) => api.projects() },
  { name: "create project", status: 201, body: projectBody([ids.securityB, ids.securityA]), method: "POST", url: "/api/underwriting/v1/product/projects", run: (api) => api.createProject(projectRequest) },
  { name: "get project", status: 200, body: projectBody(), method: "GET", url: `/api/underwriting/v1/product/projects/${ids.project}`, run: (api) => api.project(ids.project) },
  { name: "create mandate", status: 201, body: mandateBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/mandates`, run: (api) => api.createMandate(ids.project, mandateRequest) },
  { name: "create scope", status: 201, body: scopeBody([ids.securityB, ids.securityA]), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/scopes`, run: (api) => api.createScope(ids.project, scopeRequest) },
  { name: "create agenda", status: 201, body: agendaBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/agendas`, run: (api) => api.createAgenda(ids.project, agendaRequest) },
  { name: "create basis", status: 201, body: basisBody(), method: "POST", url: "/api/underwriting/v1/product/historical-bases", run: (api) => api.createHistoricalBasis(basisRequest) },
  { name: "create price", status: 201, body: priceBody(), method: "POST", url: "/api/underwriting/v1/product/market/price-snapshots", run: (api) => api.createPriceSnapshot(priceRequest) },
  { name: "create FX", status: 201, body: fxBody(), method: "POST", url: "/api/underwriting/v1/product/market/fx-snapshots", run: (api) => api.createFxSnapshot(fxRequest) },
  { name: "create capital", status: 201, body: capitalBody(), method: "POST", url: "/api/underwriting/v1/product/market/capital-structure-snapshots", run: (api) => api.createCapitalStructure(capitalRequest) },
  { name: "create rights", status: 201, body: rightsBody(), method: "POST", url: "/api/underwriting/v1/product/market/security-rights", run: (api) => api.createSecurityRights(rightsRequest) },
  { name: "get effective rights", status: 200, body: effectiveRightsBody(), method: "GET", url: `/api/underwriting/v1/product/market/security-rights/effective?security_identity_id=${ids.securityA}&as_of=2026-08-24T00%3A00%3A00Z`, run: (api) => api.effectiveSecurityRights(ids.securityA, now) },
  { name: "get draft", status: 200, body: draftBody(), method: "GET", url: `/api/underwriting/v1/product/projects/${ids.project}/draft`, run: (api) => api.draft(ids.project) },
  { name: "patch draft", status: 200, body: draftBody(), method: "PATCH", url: `/api/underwriting/v1/product/projects/${ids.project}/draft`, run: (api) => api.saveDraft(ids.project, patchRequest) },
  { name: "preview", status: 200, body: previewBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/publication-preview`, run: (api) => api.preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 }) },
  { name: "publish", status: 201, body: revisionBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/publish`, run: (api) => api.publish(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 }, "publish-key"), idempotencyKey: "publish-key" },
  { name: "get revision", status: 200, body: revisionBody(), method: "GET", url: `/api/underwriting/v1/product/revisions/${ids.revision}`, run: (api) => api.revision(ids.revision) },
];

describe("InvestmentResearchApi", () => {
  it("reads a cited live draft before evidence review and rejects substituted sources or focus", async () => {
    const body = companyResearchWorkspaceBody();
    body.product_progress = { schema_version: "underwriting.v1", run_id: ids.draft, project_id: ids.project, company_id: ids.company,
      status: "needs_input", current_step: "research_gaps", progress_percent: 25, user_focus: "云业务", cutoff_at: now, retryable: false, error_code: null };
    body.research_draft = liveResearchDraftFixture(ids.project, ids.draft, now, "云业务");
    const api = new InvestmentResearchApi("https://api.test");
    const fetchMock = vi.fn().mockResolvedValue(response(body));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ research_draft: { markdown: body.research_draft.markdown } });
    for (const change of [
      (value: any) => { value.research_draft.user_focus = "替换的问题"; },
      (value: any) => { value.research_draft.sections[0].items[0].citations[0].raw_hash = hash; },
      (value: any) => { value.research_draft.usage.attempts[0].total_tokens = 1; },
    ]) {
      const invalid = structuredClone(body); change(invalid);
      fetchMock.mockResolvedValue(response(invalid));
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each(operationCases)("enforces the $name wire contract", async (operation) => {
    const fetchSpy = vi.fn<typeof fetch>(async () => response(operation.body, operation.status));
    vi.stubGlobal("fetch", fetchSpy);
    await operation.run(new InvestmentResearchApi());
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0]!;
    expect(url).toBe(operation.url);
    expect(init).toMatchObject({ method: operation.method, credentials: "include" });
    if (operation.method === "GET") expect(init?.body).toBeUndefined();
    else {
      expect(init?.headers).toMatchObject({ "content-type": "application/json" });
      expect(typeof init?.body).toBe("string");
    }
    if (operation.idempotencyKey) expect(init?.headers).toMatchObject({ "Idempotency-Key": operation.idempotencyKey });
  });

  it("accepts uniquely identified, ordered Company and Security industry browse results", async () => {
    const fetchSpy = vi.fn<typeof fetch>(async () => response(industryCompanyBrowseBody()));
    vi.stubGlobal("fetch", fetchSpy);

    await expect(new InvestmentResearchApi().industryCompanies(ids.company, { asOf: now, limit: 3 }))
      .resolves.toMatchObject({ industry_id: ids.company, items: [{ object_id: ids.company }, { object_id: ids.securityA }, { object_id: ids.securityB }] });
    expect(fetchSpy).toHaveBeenCalledWith(
      `/api/underwriting/v1/product/industries/${ids.company}/companies?as_of=2026-08-24T00%3A00%3A00Z&limit=3`,
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });

  it("accepts a lowercase response industry UUID for an uppercase request UUID", async () => {
    const lowercaseIndustryId = "a0000000-0000-4000-8000-000000000001";
    vi.stubGlobal("fetch", vi.fn(async () => response(industryCompanyBrowseBody(undefined, lowercaseIndustryId))));

    await expect(new InvestmentResearchApi().industryCompanies(lowercaseIndustryId.toUpperCase()))
      .resolves.toMatchObject({ industry_id: lowercaseIndustryId });
  });

  it("accepts a browseable Company without an effective Security", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(industryCompanyBrowseBody([
      { schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null },
    ]))));

    await expect(new InvestmentResearchApi().industryCompanies(ids.company))
      .resolves.toMatchObject({ items: [{ object_id: ids.company, kind: "company" }] });
  });

  it("accepts an empty Company group before a complete Company and Security group", async () => {
    const companyWithoutSecurity = "00000000-0000-4000-8000-000000000021";
    vi.stubGlobal("fetch", vi.fn(async () => response(industryCompanyBrowseBody([
      { schema_version: "underwriting.v1", object_id: companyWithoutSecurity, kind: "company", external_key: "US:PRIVATE:COMPANY", canonical_name: "Private Company", symbol: null, exchange: null, share_class: null, trading_currency: null },
      { schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null },
      { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
    ]))));

    await expect(new InvestmentResearchApi().industryCompanies(ids.company))
      .resolves.toMatchObject({ items: [{ object_id: companyWithoutSecurity }, { object_id: ids.company }, { object_id: ids.securityA }] });
  });

  it("rejects an invalid industry identity locally without making a request", async () => {
    const fetchSpy = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchSpy);

    await expect(new InvestmentResearchApi().industryCompanies("not-a-uuid"))
      .rejects.toMatchObject({ code: "invalid_request" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects a complete Company and Security group bound to another industry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(industryCompanyBrowseBody(undefined, ids.membershipA))));

    await expect(new InvestmentResearchApi().industryCompanies(ids.company))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each([
    ["an industry item", industryCompanyBrowseBody([{ schema_version: "underwriting.v1", object_id: ids.company, kind: "industry", external_key: "INDUSTRY:INTERNET", canonical_name: "Internet", symbol: null, exchange: null, share_class: null, trading_currency: null }])],
    ["a security before its Company", industryCompanyBrowseBody([{ schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" }])],
    ["a duplicate object", industryCompanyBrowseBody([{ schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null }, { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" }, { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" }])],
    ["an extra response field", (() => { const body = industryCompanyBrowseBody(); Object.assign(body, { unexpected: true }); return body; })()],
    ["an extra identity field", (() => { const body = industryCompanyBrowseBody(); Object.assign(body.items[0]!, { identity_version_id: ids.membershipA }); return body; })()],
    ["a malformed UUID", industryCompanyBrowseBody([{ schema_version: "underwriting.v1", object_id: "not-a-uuid", kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null }, { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" }])],
    ["a split Company group", industryCompanyBrowseBody([{ schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null }, { schema_version: "underwriting.v1", object_id: ids.securityA, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" }, { schema_version: "underwriting.v1", object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null }, { schema_version: "underwriting.v1", object_id: ids.securityB, kind: "security", external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" }])],
  ])("rejects an invalid industry browse response: %s", async (_name, body) => {
    vi.stubGlobal("fetch", vi.fn(async () => response(body)));
    await expect(new InvestmentResearchApi().industryCompanies(ids.company)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts canonical focus echoed by the company preview", async () => {
    const body = { ...companyResearchPreviewBody(), user_focus: "Café 现金流" };
    vi.stubGlobal("fetch", vi.fn(async () => response(body)));
    await expect(new InvestmentResearchApi().previewCompanyResearch({
      schema_version: "underwriting.v1", company_id: ids.company, cutoff_at: now,
      user_focus: "  Cafe\u0301 现金流  ",
    })).resolves.toMatchObject({ user_focus: "Café 现金流" });
  });

  it("rejects a legacy preview that silently drops a submitted focus", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(companyResearchPreviewBody())));
    await expect(new InvestmentResearchApi().previewCompanyResearch({
      schema_version: "underwriting.v1", company_id: ids.company, cutoff_at: now,
      user_focus: "AI 资本开支",
    })).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("accepts only a request-bound effective cutoff in company previews", async () => {
    const requested = "2026-09-09T00:00:00Z";
    const preview = { ...companyResearchPreviewBody(), requested_cutoff_at: requested };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response({ ...preview, requested_cutoff_at: now }))
      .mockResolvedValueOnce(response({ ...preview, cutoff_at: "2027-01-01T00:00:00Z" })));
    const request = { schema_version: "underwriting.v1" as const, company_id: ids.company, cutoff_at: requested };
    const api = new InvestmentResearchApi();
    await expect(api.previewCompanyResearch(request)).resolves.toMatchObject({ cutoff_at: now });
    await expect(api.previewCompanyResearch(request)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.previewCompanyResearch(request)).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("validates the same product progress on small company status responses", async () => {
    const status = companyResearchProjectBody();
    const progress = {
      schema_version: "underwriting.v1", run_id: status.preparation.id, project_id: ids.project,
      company_id: ids.company, status: "queued", current_step: "evidence_index", progress_percent: 0,
      user_focus: null, cutoff_at: now, retryable: false, error_code: null,
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({ ...status, product_progress: progress }))
      .mockResolvedValueOnce(response({ ...status, product_progress: { ...progress, run_id: ids.revision } })));
    const api = new InvestmentResearchApi();
    await expect(api.companyResearchProject(ids.project)).resolves.toMatchObject({ product_progress: { status: "queued" } });
    await expect(api.companyResearchProject(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts server product progress and rejects identity, lifecycle and error substitutions", async () => {
    const workspace = companyResearchWorkspaceBody();
    workspace.product_progress = {
      schema_version: "underwriting.v1", run_id: ids.draft, project_id: ids.project,
      company_id: ids.company, status: "needs_input", current_step: "research_gaps",
      progress_percent: 25, user_focus: "AI 资本开支", cutoff_at: now,
      retryable: false, error_code: null,
    };
    const mutations = [
      { run_id: ids.revision }, { project_id: ids.revision }, { company_id: ids.revision },
      { status: "completed" }, { status: "invented" }, { current_step: "memo" },
      { progress_percent: 26 }, { progress_percent: -1 }, { progress_percent: "25" },
      { retryable: true }, { error_code: "invented_failure" }, { cutoff_at: "invalid" },
      { user_focus: "  not canonical " }, { user_focus: "x".repeat(2001) }, { extra: true },
    ];
    const fetchSpy = vi.fn().mockResolvedValueOnce(response(workspace));
    for (const mutation of mutations) {
      fetchSpy.mockResolvedValueOnce(response({ ...workspace, product_progress: { ...workspace.product_progress, ...mutation } }));
    }
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({
      product_progress: { status: "needs_input", user_focus: "AI 资本开支" },
    });
    for (const _mutation of mutations) {
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
  });

  it("uses only high-level company-research routes and binds initialization to its preview", async () => {
    const api = new InvestmentResearchApi();
    const previewRequest = { schema_version: "underwriting.v1" as const, company_id: ids.company, cutoff_at: now };
    const preview = companyResearchPreviewBody();
    const initializeRequest = { ...previewRequest, preview_hash: companyResearchHash };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response(companyResearchProjectBody(), 201))
      .mockResolvedValueOnce(response(companyResearchProjectBody(), 200))
      .mockResolvedValueOnce(response(companyResearchProjectBody(), 202));
    vi.stubGlobal("fetch", fetchSpy);

    await expect(api.previewCompanyResearch(previewRequest)).resolves.toMatchObject({ preview_hash: companyResearchHash });
    await expect(api.initializeCompanyResearch(initializeRequest, "company-research-key")).resolves.toMatchObject({ project_id: ids.project });
    await expect(api.companyResearchProject(ids.project)).resolves.toMatchObject({ project_id: ids.project });
    await expect(api.retryCompanyResearchProject(ids.project)).resolves.toMatchObject({ project_id: ids.project });

    expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/underwriting/v1/product/company-research/preview", "POST"],
      ["/api/underwriting/v1/product/company-research/initializations", "POST"],
      [`/api/underwriting/v1/product/company-research/projects/${ids.project}`, "GET"],
      [`/api/underwriting/v1/product/company-research/projects/${ids.project}/retry`, "POST"],
    ]);
    expect(fetchSpy.mock.calls[1]![1]!?.headers).toMatchObject({ "Idempotency-Key": "company-research-key" });
  });

  it("uses the five high-level company research publication routes and sends idempotency only for publish", async () => {
    const api = new InvestmentResearchApi();
    const confirmationRequest = {
      schema_version: "underwriting.v1" as const,
      expected_lock_version: 3,
      expected_memo_id: ids.rightsA,
      expected_memo_content_hash: companyResearchHash,
      markdown: "Frozen memo",
    };
    const previewRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4 };
    const publishRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4, expected_manifest_hash: companyResearchHash };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(companyResearchJudgmentConfirmationBody()))
      .mockResolvedValueOnce(response(companyResearchPublicationPreviewBody()))
      .mockResolvedValueOnce(response(companyResearchFrozenRevisionBody(), 201))
      .mockResolvedValueOnce(response(companyResearchFrozenRevisionBody()))
      .mockResolvedValueOnce(response(companyResearchMarkdownExportBody()));
    vi.stubGlobal("fetch", fetchSpy);

    await api.confirmCompanyResearchJudgment(ids.project, confirmationRequest);
    await api.previewCompanyResearchPublication(ids.project, previewRequest);
    await api.publishCompanyResearch(ids.project, publishRequest, "alphabet-live-freeze");
    await api.companyResearchRevision(ids.project, ids.revision);
    await api.exportCompanyResearchRevision(ids.project, ids.revision);

    const root = "/api/underwriting/v1/product/company-research/projects";
    expect(fetchSpy.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      [`${root}/${ids.project}/judgment-confirmations`, "POST"],
      [`${root}/${ids.project}/publication-preview`, "POST"],
      [`${root}/${ids.project}/publish`, "POST"],
      [`${root}/${ids.project}/revisions/${ids.revision}`, "GET"],
      [`${root}/${ids.project}/revisions/${ids.revision}/export`, "GET"],
    ]);
    expect(fetchSpy.mock.calls[0]![1]!?.headers).not.toHaveProperty("Idempotency-Key");
    expect(fetchSpy.mock.calls[1]![1]!?.headers).not.toHaveProperty("Idempotency-Key");
    expect(fetchSpy.mock.calls[2]![1]!?.headers).toMatchObject({ "Idempotency-Key": "alphabet-live-freeze" });
    expect(fetchSpy.mock.calls[3]![1]!?.headers).toBeUndefined();
    expect(fetchSpy.mock.calls[4]![1]!?.headers).toBeUndefined();
  });

  it("accepts the persisted publication manifest hash after the preview hash is consumed", async () => {
    const revision = companyResearchFrozenRevisionBody();
    revision.manifest_hash = hash;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(revision, 201)));

    await expect(new InvestmentResearchApi().publishCompanyResearch(
      ids.project,
      {
        schema_version: "underwriting.v1",
        expected_lock_version: 4,
        expected_manifest_hash: companyResearchHash,
      },
      "persisted-manifest-hash",
    )).resolves.toMatchObject({
      project_id: ids.project,
      manifest_hash: hash,
    });
  });

  it("reads the ready-to-freeze workspace with its confirmed memo after judgment confirmation", async () => {
    const confirmationRequest = {
      schema_version: "underwriting.v1" as const,
      expected_lock_version: 3,
      expected_memo_id: ids.rightsA,
      expected_memo_content_hash: companyResearchHash,
      markdown: "Frozen memo",
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(companyResearchJudgmentConfirmationBody()))
      .mockResolvedValueOnce(response(confirmedCompanyResearchWorkspaceBody())));
    const api = new InvestmentResearchApi();

    await expect(api.confirmCompanyResearchJudgment(ids.project, confirmationRequest))
      .resolves.toMatchObject({ preparation: { status: "ready_to_freeze", progress: 95 } });
    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({
      preparation: { status: "ready_to_freeze", current_step: "memo", progress: 95 },
      artifacts: expect.arrayContaining([
        expect.objectContaining({
          kind: "memo",
          payload: expect.objectContaining({
            candidate_status: "human_confirmed",
            reviewer: "human:local-user",
            markdown: "Frozen memo",
          }),
        }),
      ]),
    });
  });

  it("rejects open or malformed machine and confirmed memo payloads", async () => {
    const machineWithHumanFields = addClosedModelArtifacts(companyResearchWorkspaceBody());
    Object.assign(machineWithHumanFields.artifacts.find((item: any) => item.kind === "memo").payload, {
      reviewer: "human:local-user",
      markdown: "Frozen memo",
    });
    const missingReviewer = confirmedCompanyResearchWorkspaceBody();
    Reflect.deleteProperty(missingReviewer.artifacts.find((item: any) => item.kind === "memo").payload, "reviewer");
    const wrongReviewer = confirmedCompanyResearchWorkspaceBody();
    wrongReviewer.artifacts.find((item: any) => item.kind === "memo").payload.reviewer = "human:other";
    const unnormalized = confirmedCompanyResearchWorkspaceBody("  Frozen memo\r\n");
    const blank = confirmedCompanyResearchWorkspaceBody(" \n ");
    const oversized = confirmedCompanyResearchWorkspaceBody("x".repeat(100_001));
    const extra = confirmedCompanyResearchWorkspaceBody();
    Object.assign(extra.artifacts.find((item: any) => item.kind === "memo").payload, { unexpected: true });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(machineWithHumanFields))
      .mockResolvedValueOnce(response(missingReviewer))
      .mockResolvedValueOnce(response(wrongReviewer))
      .mockResolvedValueOnce(response(unnormalized))
      .mockResolvedValueOnce(response(blank))
      .mockResolvedValueOnce(response(oversized))
      .mockResolvedValueOnce(response(extra)));
    const api = new InvestmentResearchApi();

    for (let index = 0; index < 7; index += 1) {
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
  });

  it("normalizes UUID identity comparisons for company research publication", async () => {
    const projectId = "a0000000-0000-4000-8000-000000000001";
    const memoId = "b0000000-0000-4000-8000-000000000002";
    const revisionId = "c0000000-0000-4000-8000-000000000003";
    const confirmation = companyResearchJudgmentConfirmationBody();
    confirmation.project_id = projectId;
    confirmation.machine_memo.id = memoId;
    const preview = companyResearchPublicationPreviewBody();
    preview.project_id = projectId;
    const revision = companyResearchFrozenRevisionBody();
    revision.project_id = projectId;
    revision.id = revisionId;
    const exported = companyResearchMarkdownExportBody();
    exported.filename = `alphabet-company-research-${revisionId}.md`;
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(confirmation))
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response(revision, 201))
      .mockResolvedValueOnce(response(revision))
      .mockResolvedValueOnce(response(exported)));
    const api = new InvestmentResearchApi();
    const confirmationRequest = {
      schema_version: "underwriting.v1" as const,
      expected_lock_version: 3,
      expected_memo_id: memoId.toUpperCase(),
      expected_memo_content_hash: companyResearchHash,
      markdown: "Frozen memo",
    };
    const previewRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4 };
    const publishRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4, expected_manifest_hash: companyResearchHash };

    await expect(api.confirmCompanyResearchJudgment(projectId.toUpperCase(), confirmationRequest)).resolves.toMatchObject({ project_id: projectId });
    await expect(api.previewCompanyResearchPublication(projectId.toUpperCase(), previewRequest)).resolves.toMatchObject({ project_id: projectId });
    await expect(api.publishCompanyResearch(projectId.toUpperCase(), publishRequest, "case-key")).resolves.toMatchObject({ project_id: projectId });
    await expect(api.companyResearchRevision(projectId.toUpperCase(), revisionId.toUpperCase())).resolves.toMatchObject({ id: revisionId });
    await expect(api.exportCompanyResearchRevision(projectId.toUpperCase(), revisionId.toUpperCase())).resolves.toMatchObject({ filename: exported.filename });
  });

  it("rejects malformed and mismatched company research publication envelopes", async () => {
    const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
    const confirmationRequest = {
      schema_version: "underwriting.v1" as const,
      expected_lock_version: 3,
      expected_memo_id: ids.rightsA,
      expected_memo_content_hash: companyResearchHash,
      markdown: "Frozen memo",
    };
    const previewRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4 };
    const publishRequest = { schema_version: "underwriting.v1" as const, expected_lock_version: 4, expected_manifest_hash: companyResearchHash };

    const wrongConfirmationProject = clone(companyResearchJudgmentConfirmationBody());
    wrongConfirmationProject.project_id = ids.company;
    const wrongConfirmationStatus = clone(companyResearchJudgmentConfirmationBody());
    wrongConfirmationStatus.preparation.status = "completed";
    const extraConfirmationMemo = clone(companyResearchJudgmentConfirmationBody());
    Object.assign(extraConfirmationMemo.confirmed_memo, { unexpected: true });
    const uppercaseConfirmationHash = clone(companyResearchJudgmentConfirmationBody());
    uppercaseConfirmationHash.confirmed_memo.content_hash = "A".repeat(64);

    const wrongPreviewProject = clone(companyResearchPublicationPreviewBody());
    wrongPreviewProject.project_id = ids.company;
    const wrongPreviewLock = clone(companyResearchPublicationPreviewBody());
    wrongPreviewLock.expected_lock_version = 5;
    const extraPreview = clone(companyResearchPublicationPreviewBody());
    Object.assign(extraPreview, { unexpected: true });
    const uppercasePreviewHash = clone(companyResearchPublicationPreviewBody());
    uppercasePreviewHash.manifest_hash = "C".repeat(64);
    const openNotAnswerable: any = clone(companyResearchPublicationPreviewBody());
    openNotAnswerable.assessment.direction = "provisional_bullish";
    const rangedNotAnswerable: any = clone(companyResearchPublicationPreviewBody());
    rangedNotAnswerable.value_range = { schema_version: "underwriting.v1", minimum: "100", maximum: "120", currency: "USD" };
    const reorderedArtifacts = clone(companyResearchPublicationPreviewBody());
    reorderedArtifacts.artifacts.reverse();
    const duplicateArtifact = clone(companyResearchPublicationPreviewBody());
    duplicateArtifact.artifacts[1]!.id = duplicateArtifact.artifacts[0]!.id;

    const wrongPublishedProject = clone(companyResearchFrozenRevisionBody());
    wrongPublishedProject.project_id = ids.company;
    const wrongRevisionProject = clone(companyResearchFrozenRevisionBody());
    wrongRevisionProject.project_id = ids.company;
    const wrongRevisionId = clone(companyResearchFrozenRevisionBody());
    wrongRevisionId.id = ids.manifest;
    const wrongRevisionStatus = clone(companyResearchFrozenRevisionBody());
    wrongRevisionStatus.preparation_status = "ready_to_freeze";
    const extraRevision = clone(companyResearchFrozenRevisionBody());
    Object.assign(extraRevision, { unexpected: true });

    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(wrongConfirmationProject))
      .mockResolvedValueOnce(response(wrongConfirmationStatus))
      .mockResolvedValueOnce(response(extraConfirmationMemo))
      .mockResolvedValueOnce(response(uppercaseConfirmationHash))
      .mockResolvedValueOnce(response(wrongPreviewProject))
      .mockResolvedValueOnce(response(wrongPreviewLock))
      .mockResolvedValueOnce(response(extraPreview))
      .mockResolvedValueOnce(response(uppercasePreviewHash))
      .mockResolvedValueOnce(response(openNotAnswerable))
      .mockResolvedValueOnce(response(rangedNotAnswerable))
      .mockResolvedValueOnce(response(reorderedArtifacts))
      .mockResolvedValueOnce(response(duplicateArtifact))
      .mockResolvedValueOnce(response(wrongPublishedProject, 201))
      .mockResolvedValueOnce(response(wrongRevisionProject))
      .mockResolvedValueOnce(response(wrongRevisionId))
      .mockResolvedValueOnce(response(wrongRevisionStatus))
      .mockResolvedValueOnce(response(extraRevision));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();

    for (let index = 0; index < 4; index += 1) {
      await expect(api.confirmCompanyResearchJudgment(ids.project, confirmationRequest)).rejects.toMatchObject({
        code: index === 0 ? "identity_mismatch" : "invalid_response",
      });
    }
    for (let index = 0; index < 8; index += 1) {
      await expect(api.previewCompanyResearchPublication(ids.project, previewRequest)).rejects.toMatchObject({
        code: index < 2 ? "identity_mismatch" : "invalid_response",
      });
    }
    await expect(api.publishCompanyResearch(ids.project, publishRequest, "publish-key"))
      .rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.companyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.companyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.companyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(17);
  });

  it("verifies company research publication export identity and UTF-8 content hash", async () => {
    const badHash = companyResearchMarkdownExportBody();
    badHash.content_hash = hash;
    const wrongRevision = companyResearchMarkdownExportBody();
    wrongRevision.filename = `alphabet-company-research-${ids.manifest}.md`;
    const extraExport = companyResearchMarkdownExportBody();
    Object.assign(extraExport, { unexpected: true });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(badHash))
      .mockResolvedValueOnce(response(wrongRevision))
      .mockResolvedValueOnce(response(extraExport)));
    const api = new InvestmentResearchApi();

    await expect(api.exportCompanyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.exportCompanyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.exportCompanyResearchRevision(ids.project, ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each([
    ["evidence_index", "queued", 0], ["business_map", "queued", 0], ["driver_map", "queued", 0],
    ["financial_bridge", "queued", 0], ["scenario_set", "queued", 0], ["valuation_set", "queued", 0],
    ["research_gaps", "queued", 0], ["judgment_context", "queued", 0], ["memo", "queued", 0],
    ["model_bundle", "building_model", 25],
  ] as const)("accepts the repository retry response for %s", async (step, status, progress) => {
    vi.stubGlobal("fetch", vi.fn(async () => response(companyResearchProjectBody(status, step, progress), 202)));
    await expect(new InvestmentResearchApi().retryCompanyResearchProject(ids.project)).resolves.toMatchObject({
      preparation: { current_step: step, status, progress },
    });
  });

  it("rejects company-research response identity and preparation-state drift", async () => {
    const api = new InvestmentResearchApi();
    const invalidPreview = companyResearchPreviewBody();
    invalidPreview.securities.push({ ...invalidPreview.securities[0]! });
    const invalidStatus = companyResearchProjectBody("completed", "evidence_index");
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(invalidPreview))
      .mockResolvedValueOnce(response(invalidStatus));
    vi.stubGlobal("fetch", fetchSpy);

    await expect(api.previewCompanyResearch({ schema_version: "underwriting.v1", company_id: ids.company, cutoff_at: now }))
      .rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchProject(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts canonical set equality but rejects missing and duplicate association IDs", async () => {
    const api = new InvestmentResearchApi();
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(projectBody([ids.securityB, ids.securityA]), 201))
      .mockResolvedValueOnce(response(projectBody([ids.securityA]), 201))
      .mockResolvedValueOnce(response(projectBody([ids.securityA, ids.securityA]), 201));
    vi.stubGlobal("fetch", fetchSpy);
    await expect(api.createProject(projectRequest)).resolves.toMatchObject({ id: ids.project });
    await expect(api.createProject(projectRequest)).rejects.toThrow("project identity mismatch");
    await expect(api.createProject(projectRequest)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects a valid body delivered with the wrong success status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ schema_version: "underwriting.v1", items: [] }, 201)));
    await expect(new InvestmentResearchApi().projects()).rejects.toMatchObject({ code: "unexpected_status", status: 201 });
  });

  it("rejects an effective-rights response whose version does not cover as_of", async () => {
    const invalid = rightsBody();
    invalid.effective_from = "2026-08-25T00:00:00Z";
    vi.stubGlobal("fetch", vi.fn(async () => response(effectiveRightsBody(invalid))));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, now))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects a company-research preview for a different company", async () => {
    const invalidPreview = companyResearchPreviewBody();
    invalidPreview.company.object_id = ids.securityA;
    vi.stubGlobal("fetch", vi.fn(async () => response(invalidPreview)));

    await expect(new InvestmentResearchApi().previewCompanyResearch({
      schema_version: "underwriting.v1",
      company_id: ids.company,
      cutoff_at: now,
    })).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("binds company-research preview cutoff to the requested instant", async () => {
    const api = new InvestmentResearchApi();
    const validPreview = companyResearchPreviewBody();
    const invalidPreview = companyResearchPreviewBody();
    invalidPreview.cutoff_at = "2026-08-24T00:00:01Z";
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(validPreview))
      .mockResolvedValueOnce(response(invalidPreview));
    vi.stubGlobal("fetch", fetchSpy);

    await expect(api.previewCompanyResearch({
      schema_version: "underwriting.v1",
      company_id: ids.company,
      cutoff_at: "2026-08-24T08:00:00+08:00",
    })).resolves.toMatchObject({ cutoff_at: now });
    await expect(api.previewCompanyResearch({
      schema_version: "underwriting.v1",
      company_id: ids.company,
      cutoff_at: now,
    })).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("rejects upper-case hashes in company-research preview output", async () => {
    const invalidPreview = companyResearchPreviewBody();
    invalidPreview.preview_hash = "C".repeat(64);
    vi.stubGlobal("fetch", vi.fn(async () => response(invalidPreview)));

    await expect(new InvestmentResearchApi().previewCompanyResearch({
      schema_version: "underwriting.v1",
      company_id: ids.company,
      cutoff_at: now,
    })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("validates the closed company-research workspace and review response", async () => {
    const workspace = companyResearchWorkspaceBody();
    const reviewed = { schema_version: "underwriting.v1", evidence_artifact: { ...workspace.artifacts[0]!, version: 2 } };
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(workspace))
      .mockResolvedValueOnce(response(reviewed));
    const api = new InvestmentResearchApi();
    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ project_id: ids.project });
    await expect(api.reviewCompanyEvidence(ids.project, { schema_version: "underwriting.v1", evidence_artifact_id: ids.agenda, fact_key: "reported_revenue", decision: "confirmed", expected_head_id: ids.agenda })).resolves.toMatchObject({ evidence_artifact: { version: 2 } });
    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/underwriting/v1/product/company-research/projects/${ids.project}/workspace`,
      `/api/underwriting/v1/product/company-research/projects/${ids.project}/evidence-reviews`,
    ]);
  });

  it("uses memo-derived gaps and preserves legacy model gap counts", async () => {
    const current = addClosedModelArtifacts(companyResearchWorkspaceBody());
    const legacy = addClosedModelArtifacts(companyResearchWorkspaceBody(), false);
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(current))
      .mockResolvedValueOnce(response(legacy));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ gap_count: 1 });
    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ gap_count: 1 });
  });

  it("rejects a workbench response with unknown artifact kinds or a ready module without artifact", async () => {
    const unknown = companyResearchWorkspaceBody();
    unknown.artifacts[0]!.kind = "unknown_kind";
    const missing = companyResearchWorkspaceBody();
    const evidenceModule = missing.modules.find((item: any) => item.key === "evidence_and_gaps");
    evidenceModule.state = "ready";
    evidenceModule.artifact_refs = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(unknown))
      .mockResolvedValueOnce(response(missing));
    const api = new InvestmentResearchApi();
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("rejects reordered modules, invalid decimals, and evidence without its exact source parent", async () => {
    const reordered = companyResearchWorkspaceBody();
    [reordered.modules[0]!, reordered.modules[1]!] = [reordered.modules[1]!, reordered.modules[0]!];
    const invalidDecimal = companyResearchWorkspaceBody();
    invalidDecimal.artifacts[0]!.payload.facts[0]!.observation.value = "1e3";
    const missingParent = companyResearchWorkspaceBody();
    missingParent.artifacts[0]!.source_refs = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(reordered))
      .mockResolvedValueOnce(response(invalidDecimal))
      .mockResolvedValueOnce(response(missingParent));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("rejects derived observations backed by external facts and invalid failure timing", async () => {
    const externalDerived = companyResearchWorkspaceBody();
    externalDerived.artifacts[0]!.payload.facts[0]!.observation.state = "derived";
    const recoverableWithoutRetry = companyResearchWorkspaceBody();
    recoverableWithoutRetry.preparation = { schema_version: "underwriting.v1", id: ids.draft, status: "recoverable_failure", current_step: "evidence_index", progress: 10, error: { schema_version: "underwriting.v1", code: "source_unavailable", failed_step: "evidence_index", retryable: true, next_attempt_at: null } };
    recoverableWithoutRetry.artifacts = [];
    recoverableWithoutRetry.change_summary.artifact_versions = {};
    recoverableWithoutRetry.modules = recoverableWithoutRetry.modules.map((item: any) => ({ ...item, state: "blocked", artifact_refs: [] }));
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(externalDerived))
      .mockResolvedValueOnce(response(recoverableWithoutRetry));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("rejects a reported observation whose value differs from its registry fact", async () => {
    const valid = addReportedBusinessArtifact(companyResearchWorkspaceBody());
    const substituted = addReportedBusinessArtifact(companyResearchWorkspaceBody());
    const business = substituted.artifacts.find((item: any) => item.kind === "business_map");
    business.payload.modules[0]!.classified_evidence[0]!.observation.value = "999";
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(valid))
      .mockResolvedValueOnce(response(substituted));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ project_id: ids.project });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("counts rejected evidence as reviewed when it is only displayed in the registry", async () => {
    const workspace = companyResearchWorkspaceBody();
    workspace.artifacts.find((item: any) => item.kind === "evidence_index").payload.facts[0]!.review_decision = "rejected";
    workspace.change_summary.reviewed_fact_count = 1;
    vi.stubGlobal("fetch", vi.fn(async () => response(workspace)));

    await expect(new InvestmentResearchApi().companyResearchWorkspace(ids.project))
      .resolves.toMatchObject({ change_summary: { reviewed_fact_count: 1 } });
  });

  it("rejects downstream reported observations backed by rejected or pending facts", async () => {
    const rejected = addReportedBusinessArtifact(companyResearchWorkspaceBody());
    rejected.artifacts.find((item: any) => item.kind === "evidence_index").payload.facts[0]!.review_decision = "rejected";
    const pending = addReportedBusinessArtifact(companyResearchWorkspaceBody());
    delete pending.artifacts.find((item: any) => item.kind === "evidence_index").payload.facts[0]!.review_decision;
    pending.change_summary.reviewed_fact_count = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(rejected))
      .mockResolvedValueOnce(response(pending));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("rejects a change summary that differs from the response registry", async () => {
    const wrongVersion = companyResearchWorkspaceBody();
    wrongVersion.change_summary.artifact_versions.evidence_index = 2;
    const wrongReviewed = companyResearchWorkspaceBody();
    wrongReviewed.change_summary.reviewed_fact_count = 1;
    const wrongSources = companyResearchWorkspaceBody();
    wrongSources.source_count = 2;
    const wrongGaps = companyResearchWorkspaceBody();
    wrongGaps.gap_count = 1;
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(wrongVersion))
      .mockResolvedValueOnce(response(wrongReviewed))
      .mockResolvedValueOnce(response(wrongSources))
      .mockResolvedValueOnce(response(wrongGaps));
    const api = new InvestmentResearchApi();

    for (let index = 0; index < 4; index += 1) {
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
    expect(fetchSpy).toHaveBeenCalledTimes(4);
  });

  it("rejects scenario overrides that claim currency units instead of multipliers", async () => {
    const valid = addScenarioArtifact(companyResearchWorkspaceBody());
    const invalid = addScenarioArtifact(companyResearchWorkspaceBody());
    const scenario = invalid.artifacts.find((item: any) => item.kind === "scenario_set");
    const revenue = scenario.payload.scenarios[0]!.driver_overrides.find((item: any) => item.driver_key === "revenue");
    revenue.observation.unit = "USD_million";
    revenue.observation.currency = "USD";
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(valid))
      .mockResolvedValueOnce(response(invalid));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).resolves.toMatchObject({ project_id: ids.project });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("rejects ready or needs-review modules missing a static required head", async () => {
    const missingGap = companyResearchWorkspaceBody();
    missingGap.artifacts = missingGap.artifacts.filter((item: any) => item.kind !== "research_gaps");
    delete missingGap.change_summary.artifact_versions.research_gaps;
    const evidenceModule = missingGap.modules.find((item: any) => item.key === "evidence_and_gaps");
    evidenceModule.artifact_refs = evidenceModule.artifact_refs.filter((ref: any) => ref.kind !== "research_gaps");
    const missingScenario = companyResearchWorkspaceBody();
    Object.assign(missingScenario.modules.find((item: any) => item.key === "scenarios_valuation_implied_expectations"), { state: "ready", valuation_state: "blocked", artifact_refs: [] });
    const missingJudgment = companyResearchWorkspaceBody();
    Object.assign(missingJudgment.modules.find((item: any) => item.key === "overview"), { state: "ready", artifact_refs: [] });
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(missingGap))
      .mockResolvedValueOnce(response(missingScenario))
      .mockResolvedValueOnce(response(missingJudgment));
    const api = new InvestmentResearchApi();

    for (let index = 0; index < 3; index += 1) {
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("rejects workspace extra keys, project mismatch, and duplicate module keys", async () => {
    const extra = companyResearchWorkspaceBody();
    Reflect.set(extra, "unexpected", true);
    const mismatched = companyResearchWorkspaceBody();
    mismatched.project_id = ids.company;
    mismatched.artifacts.forEach((artifact: any) => { artifact.project_id = ids.company; });
    const duplicate = companyResearchWorkspaceBody();
    duplicate.modules[1]! = { ...duplicate.modules[0]! };
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(extra))
      .mockResolvedValueOnce(response(mismatched))
      .mockResolvedValueOnce(response(duplicate));
    const api = new InvestmentResearchApi();

    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("rejects nonexistent, foreign, hash-substituted, and duplicate registry artifacts", async () => {
    const nonexistent = companyResearchWorkspaceBody();
    nonexistent.modules.find((item: any) => item.key === "evidence_and_gaps").artifact_refs[0]!.id = ids.company;
    const foreign = companyResearchWorkspaceBody();
    foreign.artifacts[0]!.project_id = ids.company;
    const substituted = companyResearchWorkspaceBody();
    substituted.modules.find((item: any) => item.key === "evidence_and_gaps").artifact_refs[0]!.content_hash = companyResearchHash;
    const duplicate = companyResearchWorkspaceBody();
    duplicate.artifacts.push({ ...duplicate.artifacts[0]! });
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response(nonexistent))
      .mockResolvedValueOnce(response(foreign))
      .mockResolvedValueOnce(response(substituted))
      .mockResolvedValueOnce(response(duplicate));
    const api = new InvestmentResearchApi();

    for (let index = 0; index < 4; index += 1) {
      await expect(api.companyResearchWorkspace(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    }
    expect(fetchSpy).toHaveBeenCalledTimes(4);
  });

  it("accepts a closed recoverable company-research workspace", async () => {
    const recoverable = companyResearchWorkspaceBody();
    recoverable.preparation = { schema_version: "underwriting.v1", id: ids.draft, status: "recoverable_failure", current_step: "evidence_index", progress: 10, error: { schema_version: "underwriting.v1", code: "source_unavailable", failed_step: "evidence_index", retryable: true, next_attempt_at: now } };
    recoverable.artifacts = [];
    recoverable.source_count = 0;
    recoverable.change_summary.artifact_versions = {};
    recoverable.modules = recoverable.modules.map((item: any) => ({ ...item, state: "blocked", artifact_refs: [] }));
    vi.stubGlobal("fetch", vi.fn(async () => response(recoverable)));

    await expect(new InvestmentResearchApi().companyResearchWorkspace(ids.project))
      .resolves.toMatchObject({ preparation: { status: "recoverable_failure", error: { code: "source_unavailable", retryable: true } } });
  });

  it("rejects a zero-length rights interval from the service", async () => {
    const invalid = rightsBody();
    Reflect.set(invalid, "effective_to", invalid.effective_from);
    vi.stubGlobal("fetch", vi.fn(async () => response(effectiveRightsBody(invalid))));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, now))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects a zero-length rights response on the create path", async () => {
    const request = {
      ...rightsRequest,
      effective_to: rightsRequest.effective_from,
    };
    const invalid = rightsBody();
    Reflect.set(invalid, "effective_to", invalid.effective_from);
    vi.stubGlobal("fetch", vi.fn(async () => response(invalid, 201)));

    await expect(new InvestmentResearchApi().createSecurityRights(request))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects an appendable rights response without the explicit head parent", async () => {
    const invalid = {
      ...effectiveRightsBody(),
      effective: null,
      append_allowed: true,
      minimum_effective_from: "2026-08-23T00:00:00Z",
      reason: {
        schema_version: "underwriting.v1",
        code: "successor_required",
        action: "append_successor",
      },
    };
    vi.stubGlobal("fetch", vi.fn(async () => response(invalid)));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, now))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts a successor resolution with a closed head and its exact append boundary", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(successorRightsBody())));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, now))
      .resolves.toMatchObject({ expected_parent_id: ids.rightsA });
  });

  it("accepts an open-ended historical version shadowed by a later current head", async () => {
    const effective = rightsBody();
    effective.effective_from = "2026-08-20T00:00:00Z";
    const body = {
      ...effectiveRightsBody(effective),
      as_of: "2026-08-21T00:00:00Z",
      head: {
        schema_version: "underwriting.v1",
        id: ids.rightsB,
        effective_from: "2026-08-22T00:00:00Z",
        effective_to: null,
      },
    };
    vi.stubGlobal("fetch", vi.fn(async () => response(body)));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, body.as_of))
      .resolves.toMatchObject({ effective: { id: ids.rightsA }, head: { id: ids.rightsB } });
  });

  it.each([
    {
      name: "effective version without the current head",
      body: { ...effectiveRightsBody(), head: null },
    },
    {
      name: "effective version whose same-id head has a different interval",
      body: {
        ...effectiveRightsBody(),
        head: {
          ...effectiveRightsBody().head,
          effective_from: "2026-08-22T00:00:00Z",
        },
      },
    },
    {
      name: "historical effective version overlapping a different current head",
      body: (() => {
        const effective = rightsBody();
        effective.effective_from = "2026-08-20T00:00:00Z";
        Reflect.set(effective, "effective_to", "2026-08-23T00:00:00Z");
        return {
          ...effectiveRightsBody(effective),
          as_of: "2026-08-21T00:00:00Z",
          head: {
            schema_version: "underwriting.v1",
            id: ids.rightsB,
            effective_from: "2026-08-22T00:00:00Z",
            effective_to: null,
          },
        };
      })(),
    },
    {
      name: "different head whose start does not advance the effective version",
      body: (() => {
        const effective = rightsBody();
        effective.effective_from = "2026-08-20T00:00:00Z";
        return {
          ...effectiveRightsBody(effective),
          as_of: "2026-08-20T00:00:00Z",
          head: {
            schema_version: "underwriting.v1",
            id: ids.rightsB,
            effective_from: "2026-08-20T00:00:00Z",
            effective_to: null,
          },
        };
      })(),
    },
    {
      name: "historical effective version at the later head start",
      body: (() => {
        const effective = rightsBody();
        effective.effective_from = "2026-08-20T00:00:00Z";
        return {
          ...effectiveRightsBody(effective),
          as_of: "2026-08-22T00:00:00Z",
          head: {
            schema_version: "underwriting.v1",
            id: ids.rightsB,
            effective_from: "2026-08-22T00:00:00Z",
            effective_to: null,
          },
        };
      })(),
    },
    {
      name: "successor resolution with an open-ended head",
      body: {
        ...successorRightsBody(),
        head: { ...successorRightsBody().head, effective_to: null },
      },
    },
    {
      name: "successor resolution whose minimum differs from the head end",
      body: {
        ...successorRightsBody(),
        minimum_effective_from: "2026-08-22T00:00:00Z",
      },
    },
    {
      name: "successor resolution before the head end",
      body: {
        ...successorRightsBody(),
        as_of: "2026-08-22T00:00:00Z",
      },
    },
    {
      name: "initial resolution with a head",
      body: {
        ...successorRightsBody(),
        expected_parent_id: null,
        minimum_effective_from: null,
        reason: {
          schema_version: "underwriting.v1",
          code: "no_history",
          action: "create_initial",
        },
      },
    },
    {
      name: "before-head resolution at the head start",
      body: {
        ...successorRightsBody(),
        as_of: "2026-08-20T00:00:00Z",
        append_allowed: false,
        expected_parent_id: null,
        minimum_effective_from: null,
        reason: {
          schema_version: "underwriting.v1",
          code: "before_head",
          action: "adjust_market_at",
        },
      },
    },
  ])("rejects an impossible rights resolution: $name", async ({ body }) => {
    vi.stubGlobal("fetch", vi.fn(async () => response(body)));

    await expect(new InvestmentResearchApi().effectiveSecurityRights(ids.securityA, String(body.as_of)))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects missing required response fields and inconsistent answerability", async () => {
    const invalidProject = projectBody();
    Reflect.deleteProperty(invalidProject, "content_hash");
    const invalidPreview = previewBody();
    Reflect.set(invalidPreview.assessment, "direction", "provisional_bullish");
    const invalidDate = projectBody();
    invalidDate.created_at = "2026-02-30T00:00:00Z";
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(invalidProject))
      .mockResolvedValueOnce(response(invalidPreview))
      .mockResolvedValueOnce(response(invalidDate));
    vi.stubGlobal("fetch", fetchSpy);
    await expect(new InvestmentResearchApi().project(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(new InvestmentResearchApi().preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 })).rejects.toMatchObject({ code: "invalid_response" });
    await expect(new InvestmentResearchApi().project(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects impossible preview and revision reference collections", async () => {
    const missingPreview = previewBody();
    missingPreview.manifest.market_snapshot_refs.pop();
    const extraPreview = previewBody();
    extraPreview.manifest.market_snapshot_refs.push(`price:${ids.membershipA}`);
    const wrongPrefixPreview = previewBody();
    wrongPrefixPreview.manifest.market_snapshot_refs[0]! = `fx:${ids.priceA}`;
    const duplicatePreview = previewBody();
    duplicatePreview.manifest.market_snapshot_refs[1]! = duplicatePreview.manifest.market_snapshot_refs[0]!;
    const forbiddenModelPreview = previewBody();
    Reflect.set(forbiddenModelPreview.manifest, "model_refs", ["model:forbidden"]);
    const missingRevision = revisionBody();
    missingRevision.market_snapshot_ids.pop();
    const extraRevision = revisionBody();
    extraRevision.market_snapshot_ids.push(ids.membershipA);
    const duplicateRevision = revisionBody();
    duplicateRevision.market_snapshot_ids[1]! = duplicateRevision.market_snapshot_ids[0]!;
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(missingPreview))
      .mockResolvedValueOnce(response(extraPreview))
      .mockResolvedValueOnce(response(wrongPrefixPreview))
      .mockResolvedValueOnce(response(duplicatePreview))
      .mockResolvedValueOnce(response(forbiddenModelPreview))
      .mockResolvedValueOnce(response(missingRevision))
      .mockResolvedValueOnce(response(extraRevision))
      .mockResolvedValueOnce(response(duplicateRevision));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    for (let index = 0; index < 5; index += 1) {
      await expect(api.preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 })).rejects.toMatchObject({ code: "invalid_response" });
    }
    for (let index = 0; index < 3; index += 1) {
      await expect(api.revision(ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
    }
  });

  it("rejects agenda provenance, item, and output-hash mismatches", async () => {
    const missingProvenance = agendaBody();
    Reflect.deleteProperty(missingProvenance.generator_provenance, "template_version");
    const invalidProvenance = agendaBody();
    Reflect.set(invalidProvenance.generator_provenance, "input_summary_hash", null);
    const changedItems = agendaBody();
    changedItems.payload.items = ["核验风险"];
    const changedHash = agendaBody();
    changedHash.generator_provenance.output_hash = hash;
    const invalidAi = agendaBody();
    Object.assign(invalidAi.generator_provenance, { method: "ai_generated", template_key: null, template_version: null, model_name: null, prompt_template_version: "prompt-v1", input_summary_hash: hash });
    const validAi = agendaBody();
    Object.assign(validAi.generator_provenance, aiAgendaRequest.generator);
    const invalidAiHash = agendaBody();
    Object.assign(invalidAiHash.generator_provenance, aiAgendaRequest.generator, { output_hash: hash });
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(missingProvenance, 201))
      .mockResolvedValueOnce(response(invalidProvenance, 201))
      .mockResolvedValueOnce(response(changedItems, 201))
      .mockResolvedValueOnce(response(changedHash, 201))
      .mockResolvedValueOnce(response(invalidAi, 201))
      .mockResolvedValueOnce(response(validAi, 201))
      .mockResolvedValueOnce(response(invalidAiHash, 201));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    await expect(api.createAgenda(ids.project, agendaRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createAgenda(ids.project, agendaRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createAgenda(ids.project, agendaRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createAgenda(ids.project, agendaRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createAgenda(ids.project, aiAgendaRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createAgenda(ids.project, aiAgendaRequest)).resolves.toMatchObject({ id: ids.agenda });
    await expect(api.createAgenda(ids.project, { ...aiAgendaRequest, generator: { ...aiAgendaRequest.generator, output_hash: hash } })).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("binds every echoed immutable request field using contract semantics", async () => {
    const mandateMismatch = mandateBody(); mandateMismatch.required_return = "0.13";
    const scopeMismatch = scopeBody(); Reflect.set(scopeMismatch.payload, "industry_ids", [ids.membershipA]);
    const basisMismatch = basisBody(); basisMismatch.source_manifest_hash = "b".repeat(64);
    const priceMismatch = priceBody(); priceMismatch.source_id = "other-source";
    const fxMismatch = fxBody(); fxMismatch.rate = "0.15";
    const capitalMismatch = capitalBody(); capitalMismatch.diluted_shares = "1011";
    const rightsMismatch = rightsBody(); rightsMismatch.effective_from = "2026-08-25T00:00:00Z";
    const previewMismatch = previewBody(); previewMismatch.expected_lock_version = 3;
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(mandateMismatch, 201))
      .mockResolvedValueOnce(response(scopeMismatch, 201))
      .mockResolvedValueOnce(response(basisMismatch, 201))
      .mockResolvedValueOnce(response(priceMismatch, 201))
      .mockResolvedValueOnce(response(fxMismatch, 201))
      .mockResolvedValueOnce(response(capitalMismatch, 201))
      .mockResolvedValueOnce(response(rightsMismatch, 201))
      .mockResolvedValueOnce(response(previewMismatch));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    await expect(api.createMandate(ids.project, mandateRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createScope(ids.project, scopeRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createHistoricalBasis(basisRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createPriceSnapshot(priceRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createFxSnapshot(fxRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createCapitalStructure(capitalRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.createSecurityRights(rightsRequest)).rejects.toMatchObject({ code: "identity_mismatch" });
    await expect(api.preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 })).rejects.toMatchObject({ code: "identity_mismatch" });
  });

  it("accepts decimal and instant equivalence without floating point coercion", async () => {
    const equivalentMandate = mandateBody(); equivalentMandate.required_return = "0.12000000"; equivalentMandate.effective_at = "2026-08-24T08:00:00+08:00";
    const equivalentPrice = priceBody(); equivalentPrice.price = "100.000"; equivalentPrice.market_at = "2026-08-24T08:00:00+08:00";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(equivalentMandate, 201)).mockResolvedValueOnce(response(equivalentPrice, 201)));
    const api = new InvestmentResearchApi();
    await expect(api.createMandate(ids.project, mandateRequest)).resolves.toMatchObject({ id: ids.mandate });
    await expect(api.createPriceSnapshot(priceRequest)).resolves.toMatchObject({ id: ids.priceA });
  });

  it("fails closed on extra keys and impossible domain values", async () => {
    const extraProject = projectBody(); Reflect.set(extraProject, "unexpected", true);
    const negativePrice = priceBody(); negativePrice.price = "-1";
    const sameCurrencyFx = fxBody(); sameCurrencyFx.quote_currency = "CNY";
    const invalidCapital = capitalBody(); invalidCapital.basic_shares = "1011"; invalidCapital.diluted_shares = "1010";
    const negativeRights = rightsBody(); negativeRights.economic_units = "-1";
    const invalidMandate = mandateBody(); invalidMandate.horizon_years = 2;
    const extraDraft = draftBody(); Reflect.set(extraDraft.content, "unexpected", true);
    const extraPreview = previewBody(); Reflect.set(extraPreview.assessment, "unexpected", true);
    const extraRevision = revisionBody(); Reflect.set(extraRevision, "unexpected", true);
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(extraProject))
      .mockResolvedValueOnce(response(negativePrice, 201))
      .mockResolvedValueOnce(response(sameCurrencyFx, 201))
      .mockResolvedValueOnce(response(invalidCapital, 201))
      .mockResolvedValueOnce(response(negativeRights, 201))
      .mockResolvedValueOnce(response(invalidMandate, 201))
      .mockResolvedValueOnce(response(extraDraft))
      .mockResolvedValueOnce(response(extraPreview))
      .mockResolvedValueOnce(response(extraRevision));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    await expect(api.project(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createPriceSnapshot(priceRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createFxSnapshot(fxRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createCapitalStructure(capitalRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createSecurityRights(rightsRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createMandate(ids.project, mandateRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.draft(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 })).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.revision(ids.revision)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("validates the complete error envelope and rejects header/body request identity mismatch", async () => {
    const validError = { schema_version: "underwriting.v1", error: { code: "validation_failed", message: "security relation failed", request_id: "req-body", details: { field: "target_security_ids" } } };
    const extraError = { ...validError, unexpected: true };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(validError, 422, "req-body"))
      .mockResolvedValueOnce(response(validError, 422, "req-header"))
      .mockResolvedValueOnce(response({ schema_version: "underwriting.v1", error: { code: "validation_failed", message: "missing request identity" } }, 422))
      .mockResolvedValueOnce(response(extraError, 422, "req-body"));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    const valid = await api.createProject(projectRequest).catch((error: unknown) => error);
    expect(valid).toBeInstanceOf(InvestmentResearchRequestError);
    expect(valid).toMatchObject({ status: 422, code: "validation_failed", message: "security relation failed", requestId: "req-body", details: { field: "target_security_ids" } });
    await expect(api.createProject(projectRequest)).rejects.toMatchObject({ code: "invalid_error_response", message: "投资研究服务暂时无法完成请求" });
    await expect(api.createProject(projectRequest)).rejects.toMatchObject({ code: "invalid_error_response" });
    await expect(api.createProject(projectRequest)).rejects.toMatchObject({ code: "invalid_error_response" });
  });

  it("rejects non-JSON error bodies without leaking their contents", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<h1>proxy secret</h1>", { status: 502 })));
    const failure = await new InvestmentResearchApi().projects().catch((error: unknown) => error);
    expect(failure).toMatchObject({ status: 502, code: "invalid_error_response", message: "投资研究服务暂时无法完成请求" });
    expect(String(failure)).not.toContain("proxy secret");
  });

  it("encodes path and query identities", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response({ schema_version: "underwriting.v1", items: [] }))
      .mockResolvedValueOnce(response({ ...projectBody(), id: "project id" }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi("/gateway/");
    await api.searchObjects("宁德 时代", { asOf: "2026-08-24T00:00:00+08:00", limit: 10 });
    await expect(api.project("project id")).rejects.toMatchObject({ code: "invalid_response" });
    expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([
      "/gateway/api/underwriting/v1/product/objects?query=%E5%AE%81%E5%BE%B7+%E6%97%B6%E4%BB%A3&as_of=2026-08-24T00%3A00%3A00%2B08%3A00&limit=10",
      "/gateway/api/underwriting/v1/product/projects/project%20id",
    ]);
  });
});
