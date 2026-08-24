import { afterEach, describe, expect, it, vi } from "vitest";

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

function response(body: object, status = 200, requestId = "req-product"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": requestId },
  });
}

function projectBody(targets = [ids.securityA, ids.securityB]) {
  return { schema_version: "underwriting.v1", id: ids.project, primary_company_id: ids.company, target_security_ids: targets, company_identity: { schema_version: "underwriting.v1", object_id: ids.company, identity_version_id: ids.membershipA, canonical_name: "Company" }, security_identities: targets.map((objectId, index) => ({ schema_version: "underwriting.v1", object_id: objectId, identity_version_id: index === 0 ? ids.priceA : ids.priceB, canonical_name: index === 0 ? "Security A" : "Security B", symbol: index === 0 ? "AAA" : "BBB", exchange: "EX", share_class: "ordinary", trading_currency: "CNY" })), content_hash: hash, created_at: now };
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
  { name: "get effective rights", status: 200, body: { schema_version: "underwriting.v1", security_identity_id: ids.securityA, as_of: now, effective: rightsBody(), head_id: ids.rightsA }, method: "GET", url: `/api/underwriting/v1/product/market/security-rights/effective?security_identity_id=${ids.securityA}&as_of=2026-08-24T00%3A00%3A00Z`, run: (api) => api.effectiveSecurityRights(ids.securityA, now) },
  { name: "get draft", status: 200, body: draftBody(), method: "GET", url: `/api/underwriting/v1/product/projects/${ids.project}/draft`, run: (api) => api.draft(ids.project) },
  { name: "patch draft", status: 200, body: draftBody(), method: "PATCH", url: `/api/underwriting/v1/product/projects/${ids.project}/draft`, run: (api) => api.saveDraft(ids.project, patchRequest) },
  { name: "preview", status: 200, body: previewBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/publication-preview`, run: (api) => api.preview(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 }) },
  { name: "publish", status: 201, body: revisionBody(), method: "POST", url: `/api/underwriting/v1/product/projects/${ids.project}/publish`, run: (api) => api.publish(ids.project, { schema_version: "underwriting.v1", expected_lock_version: 2 }, "publish-key"), idempotencyKey: "publish-key" },
  { name: "get revision", status: 200, body: revisionBody(), method: "GET", url: `/api/underwriting/v1/product/revisions/${ids.revision}`, run: (api) => api.revision(ids.revision) },
];

describe("InvestmentResearchApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(operationCases)("enforces the $name wire contract", async (operation) => {
    const fetchSpy = vi.fn<typeof fetch>(async () => response(operation.body, operation.status));
    vi.stubGlobal("fetch", fetchSpy);
    await operation.run(new InvestmentResearchApi());
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(operation.url);
    expect(init).toMatchObject({ method: operation.method, credentials: "include" });
    if (operation.method === "GET") expect(init?.body).toBeUndefined();
    else {
      expect(init?.headers).toMatchObject({ "content-type": "application/json" });
      expect(typeof init?.body).toBe("string");
    }
    if (operation.idempotencyKey) expect(init?.headers).toMatchObject({ "Idempotency-Key": operation.idempotencyKey });
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
    wrongPrefixPreview.manifest.market_snapshot_refs[0] = `fx:${ids.priceA}`;
    const duplicatePreview = previewBody();
    duplicatePreview.manifest.market_snapshot_refs[1] = duplicatePreview.manifest.market_snapshot_refs[0];
    const forbiddenModelPreview = previewBody();
    Reflect.set(forbiddenModelPreview.manifest, "model_refs", ["model:forbidden"]);
    const missingRevision = revisionBody();
    missingRevision.market_snapshot_ids.pop();
    const extraRevision = revisionBody();
    extraRevision.market_snapshot_ids.push(ids.membershipA);
    const duplicateRevision = revisionBody();
    duplicateRevision.market_snapshot_ids[1] = duplicateRevision.market_snapshot_ids[0];
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
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(extraProject))
      .mockResolvedValueOnce(response(negativePrice, 201))
      .mockResolvedValueOnce(response(sameCurrencyFx, 201))
      .mockResolvedValueOnce(response(invalidCapital, 201))
      .mockResolvedValueOnce(response(negativeRights, 201))
      .mockResolvedValueOnce(response(invalidMandate, 201));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    await expect(api.project(ids.project)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createPriceSnapshot(priceRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createFxSnapshot(fxRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createCapitalStructure(capitalRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createSecurityRights(rightsRequest)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.createMandate(ids.project, mandateRequest)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("validates the complete error envelope and rejects header/body request identity mismatch", async () => {
    const validError = { schema_version: "underwriting.v1", error: { code: "validation_failed", message: "security relation failed", request_id: "req-body", details: { field: "target_security_ids" } } };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(response(validError, 422, "req-body"))
      .mockResolvedValueOnce(response(validError, 422, "req-header"))
      .mockResolvedValueOnce(response({ schema_version: "underwriting.v1", error: { code: "validation_failed", message: "missing request identity" } }, 422));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();
    const valid = await api.createProject(projectRequest).catch((error: unknown) => error);
    expect(valid).toBeInstanceOf(InvestmentResearchRequestError);
    expect(valid).toMatchObject({ status: 422, code: "validation_failed", message: "security relation failed", requestId: "req-body", details: { field: "target_security_ids" } });
    await expect(api.createProject(projectRequest)).rejects.toMatchObject({ code: "invalid_error_response", message: "投资研究服务暂时无法完成请求" });
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
