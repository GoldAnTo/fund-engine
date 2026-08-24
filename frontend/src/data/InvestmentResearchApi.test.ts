import { afterEach, describe, expect, it, vi } from "vitest";

import {
  InvestmentResearchApi,
  InvestmentResearchRequestError,
} from "./investmentResearchApi";

const hash = "a".repeat(64);

function response(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-product" },
  });
}

describe("InvestmentResearchApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses encoded product URLs and binds project, draft and revision identities", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        items: [],
      }))
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        id: "project id",
        primary_company_id: "company-1",
        target_security_ids: ["security-1"],
        content_hash: hash,
        created_at: "2026-08-24T00:00:00Z",
      }))
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        id: "draft-1",
        project_id: "project id",
        base_revision_id: null,
        lock_version: 2,
        content: {
          schema_version: "underwriting.v1",
          publication_status: "draft",
          mandate_id: null,
          scope_id: null,
          agenda_id: null,
          historical_basis_id: null,
          price_snapshot_ids: [],
          fx_snapshot_ids: [],
          capital_structure_snapshot_id: null,
          security_rights_ids: [],
          user_focus: null,
        },
        created_at: "2026-08-24T00:00:00Z",
        updated_at: "2026-08-24T00:00:00Z",
      }))
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        id: "revision id",
        project_id: "project id",
        object_id: "company-1",
        basis_id: "basis-1",
        boundary_id: "boundary-1",
        manifest_id: "manifest-1",
        version_kind: "independent_research",
        sequence: 1,
        content_hash: hash,
        cutoff: "2026-08-24T00:00:00Z",
        source_manifest_hash: hash,
        manifest_hash: hash,
        parent_revision_id: null,
        price_snapshot_ids: ["price-1"],
        fx_snapshot_ids: [],
        capital_structure_snapshot_id: "capital-1",
        security_rights_ids: ["rights-1"],
        market_snapshot_ids: ["price:price-1"],
        answerability: "not_answerable",
        direction: null,
        confidence: null,
        publication_status: "user_frozen",
      }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi("/gateway/");

    await api.searchObjects("宁德 时代", { asOf: "2026-08-24T00:00:00+08:00", limit: 10 });
    await api.project("project id");
    await api.draft("project id");
    await api.revision("revision id");

    expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([
      "/gateway/api/underwriting/v1/product/objects?query=%E5%AE%81%E5%BE%B7+%E6%97%B6%E4%BB%A3&as_of=2026-08-24T00%3A00%3A00%2B08%3A00&limit=10",
      "/gateway/api/underwriting/v1/product/projects/project%20id",
      "/gateway/api/underwriting/v1/product/projects/project%20id/draft",
      "/gateway/api/underwriting/v1/product/revisions/revision%20id",
    ]);
  });

  it("rejects non-JSON, malformed envelopes and cross-project responses without leaking bodies", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(new Response("<h1>proxy secret</h1>", { status: 502 }))
      .mockResolvedValueOnce(response({ ok: true }))
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        id: "draft-1",
        project_id: "different-project",
        base_revision_id: null,
        lock_version: 1,
        content: {
          schema_version: "underwriting.v1",
          publication_status: "draft",
          mandate_id: "mandate-1",
          scope_id: null,
          agenda_id: null,
          historical_basis_id: null,
          price_snapshot_ids: [],
          fx_snapshot_ids: [],
          capital_structure_snapshot_id: null,
          security_rights_ids: [],
          user_focus: null,
        },
        created_at: "2026-08-24T00:00:00Z",
        updated_at: "2026-08-24T00:00:00Z",
      }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();

    const nonJson = await api.projects().catch((error: unknown) => error);
    expect(nonJson).toBeInstanceOf(InvestmentResearchRequestError);
    expect(nonJson).toMatchObject({ status: 502, message: "投资研究服务暂时无法完成请求" });
    expect(String(nonJson)).not.toContain("proxy secret");

    await expect(api.projects()).rejects.toMatchObject({
      code: "invalid_response",
      message: "投资研究服务返回了无法验证的数据",
    });
    await expect(api.saveDraft("project-1", {
      schema_version: "underwriting.v1",
      expected_lock_version: 1,
      mandate_id: "mandate-1",
    })).rejects.toThrow("draft project identity mismatch");
  });

  it("preserves a validated error envelope and sends the publish idempotency key", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        error: {
          code: "validation_failed",
          message: "security is not related to the selected company",
          details: { field: "target_security_ids" },
        },
      }, 422))
      .mockResolvedValueOnce(response({
        schema_version: "underwriting.v1",
        id: "revision-1",
        project_id: "project-1",
        object_id: "company-1",
        basis_id: "basis-1",
        boundary_id: "boundary-1",
        manifest_id: "manifest-1",
        version_kind: "independent_research",
        sequence: 1,
        content_hash: hash,
        cutoff: "2026-08-24T00:00:00Z",
        source_manifest_hash: hash,
        manifest_hash: hash,
        parent_revision_id: null,
        price_snapshot_ids: ["price-1"],
        fx_snapshot_ids: [],
        capital_structure_snapshot_id: "capital-1",
        security_rights_ids: ["rights-1"],
        market_snapshot_ids: ["price:price-1"],
        answerability: "not_answerable",
        direction: null,
        confidence: null,
        publication_status: "user_frozen",
      }));
    vi.stubGlobal("fetch", fetchSpy);
    const api = new InvestmentResearchApi();

    const failure = await api.createProject({
      schema_version: "underwriting.v1",
      primary_company_id: "company-1",
      target_security_ids: ["security-1"],
    }).catch((error: unknown) => error);
    expect(failure).toMatchObject({
      status: 422,
      code: "validation_failed",
      message: "security is not related to the selected company",
      requestId: "req-product",
    });

    await api.publish(
      "project-1",
      { schema_version: "underwriting.v1", expected_lock_version: 4 },
      "publish-key-1",
    );
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/underwriting/v1/product/projects/project-1/publish",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "content-type": "application/json",
          "Idempotency-Key": "publish-key-1",
        }),
      }),
    );
  });
});
