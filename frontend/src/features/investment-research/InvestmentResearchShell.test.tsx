import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRoutes } from "../../app/routes";
import shellSource from "../../app/InvestmentResearchShell.tsx?raw";
import apiSource from "../../data/investmentResearchApi.ts?raw";
import homeSource from "./ResearchHomePage.tsx?raw";
import setupSource from "./NewResearchPage.tsx?raw";
import workbenchSource from "./ResearchWorkbenchPage.tsx?raw";

const hash = "a".repeat(64);

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("independent investment research shell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ["/research", "独立投资研究"],
    ["/research/new", "建立研究项目"],
  ])("renders %s outside legacy shells with product-only navigation", async (path, heading) => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/product/projects?")) {
        return json({ schema_version: "underwriting.v1", items: [] });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(
      <MemoryRouter initialEntries={[path]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    const nav = screen.getByRole("navigation", { name: "投资研究导航" });
    expect(nav).toHaveTextContent("研究目录");
    expect(nav).toHaveTextContent("建立研究");
    expect(screen.queryByRole("complementary", { name: "研究工作台导航" }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "不可变研究档案导航" }))
      .not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/事件|worker|基金|模拟持仓/i);
  });

  it("keeps forbidden Event Research clients, types and polling outside the product module graph", () => {
    const source = [shellSource, apiSource, homeSource, setupSource, workbenchSource].join("\n");

    expect(source).not.toMatch(/researchClient|researchOsApi|mockResearchOsApi/);
    expect(source).not.toMatch(/domain\/(eventResearch|automaticResearch)|EventResearch/);
    expect(source).not.toMatch(/setInterval|自动轮询|automatic-research/);
    expect(source.match(/from\s+["'][^"']+["']/g) ?? []).toEqual(expect.arrayContaining([
      expect.stringContaining("contracts/v1"),
      expect.stringContaining("investmentResearchApi"),
    ]));
  });

  it("searches all three identity kinds and orders recent projects newest first", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/product/projects?")) {
        return json({
          schema_version: "underwriting.v1",
          items: [
            { schema_version: "underwriting.v1", id: "older", primary_company_id: "company-old", target_security_ids: ["security-old"], content_hash: hash, created_at: "2026-08-22T08:00:00Z" },
            { schema_version: "underwriting.v1", id: "newer", primary_company_id: "company-new", target_security_ids: ["security-new"], content_hash: hash, created_at: "2026-08-24T08:00:00Z" },
          ],
        });
      }
      if (url.includes("/product/objects?")) {
        return json({
          schema_version: "underwriting.v1",
          items: [
            { schema_version: "underwriting.v1", object_id: "company-1", identity_version_id: "company-v1", kind: "company", external_key: "CATL:COMPANY", canonical_name: "宁德时代", symbol: null, exchange: null, share_class: null, trading_currency: null },
            { schema_version: "underwriting.v1", object_id: "security-1", identity_version_id: "security-v1", kind: "security", external_key: "SZSE:300750", canonical_name: "宁德时代 A 股", symbol: "300750", exchange: "SZSE", share_class: "A", trading_currency: "CNY" },
            { schema_version: "underwriting.v1", object_id: "industry-1", identity_version_id: "industry-v1", kind: "industry", external_key: "INDUSTRY:BATTERY", canonical_name: "动力电池", symbol: null, exchange: null, share_class: null, trading_currency: null },
          ],
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    render(
      <MemoryRouter initialEntries={["/research"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    const recent = await screen.findByRole("heading", { name: "最近项目" });
    const list = recent.closest("section");
    expect(list).not.toBeNull();
    const projectLinks = await within(list as HTMLElement).findAllByRole("link");
    expect(projectLinks.map((link) => link.getAttribute("href"))).toEqual([
      "/research/projects/newer",
      "/research/projects/older",
    ]);

    await user.type(screen.getByLabelText("搜索 Company、Security 或 Industry"), "CATL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    const results = await screen.findByRole("list", { name: "对象搜索结果" });
    expect(within(results).getByText("Company")).toBeVisible();
    expect(within(results).getByText("Security")).toBeVisible();
    expect(within(results).getByText("Industry")).toBeVisible();
    expect(within(results).getByText("仅浏览")).toBeVisible();
  });

  it("renders the honest nine-module workbench and an insufficient-evidence preview", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/product/projects/project-1")) {
        return json({
          schema_version: "underwriting.v1",
          id: "project-1",
          primary_company_id: "company-1",
          target_security_ids: ["security-1"],
          content_hash: hash,
          created_at: "2026-08-24T08:00:00Z",
        });
      }
      if (url.endsWith("/product/projects/project-1/draft")) {
        return json({
          schema_version: "underwriting.v1",
          id: "draft-1",
          project_id: "project-1",
          base_revision_id: null,
          lock_version: 4,
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
      if (url.endsWith("/product/projects/project-1/publication-preview")) {
        return json({
          schema_version: "underwriting.v1",
          project_id: "project-1",
          expected_lock_version: 4,
          boundary_as_of: "2026-08-24T08:05:00Z",
          assessment: {
            schema_version: "underwriting.v1",
            answerability: "not_answerable",
            direction: null,
            confidence: null,
            publication_status: "user_frozen",
            blockers: ["missing_key_baseline"],
            resolution_requirements: ["核验行业有效产能与利用率口径"],
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
      <MemoryRouter initialEntries={["/research/projects/project-1"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "研究工作台" })).toBeVisible();
    const modules = [
      "概览", "来源与证据", "行业", "公司模型", "预测与情景",
      "估值", "判断与反证", "版本与变化", "研究备忘录",
    ];
    for (const module of modules) {
      expect(screen.getByRole("button", { name: new RegExp(module) })).toBeVisible();
    }
    for (const module of modules.filter((item) => !["概览", "版本与变化"].includes(item))) {
      expect(screen.getByRole("button", { name: new RegExp(module) })).toBeDisabled();
    }
    expect(screen.getAllByText("尚未建立").length).toBeGreaterThanOrEqual(7);
    expect(screen.getByText("insufficient_evidence")).toBeVisible();
    expect(screen.getByText("missing_key_baseline")).toBeVisible();
    expect(screen.getByText("核验行业有效产能与利用率口径")).toBeVisible();
    expect(screen.getByText(/方向尚未形成/)).toBeVisible();
    expect(screen.getByText(/置信度尚未形成/)).toBeVisible();
    expect(document.body).not.toHaveTextContent(/target_price|action|推荐|仓位/i);

    await user.click(screen.getByRole("button", { name: /版本与变化/ }));
    expect(screen.getByText(/当前草稿版本 4/)).toBeVisible();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(3));
  });
});
