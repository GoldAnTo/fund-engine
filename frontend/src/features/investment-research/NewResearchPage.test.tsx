import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRoutes } from "../../app/routes";

const hash = "a".repeat(64);

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": "req-setup" },
  });
}

function searchResponse(): object {
  return {
    schema_version: "underwriting.v1",
    items: [
      {
        schema_version: "underwriting.v1",
        object_id: "company-1",
        identity_version_id: "company-identity-1",
        kind: "company",
        external_key: "CN:300750:COMPANY",
        canonical_name: "宁德时代新能源科技股份有限公司",
        symbol: null,
        exchange: null,
        share_class: null,
        trading_currency: null,
      },
      {
        schema_version: "underwriting.v1",
        object_id: "security-1",
        identity_version_id: "security-identity-1",
        kind: "security",
        external_key: "SZSE:300750",
        canonical_name: "宁德时代 A 股",
        symbol: "300750",
        exchange: "SZSE",
        share_class: "A",
        trading_currency: "CNY",
      },
      {
        schema_version: "underwriting.v1",
        object_id: "industry-1",
        identity_version_id: "industry-identity-1",
        kind: "industry",
        external_key: "INDUSTRY:BATTERY",
        canonical_name: "动力电池",
        symbol: null,
        exchange: null,
        share_class: null,
        trading_currency: null,
      },
    ],
  };
}

async function selectCatl(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "建立研究项目" });
  await user.type(screen.getByLabelText("搜索公司、证券或行业"), "CATL");
  await user.click(screen.getByRole("button", { name: "搜索对象" }));
  await screen.findByRole("region", { name: "对象搜索结果" });
  await user.click(screen.getByRole("radio", { name: /Company.*宁德时代新能源/ }));
  await user.click(screen.getByRole("checkbox", { name: /Security.*300750.*SZSE/ }));
}

describe("new independent investment research setup", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("distinguishes object identities and keeps Industry browse-only", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async () => json(searchResponse())));
    render(
      <MemoryRouter initialEntries={["/research/new"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "建立研究项目" });
    await user.type(screen.getByLabelText("搜索公司、证券或行业"), "CATL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));

    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(within(results).getByText("Company")).toBeVisible();
    expect(within(results).getByText("Security")).toBeVisible();
    expect(within(results).getByText("Industry")).toBeVisible();
    expect(within(results).getByText("300750 · SZSE · A · CNY")).toBeVisible();
    expect(within(results).getByText(/行业仅可加入浏览范围/)).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: /Industry.*动力电池/ }));
    expect(screen.getByRole("button", { name: "提交身份账本校验" })).toBeDisabled();
    expect(screen.getByText(/必须选择一家公司和至少一只证券/)).toBeVisible();
  });

  it("keeps an unrelated Security 422 inline and does not claim a confirmed relation", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(json(searchResponse()))
      .mockResolvedValueOnce(json({
        schema_version: "underwriting.v1",
        error: {
          code: "validation_failed",
          message: "所选证券不属于该公司",
          details: { field: "target_security_ids" },
        },
      }, 422));
    vi.stubGlobal("fetch", fetchSpy);
    render(
      <MemoryRouter initialEntries={["/research/new"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await selectCatl(user);
    expect(screen.getByText(/提交后由身份账本校验关系/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "提交身份账本校验" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("所选证券不属于该公司");
    expect(screen.getByRole("heading", { name: /确认 Company 与 Security/ })).toBeVisible();
    expect(screen.queryByText(/关系已确认/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /研究任务与边界/ })).not.toBeInTheDocument();
  });

  it("runs the real foundation request sequence and enters the workbench with insufficient evidence", async () => {
    const user = userEvent.setup();
    const requestUrls: string[] = [];
    const fetchSpy = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      requestUrls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/product/objects?")) return json(searchResponse());
      if (url.endsWith("/product/projects") && init?.method === "POST") {
        return json({
          schema_version: "underwriting.v1",
          id: "project-1",
          primary_company_id: "company-1",
          target_security_ids: ["security-1"],
          content_hash: hash,
          created_at: "2026-08-24T08:00:00Z",
        }, 201);
      }
      if (url.endsWith("/product/projects/project-1") && init?.method === "GET") {
        return json({
          schema_version: "underwriting.v1",
          id: "project-1",
          primary_company_id: "company-1",
          target_security_ids: ["security-1"],
          content_hash: hash,
          created_at: "2026-08-24T08:00:00Z",
        });
      }
      if (url.endsWith("/draft") && init?.method === "GET") {
        return json({
          schema_version: "underwriting.v1",
          id: "draft-1",
          project_id: "project-1",
          base_revision_id: null,
          lock_version: 1,
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
          created_at: "2026-08-24T08:00:00Z",
          updated_at: "2026-08-24T08:00:00Z",
        });
      }
      if (url.endsWith("/mandates")) return json({ schema_version: "underwriting.v1", id: "mandate-1", project_id: "project-1" }, 201);
      if (url.endsWith("/scopes")) return json({ schema_version: "underwriting.v1", id: "scope-1", project_id: "project-1", payload: { primary_company_id: "company-1", target_security_ids: ["security-1"] } }, 201);
      if (url.endsWith("/agendas")) return json({ schema_version: "underwriting.v1", id: "agenda-1", project_id: "project-1", scope_id: "scope-1" }, 201);
      if (url.endsWith("/historical-bases")) return json({ schema_version: "underwriting.v1", id: "basis-1" }, 201);
      if (url.endsWith("/price-snapshots")) return json({ schema_version: "underwriting.v1", id: "price-1", security_identity_id: "security-identity-1" }, 201);
      if (url.endsWith("/capital-structure-snapshots")) return json({ schema_version: "underwriting.v1", id: "capital-1", company_id: "company-1" }, 201);
      if (url.endsWith("/security-rights")) return json({ schema_version: "underwriting.v1", id: "rights-1", security_identity_id: "security-identity-1" }, 201);
      if (url.endsWith("/draft") && init?.method === "PATCH") {
        return json({
          schema_version: "underwriting.v1",
          id: "draft-1",
          project_id: "project-1",
          base_revision_id: null,
          lock_version: 2,
          content: {
            schema_version: "underwriting.v1",
            publication_status: "draft",
            mandate_id: "mandate-1",
            scope_id: "scope-1",
            agenda_id: "agenda-1",
            historical_basis_id: "basis-1",
            price_snapshot_ids: ["price-1"],
            fx_snapshot_ids: [],
            capital_structure_snapshot_id: "capital-1",
            security_rights_ids: ["rights-1"],
            user_focus: "验证长期竞争力",
          },
          created_at: "2026-08-24T08:00:00Z",
          updated_at: "2026-08-24T08:05:00Z",
        });
      }
      if (url.endsWith("/publication-preview")) {
        return json({
          schema_version: "underwriting.v1",
          project_id: "project-1",
          expected_lock_version: 2,
          boundary_as_of: "2026-08-24T08:05:00Z",
          assessment: {
            schema_version: "underwriting.v1",
            answerability: "not_answerable",
            direction: null,
            confidence: null,
            publication_status: "user_frozen",
            blockers: ["missing_key_baseline"],
            resolution_requirements: ["补齐关键基线证据"],
            next_review_at: null,
            parent_assessment_id: null,
            content_hash: hash,
          },
          boundary: {
            schema_version: "underwriting.v1",
            historical_basis_id: "basis-1",
            mandate_id: "mandate-1",
            scope_id: "scope-1",
            agenda_id: "agenda-1",
            price_snapshot_ids: ["price-1"],
            fx_snapshot_ids: [],
            capital_structure_snapshot_id: "capital-1",
            security_rights_ids: ["rights-1"],
            parent_revision_id: null,
          },
          boundary_hash: hash,
          manifest: {
            schema_version: "underwriting.research-revision-manifest.v1",
            project_id: "project-1",
            project_ref: { project_id: "project-1", content_hash: hash },
            project_membership_refs: [],
            primary_object_id: "company-1",
            boundary_ref: "$boundary",
            mandate_id: "mandate-1",
            scope_id: "scope-1",
            agenda_id: "agenda-1",
            historical_basis_id: "basis-1",
            price_snapshot_ids: ["price-1"],
            fx_snapshot_ids: [],
            capital_structure_snapshot_id: "capital-1",
            security_rights_ids: ["rights-1"],
            market_snapshot_refs: ["price:price-1"],
            model_refs: [],
            assessment_ref: "$assessment",
            memo_ref: null,
            parent_revision_id: null,
          },
          manifest_hash: hash,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchSpy);
    render(
      <MemoryRouter initialEntries={["/research/new"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await selectCatl(user);
    await user.click(screen.getByRole("button", { name: "提交身份账本校验" }));
    expect(await screen.findByText(/关系已确认/)).toBeVisible();

    const values: Array<[string, string]> = [
      ["研究期限（年）", "3"],
      ["必要回报率", "0.12"],
      ["永久损失上限", "0.25"],
      ["比较集合", "全球动力电池企业"],
      ["生效时间", "2026-08-24T08:00"],
      ["覆盖业务分部", "动力电池"],
      ["研究焦点", "验证长期竞争力"],
      ["排除范围", "短期交易信号"],
      ["议程事项", "核验产能与利用率\n核验现金流质量"],
      ["模板标识", "foundation-agenda"],
      ["模板版本", "1"],
      ["议程输出哈希", hash],
      ["历史截止时间", "2026-08-24T08:00"],
      ["来源清单哈希", hash],
      ["定义包哈希", hash],
      ["解析器包哈希", hash],
      ["300750 价格", "210.50"],
      ["300750 市场时间", "2026-08-24T08:00"],
      ["300750 可用时间", "2026-08-24T08:05"],
      ["300750 价格来源", "exchange-close"],
      ["300750 价格原文哈希", hash],
      ["现金", "100"],
      ["债务", "40"],
      ["少数股东权益", "5"],
      ["投资资产", "10"],
      ["养老金负债", "0"],
      ["其他调整", "0"],
      ["基本股数", "4400"],
      ["稀释股数", "4410"],
      ["报告期开始", "2026-01-01T00:00"],
      ["报告期结束", "2026-06-30T23:59"],
      ["资本结构市场时间", "2026-08-24T08:00"],
      ["资本结构可用时间", "2026-08-24T08:05"],
      ["资本结构来源", "company-filing"],
      ["资本结构原文哈希", hash],
      ["300750 经济单位", "1"],
      ["300750 每单位投票权", "1"],
      ["300750 转换比例", "1"],
      ["300750 ADR 比例", "1"],
      ["300750 每单位分红权", "1"],
      ["300750 权利生效时间", "2026-08-24T08:00"],
      ["300750 权利来源", "company-filing"],
      ["300750 权利原文哈希", hash],
    ];
    for (const [label, value] of values) {
      const control = screen.getByLabelText(label);
      await user.clear(control);
      await user.type(control, value);
    }
    await user.selectOptions(screen.getByLabelText("基础货币"), "CNY");
    await user.click(screen.getByRole("button", { name: "建立版本边界并进入工作台" }));

    expect(await screen.findByRole("heading", { name: "研究工作台" })).toBeVisible();
    expect(screen.getByText("insufficient_evidence")).toBeVisible();
    expect(requestUrls).toEqual(expect.arrayContaining([
      "POST /api/underwriting/v1/product/projects",
      "POST /api/underwriting/v1/product/projects/project-1/mandates",
      "POST /api/underwriting/v1/product/projects/project-1/scopes",
      "POST /api/underwriting/v1/product/projects/project-1/agendas",
      "POST /api/underwriting/v1/product/historical-bases",
      "POST /api/underwriting/v1/product/market/price-snapshots",
      "POST /api/underwriting/v1/product/market/capital-structure-snapshots",
      "POST /api/underwriting/v1/product/market/security-rights",
      "PATCH /api/underwriting/v1/product/projects/project-1/draft",
      "POST /api/underwriting/v1/product/projects/project-1/publication-preview",
    ]));
    const agendaIndex = requestUrls.findIndex((item) => item.endsWith("/agendas"));
    const scopeIndex = requestUrls.findIndex((item) => item.endsWith("/scopes"));
    const patchIndex = requestUrls.findIndex((item) => item.endsWith("/draft") && item.startsWith("PATCH"));
    expect(agendaIndex).toBeGreaterThan(scopeIndex);
    expect(patchIndex).toBeGreaterThan(agendaIndex);
  });
});
