import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRoutes } from "../../app/routes";
import { ProductRouteErrorBoundary } from "../../app/ProductRouteErrorBoundary";
import routeEntrySource from "../../app/routes.tsx?raw";
import mainEntrySource from "../../main.tsx?raw";
import shellSource from "../../app/InvestmentResearchShell.tsx?raw";
import apiSource from "../../data/investmentResearchApi.ts?raw";
import { investmentResearchApi, type CompanyResearchWorkspace, type ProductProject } from "../../data/investmentResearchApi";
import homeSource from "./ResearchHomePage.tsx?raw";
import setupSource from "./NewResearchPage.tsx?raw";
import workbenchSource from "./ResearchWorkbenchPage.tsx?raw";

const hash = "a".repeat(64);
const uid = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const shellIds = { older: uid(1), newer: uid(2), project: uid(3), company: uid(4), security: uid(5), companyVersion: uid(6), securityVersion: uid(7), industry: uid(8), industryVersion: uid(9), draft: uid(10), mandate: uid(11), scope: uid(12), agenda: uid(13), basis: uid(14), price: uid(15), capital: uid(16), rights: uid(17), membership: uid(18) };

function shellProject(id = shellIds.project, createdAt = "2026-08-24T08:00:00Z"): ProductProject {
  return { schema_version: "underwriting.v1", id, primary_company_id: shellIds.company, target_security_ids: [shellIds.security], company_identity: { schema_version: "underwriting.v1", object_id: shellIds.company, identity_version_id: shellIds.companyVersion, canonical_name: "宁德时代" }, security_identities: [{ schema_version: "underwriting.v1", object_id: shellIds.security, identity_version_id: shellIds.securityVersion, canonical_name: "宁德时代 A 股", symbol: "300750", exchange: "SZSE", share_class: "A", trading_currency: "CNY" }], content_hash: hash, created_at: createdAt };
}

function shellDraft() {
  return { schema_version: "underwriting.v1", id: shellIds.draft, project_id: shellIds.project, base_revision_id: null, lock_version: 4, content: { schema_version: "underwriting.v1", publication_status: "draft", mandate_id: shellIds.mandate, scope_id: shellIds.scope, agenda_id: shellIds.agenda, historical_basis_id: shellIds.basis, price_snapshot_ids: [shellIds.price], fx_snapshot_ids: [], capital_structure_snapshot_id: shellIds.capital, security_rights_ids: [shellIds.rights], user_focus: "验证长期竞争力" }, created_at: "2026-08-24T08:00:00Z", updated_at: "2026-08-24T08:05:00Z" };
}

function shellPreview() {
  return { schema_version: "underwriting.v1", project_id: shellIds.project, expected_lock_version: 4, boundary_as_of: "2026-08-24T08:05:00Z", assessment: { schema_version: "underwriting.v1", answerability: "not_answerable", direction: null, confidence: null, publication_status: "user_frozen", blockers: ["missing_key_baseline"], resolution_requirements: ["核验行业有效产能与利用率口径"], next_review_at: null, parent_assessment_id: null, content_hash: hash }, boundary: { schema_version: "underwriting.v1", historical_basis_id: shellIds.basis, mandate_id: shellIds.mandate, scope_id: shellIds.scope, agenda_id: shellIds.agenda, price_snapshot_ids: [shellIds.price], fx_snapshot_ids: [], capital_structure_snapshot_id: shellIds.capital, security_rights_ids: [shellIds.rights], parent_revision_id: null }, boundary_hash: hash, manifest: { schema_version: "underwriting.research-revision-manifest.v1", project_id: shellIds.project, project_ref: { project_id: shellIds.project, content_hash: hash }, project_membership_refs: [{ membership_id: shellIds.membership, security_id: shellIds.security, content_hash: hash }], primary_object_id: shellIds.company, boundary_ref: "$boundary", mandate_id: shellIds.mandate, scope_id: shellIds.scope, agenda_id: shellIds.agenda, historical_basis_id: shellIds.basis, price_snapshot_ids: [shellIds.price], fx_snapshot_ids: [], capital_structure_snapshot_id: shellIds.capital, security_rights_ids: [shellIds.rights], market_snapshot_refs: [`price:${shellIds.price}`, `capital_structure:${shellIds.capital}`, `security_rights:${shellIds.rights}`], model_refs: [], assessment_ref: "$assessment", memo_ref: null, parent_revision_id: null }, manifest_hash: hash };
}

function shellWorkspace(projectId = shellIds.project): CompanyResearchWorkspace {
  const source = { kind: "external", fact_key: "reported_revenue", source_role: "filing", source_url: "https://example.test/filing", source_locator: "annual report p. 10", raw_hash: hash };
  const evidence = { schema_version: "underwriting.v1", id: shellIds.agenda, project_id: projectId, kind: "evidence_index", version: 1, input_hash: hash, content_hash: hash, source_refs: [{ source_role: "filing", source_url: source.source_url, source_locator: source.source_locator, raw_hash: hash }], payload: { fixture_content_hash: hash, cutoff: "2026-08-24T08:05:00Z", company_external_key: "CATL:COMPANY", security_external_keys: ["SZSE:300750"], facts: [{ fact_key: "reported_revenue", company_external_key: "CATL:COMPANY", business_module: "battery", metric_key: "revenue", observation: { key: "revenue", value: "362013", unit: "CNY million", currency: "CNY", period: "FY2025", state: "reported", source_ref: source, gap_key: null, assumption_key: null }, period_start: "2025-01-01", period_end: "2025-12-31", published_at: "2026-03-01T00:00:00Z", available_at: "2026-03-01T00:00:00Z", source_role: "filing", source_url: source.source_url, source_locator: source.source_locator, raw_hash: hash }] } };
  const gaps = { schema_version: "underwriting.v1", id: shellIds.scope, project_id: projectId, kind: "research_gaps", version: 1, input_hash: hash, content_hash: hash, source_refs: [{ source_role: "filing", source_url: source.source_url, source_locator: source.source_locator, raw_hash: hash }], payload: { fixture_content_hash: hash, company_external_key: "CATL:COMPANY", gaps: [{ gap_key: "missing_key_baseline", business_module: "battery", reason: "核验行业有效产能与利用率口径" }] } };
  const keys = ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"];
  return { schema_version: "underwriting.v1", project_id: projectId, company: { schema_version: "underwriting.v1", object_id: shellIds.company, id: shellIds.company, external_key: "CATL:COMPANY", canonical_name: "宁德时代" }, preparation: { schema_version: "underwriting.v1", id: shellIds.draft, status: "awaiting_evidence_review", current_step: "research_gaps", progress: 25, error: null }, artifacts: [evidence, gaps] as CompanyResearchWorkspace["artifacts"], modules: keys.map((key) => ({ schema_version: "underwriting.v1", key, state: key === "evidence_and_gaps" ? "needs_review" : "preparing", artifact_refs: key === "evidence_and_gaps" ? [{ id: evidence.id, kind: evidence.kind, content_hash: evidence.content_hash }, { id: gaps.id, kind: gaps.kind, content_hash: gaps.content_hash }] : [], valuation_state: key === "scenarios_valuation_implied_expectations" ? "pending" : "not_applicable" })) as CompanyResearchWorkspace["modules"], source_count: 1, gap_count: 1, draft: { schema_version: "underwriting.v1", id: shellIds.draft, lock_version: 4, base_revision_id: null }, selected_revision: null, change_summary: { artifact_versions: { evidence_index: 1, research_gaps: 1 }, reviewed_fact_count: 0 } };
}

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function ProjectSwitcher({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(`/research/projects/${projectId}`)} type="button">切换项目</button>;
}

describe("independent investment research shell", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    ["/research", "AI 公司研究"],
    ["/research/new", "开始 AI 公司研究"],
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
    expect(screen.getAllByRole("link", { name: /开始 AI 研究|建立研究/ }).length).toBeGreaterThan(0);
  }, 10_000);

  it("keeps the AI research call to action primary and legacy surfaces under advanced tools", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/product/projects?")) {
        return json({ schema_version: "underwriting.v1", items: [] });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(
      <MemoryRouter initialEntries={["/research"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    const primary = await screen.findByRole("link", { name: "开始 AI 研究" });
    expect(primary).toHaveClass("ir-button--primary");
    const objectSearch = screen.getByRole("heading", { name: "查找研究对象" }).closest("section");
    expect(objectSearch).not.toHaveAttribute("aria-live");
    expect(objectSearch?.querySelector("form")).not.toHaveAttribute("aria-live");
    const advanced = screen.getByText("高级工具").closest("details");
    expect(advanced).not.toBeNull();
    expect(advanced).not.toHaveAttribute("open");
    expect(within(advanced as HTMLElement).getByRole("link", { name: "事件研究与基金披露" })).not.toHaveClass("ir-button--primary");
    expect(within(advanced as HTMLElement).queryByRole("link", { name: "基金披露" })).not.toBeInTheDocument();
    expect(within(advanced as HTMLElement).getByRole("link", { name: "历史档案" })).not.toHaveClass("ir-button--primary");
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

  it("keeps the product route entry disconnected from legacy shells, clients, and automatic research", () => {
    const entryGraph = `${routeEntrySource}\n${mainEntrySource}`;
    expect(entryGraph).not.toMatch(/import\s+[^;]*(AppShell|UnderwritingArchiveShell|researchClient|researchOsApi|mockResearchOsApi|AutomaticResearch)/);
    expect(routeEntrySource).toMatch(/lazy\(\(\) => import\("\.\/LegacyResearchRoutes"\)\)/);
    expect(routeEntrySource).toContain("window.location.reload()");
  });

  it("searches all three identity kinds and orders recent projects newest first", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/product/projects?")) {
        return json({
          schema_version: "underwriting.v1",
          items: [
            shellProject(shellIds.older, "2026-08-22T08:00:00Z"),
            shellProject(shellIds.newer, "2026-08-24T08:00:00Z"),
          ],
        });
      }
      if (url.includes("/product/objects?")) {
        return json({
          schema_version: "underwriting.v1",
          items: [
            { schema_version: "underwriting.v1", object_id: shellIds.company, identity_version_id: shellIds.companyVersion, kind: "company", external_key: "CATL:COMPANY", canonical_name: "宁德时代", symbol: null, exchange: null, share_class: null, trading_currency: null },
            { schema_version: "underwriting.v1", object_id: shellIds.security, identity_version_id: shellIds.securityVersion, kind: "security", external_key: "SZSE:300750", canonical_name: "宁德时代 A 股", symbol: "300750", exchange: "SZSE", share_class: "A", trading_currency: "CNY" },
            { schema_version: "underwriting.v1", object_id: shellIds.industry, identity_version_id: shellIds.industryVersion, kind: "industry", external_key: "INDUSTRY:BATTERY", canonical_name: "动力电池", symbol: null, exchange: null, share_class: null, trading_currency: null },
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
      `/research/projects/${shellIds.newer}`,
      `/research/projects/${shellIds.older}`,
    ]);
    expect(within(list as HTMLElement).getAllByText("宁德时代")).toHaveLength(2);
    expect(within(list as HTMLElement).getAllByText(/300750 · A/)).toHaveLength(2);

    await user.type(screen.getByLabelText("搜索公司或证券"), "CATL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    const results = await screen.findByRole("list", { name: "对象搜索结果" });
    expect(within(results).getByText("Company")).toBeVisible();
    expect(within(results).getByText("Security")).toBeVisible();
    expect(within(results).getByText("Industry")).toBeVisible();
    expect(within(results).getByRole("link", { name: /查看相关公司/ })).toBeVisible();
  });

  it("renders the honest nine-module company workbench without requesting publication preview", async () => {
    const user = userEvent.setup();
    vi.spyOn(investmentResearchApi, "project").mockResolvedValue(shellProject());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(shellWorkspace());
    const previewSpy = vi.spyOn(investmentResearchApi, "preview");
    const fetchSpy = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith(`/product/projects/${shellIds.project}`)) return json(shellProject());
      if (url.endsWith(`/product/projects/${shellIds.project}/draft`)) return json(shellDraft());
      if (url.endsWith(`/product/projects/${shellIds.project}/publication-preview`)) return json(shellPreview());
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
      <MemoryRouter initialEntries={[`/research/projects/${shellIds.project}`]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "研究工作台" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "宁德时代" })).toBeVisible();
    expect(screen.getByText(/300750 · A · SZSE/)).toBeVisible();
    const modules = ["概览与当前判断", "Google 如何赚钱", "关键经营变量", "来源、事实与缺口", "行业、竞争与监管", "财务、现金流与资本配置", "情景、估值与当前价格隐含", "反证、风险与下一验证", "版本、变化与研究备忘录"];
    for (const module of modules) {
      expect(screen.getByRole("button", { name: new RegExp(module) })).toBeVisible();
    }
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    expect(screen.getByText(/missing_key_baseline/)).toBeVisible();
    expect(screen.getByText(/核验行业有效产能与利用率口径/)).toBeVisible();
    expect(document.body).not.toHaveTextContent(/target_price|action|推荐|仓位/i);

    await user.click(screen.getByRole("button", { name: /版本、变化与研究备忘录/ }));
    expect(screen.getByText(/版本、变化与研究备忘录正在准备/)).toBeVisible();
    expect(screen.queryByText(/当前草稿版本 4/)).not.toBeInTheDocument();
    expect(previewSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("never carries company workspace state across project route changes", async () => {
    const projectB = shellIds.newer;
    vi.spyOn(investmentResearchApi, "project").mockImplementation(async (projectId) => shellProject(projectId));
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockImplementation(async (projectId) => {
      if (projectId === projectB) throw new Error("B 项目工作区读取失败");
      return shellWorkspace(projectId);
    });
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith(`/projects/${shellIds.project}`)) return json(shellProject());
      if (url.endsWith(`/projects/${shellIds.project}/draft`)) return json(shellDraft());
      if (url.endsWith(`/projects/${shellIds.project}/publication-preview`)) return json(shellPreview());
      if (url.endsWith(`/projects/${projectB}`)) return json(shellProject(projectB));
      if (url.endsWith(`/projects/${projectB}/draft`)) return json({ ...shellDraft(), project_id: projectB, base_revision_id: null });
      if (url.endsWith(`/projects/${projectB}/publication-preview`)) return json({ schema_version: "underwriting.v1", error: { code: "preview_failed", message: "B 项目预览读取失败", request_id: "req-b", details: null } }, 503);
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/research/projects/${shellIds.project}`]}>
        <ProjectSwitcher projectId={projectB} />
        <ResearchOsRoutes />
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "宁德时代" });
    await user.click(screen.getByRole("button", { name: /来源、事实与缺口/ }));
    expect(screen.getByText(/missing_key_baseline/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "切换项目" }));
    expect(await screen.findByText("B 项目工作区读取失败")).toBeVisible();
    expect(screen.queryByText(/missing_key_baseline/)).not.toBeInTheDocument();
  });

  it("keeps unknown research URLs inside the product shell", async () => {
    render(
      <MemoryRouter initialEntries={["/research/not-a-product-route"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "研究页面不存在" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "投资研究导航" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "研究系统主导航" })).not.toBeInTheDocument();
  });

  it("lets a product lazy-load failure recover in place", async () => {
    let shouldThrow = true;
    function FragilePage() {
      if (shouldThrow) throw new Error("chunk unavailable");
      return <h1>页面已恢复</h1>;
    }
    const user = userEvent.setup();
    const retry = vi.fn(() => { shouldThrow = false; return true; });
    render(<ProductRouteErrorBoundary onRetry={retry}><FragilePage /></ProductRouteErrorBoundary>);
    expect(await screen.findByRole("alert")).toHaveTextContent("产品页面载入失败");
    await user.click(screen.getByRole("button", { name: "重试载入产品页面" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "页面已恢复" })).toBeVisible();
  });

  it("retries project and search reads, then carries a validated object into setup", async () => {
    let projectAttempts = 0;
    let searchAttempts = 0;
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/product/projects?")) {
        projectAttempts += 1;
        if (projectAttempts === 1) return json({ schema_version: "underwriting.v1", error: { code: "temporary", message: "目录读取失败", request_id: "req-home", details: null } }, 503);
        return json({ schema_version: "underwriting.v1", items: [] });
      }
      if (url.includes("/product/objects?")) {
        searchAttempts += 1;
        if (searchAttempts === 1) return json({ schema_version: "underwriting.v1", error: { code: "temporary", message: "搜索读取失败", request_id: "req-search", details: null } }, 503);
        return json({ schema_version: "underwriting.v1", items: [{ schema_version: "underwriting.v1", object_id: shellIds.company, identity_version_id: shellIds.companyVersion, kind: "company", external_key: "CATL:COMPANY", canonical_name: "宁德时代", symbol: null, exchange: null, share_class: null, trading_currency: null }] });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    render(<MemoryRouter initialEntries={["/research"]}><ResearchOsRoutes /></MemoryRouter>);

    expect(await screen.findByRole("alert")).toHaveTextContent("目录读取失败");
    await user.click(screen.getByRole("button", { name: "重试读取项目目录" }));
    expect(await screen.findByText("尚无独立研究项目")).toBeVisible();
    await user.type(screen.getByLabelText("搜索公司或证券"), "CATL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("搜索读取失败");
    await user.click(screen.getByRole("button", { name: "重试对象搜索" }));
    await user.click(await screen.findByRole("link", { name: /研究 宁德时代/ }));
    expect(await screen.findByRole("heading", { name: "开始 AI 公司研究" })).toBeVisible();
    expect(screen.getByRole("button", { name: "选择 宁德时代" })).toBeVisible();
  });

  it("retries the company workbench read in place", async () => {
    let projectAttempts = 0;
    let previewAttempts = 0;
    const user = userEvent.setup();
    vi.spyOn(investmentResearchApi, "project").mockRejectedValueOnce(new Error("项目读取失败")).mockResolvedValue(shellProject());
    vi.spyOn(investmentResearchApi, "companyResearchWorkspace").mockResolvedValue(shellWorkspace());
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith(`/projects/${shellIds.project}`)) {
        projectAttempts += 1;
        if (projectAttempts === 1) return json({ schema_version: "underwriting.v1", error: { code: "temporary", message: "项目读取失败", request_id: "req-project", details: null } }, 503);
        return json(shellProject());
      }
      if (url.endsWith(`/projects/${shellIds.project}/draft`)) return json(shellDraft());
      if (url.endsWith(`/projects/${shellIds.project}/publication-preview`)) {
        previewAttempts += 1;
        if (previewAttempts === 1) return json({ schema_version: "underwriting.v1", error: { code: "temporary", message: "预览读取失败", request_id: "req-preview", details: null } }, 503);
        return json(shellPreview());
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    render(<MemoryRouter initialEntries={[`/research/projects/${shellIds.project}`]}><ResearchOsRoutes /></MemoryRouter>);

    expect(await screen.findByRole("alert")).toHaveTextContent("项目读取失败");
    await user.click(screen.getByRole("button", { name: "重试读取研究项目" }));
    expect(await screen.findByRole("heading", { name: "宁德时代" })).toBeVisible();
  });
});
