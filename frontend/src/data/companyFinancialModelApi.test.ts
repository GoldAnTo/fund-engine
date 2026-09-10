import { createHash } from "node:crypto";
import actualBaseline from "../../../backend/app/underwriting/data/alphabet_financial_baseline/baseline.json";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvestmentResearchApi } from "./investmentResearchApi";
import { modelHash, modelIds, modelRecord, modelWorkspace } from "./companyFinancialModel.test-fixtures";
const api = new InvestmentResearchApi("");
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
describe("company financial model API boundaries", () => {
  it("accepts the shipped official baseline without stripping its version or source identities", async () => {
    const value = { ...modelWorkspace(), baseline: actualBaseline };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
    expect((await api.companyFinancialModel(modelIds.project)).baseline).toEqual(actualBaseline);
  });
  it("accepts the versioned capital policy URN while rejecting executable source links", async () => {
    const value = modelWorkspace(); value.market!.capital_structure.policy_ref = { ...value.market!.capital_structure.policy_ref, fact_key: "capital_bridge_policy", source_url: "urn:company-research:capital-bridge-policy" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
    expect((await api.companyFinancialModel(modelIds.project)).market).toEqual(value.market);
    value.market!.capital_structure.source_ref.source_url = "javascript:alert(1)";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
    await expect(api.companyFinancialModel(modelIds.project)).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("reads a frozen financial context from the existing product API", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(modelWorkspace())); vi.stubGlobal("fetch", fetcher);
    expect(await api.companyFinancialModel(modelIds.project)).toEqual(modelWorkspace());
    expect(fetcher.mock.calls[0]?.[0]).toBe(`/api/underwriting/v1/product/company-research/projects/${modelIds.project}/financial-model`);
  });
  it("sends a draft CAS and idempotency key and verifies the echoed inputs", async () => {
    const value = modelRecord(); const fetcher = vi.fn().mockResolvedValue(response(value, 201)); vi.stubGlobal("fetch", fetcher);
    const body = { parent_revision_id: modelIds.revision, expected_latest_id: null, baseline_content_hash: modelHash, inputs: value.inputs };
    expect(await api.saveCompanyFinancialModel(modelIds.project, body, "model-save")).toEqual(value);
    expect(fetcher.mock.calls[0]?.[1].headers).toMatchObject({ "Idempotency-Key": "model-save" });
  });
  it.each(["project", "status", "number", "scenario", "history", "source"])("rejects malformed or substituted %s", async (kind) => {
    const value = modelWorkspace();
    if (kind === "project") value.project_id = "20000000-0000-4000-8000-000000000099";
    if (kind === "status") { value.latest = modelRecord(); (value.latest as unknown as Record<string, unknown>).status = "confirmed"; }
    if (kind === "number") value.initial_inputs.scenarios[0]!.paths.revenue[0] = "NaN";
    if (kind === "scenario") value.initial_inputs.scenarios[1]!.scenario_id = "base";
    if (kind === "history") value.history = [{ ...modelRecord(), id: "foreign" }];
    if (kind === "source") value.baseline.facts[0]!.source_ids = ["foreign"];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
    await expect(api.companyFinancialModel(modelIds.project)).rejects.toMatchObject({ code: kind === "project" ? "identity_mismatch" : "invalid_response" });
  });
  it("rejects a returned draft for another requested history id", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(modelRecord(2))));
    await expect(api.companyFinancialModelDraft(modelIds.project, modelIds.draft)).rejects.toMatchObject({ code: "identity_mismatch" });
  });
  it("verifies exported UTF-8 content and rejects either altered bytes or a foreign draft filename", async () => {
    const content = "# 条件模型草稿\n尚未复核。\n";
    const body = { filename: `alphabet-conditional-model-${modelIds.draft}.md`, media_type: "text/markdown", content, content_hash: createHash("sha256").update(content, "utf8").digest("hex") };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(body)));
    expect(await api.exportCompanyFinancialModelDraft(modelIds.project, modelIds.draft)).toEqual(body);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...body, content: content + "altered" })));
    await expect(api.exportCompanyFinancialModelDraft(modelIds.project, modelIds.draft)).rejects.toMatchObject({ code: "identity_mismatch" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...body, filename: `alphabet-conditional-model-${modelRecord(2).id}.md` })));
    await expect(api.exportCompanyFinancialModelDraft(modelIds.project, modelIds.draft)).rejects.toMatchObject({ code: "identity_mismatch" });
  });
  it("rejects changed saved input even if project and parent match", async () => {
    const value = modelRecord(); value.inputs.discount_rate = "0.2";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value, 201)));
    await expect(api.saveCompanyFinancialModel(modelIds.project, { parent_revision_id: modelIds.revision, expected_latest_id: null, baseline_content_hash: modelHash, inputs: modelWorkspace().initial_inputs }, "save")).rejects.toMatchObject({ code: "identity_mismatch" });
  });
});
