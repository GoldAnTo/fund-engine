import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRoutes } from "../../app/routes";

const uid = (value: number) => `10000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const ids = { company: uid(1), googl: uid(2), goog: uid(3), industry: uid(4), project: uid(5) };
const hash = "a".repeat(64);
const cutoff = "2026-08-25T00:00:00Z";
const dto = { schema_version: "underwriting.v1" } as const;

const objects = [
  { ...dto, object_id: ids.company, identity_version_id: uid(10), kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null },
  { ...dto, object_id: ids.googl, identity_version_id: uid(11), kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
  { ...dto, object_id: ids.goog, identity_version_id: uid(12), kind: "security", external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
  { ...dto, object_id: ids.industry, identity_version_id: uid(13), kind: "industry", external_key: "INDUSTRY:INTERNET", canonical_name: "互联网平台", symbol: null, exchange: null, share_class: null, trading_currency: null },
] as const;

const industryCompanyItems = [
  { ...dto, object_id: ids.company, kind: "company", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null },
  { ...dto, object_id: ids.googl, kind: "security", external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
  { ...dto, object_id: ids.goog, kind: "security", external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
] as const;

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-request-id": "req-new" } });
}

function preview(requestCutoff = cutoff) {
  return {
    ...dto,
    company: { ...dto, object_id: ids.company, external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    securities: [
      { ...dto, object_id: ids.googl, external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
      { ...dto, object_id: ids.goog, external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
    ],
    strategy_version: "company-research-default.v1",
    horizon_years: 5,
    base_currency: "CNY",
    required_return: "0.12",
    permanent_loss_limit: "0.25",
    cutoff_at: requestCutoff,
    agenda: ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"].map((key) => ({ ...dto, key, label: key })),
    preview_hash: hash,
  };
}

function initialized() {
  return { ...dto, project_id: ids.project, company_id: ids.company, preparation: { ...dto, id: uid(20), project_id: ids.project, request_hash: hash, strategy_version: "company-research-default.v1", status: "queued", current_step: "evidence_index", progress: 0, attempt: 1, next_attempt_at: null, last_error_code: null } };
}

type Request = { url: string; init: RequestInit | undefined };
function server({ loseFirstInitialization = false, searchItems = objects, industryItems = industryCompanyItems, loseFirstIndustryBrowse = false }: { loseFirstInitialization?: boolean; searchItems?: readonly object[]; industryItems?: readonly object[]; loseFirstIndustryBrowse?: boolean } = {}) {
  let initializationAttempts = 0;
  let industryBrowseAttempts = 0;
  const requests: Request[] = [];
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.includes("/product/objects?")) return json({ ...dto, items: searchItems });
    if (url.includes(`/product/industries/${ids.industry}/companies?`)) {
      industryBrowseAttempts += 1;
      if (loseFirstIndustryBrowse && industryBrowseAttempts === 1) throw new TypeError("offline");
      return json({ ...dto, items: industryItems });
    }
    if (url.endsWith("/company-research/preview")) {
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as { cutoff_at: string } : null;
      return json(preview(body?.cutoff_at));
    }
    if (url.endsWith("/company-research/initializations")) {
      initializationAttempts += 1;
      if (loseFirstInitialization && initializationAttempts === 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        throw new TypeError("connection lost after write");
      }
      return json(initialized(), 201);
    }
    if (url.includes(`/product/projects/${ids.project}`)) return json({ ...dto, error: { code: "not_ready", message: "项目正在准备", request_id: "req-project", details: null } }, 503);
    throw new Error(`unexpected request: ${url}`);
  });
  return { fetch, requests };
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路径">{location.pathname}</output>;
}

function renderPage(initialEntry: string | { pathname: string; state: unknown } = "/research/new") {
  render(<MemoryRouter initialEntries={[initialEntry]}><ResearchOsRoutes /><LocationProbe /></MemoryRouter>);
}

function deferred<T>() {
  let resolve: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve: resolve! };
}

async function selectAlphabet(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "Google");
  await user.click(screen.getByRole("button", { name: "搜索对象" }));
  await screen.findByRole("region", { name: "对象搜索结果" });
  await user.click(screen.getByRole("button", { name: "研究 Alphabet" }));
}

describe("company research entry", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("takes Google through one default plan into the new project route without internal setup fields", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server().fetch);
    renderPage();

    expect(await screen.findByRole("heading", { name: "选择研究公司" })).toBeVisible();
    await selectAlphabet(user);
    expect(await screen.findByRole("heading", { name: "确认默认研究方案" })).toBeVisible();
    expect(screen.getByText("研究对象：Alphabet Inc.")).toBeVisible();
    expect(screen.getByText("关联证券：GOOGL Class A；GOOG Class C")).toBeVisible();
    expect(screen.getByText("研究期限：5 年")).toBeVisible();
    expect(screen.getByText("基准币种：CNY")).toBeVisible();
    expect(screen.getByText("最低要求回报：12%")).toBeVisible();
    expect(screen.getByText("永久损失边界：25%")).toBeVisible();
    expect(screen.getByText(/资料截止：/)).toBeVisible();
    expect(screen.getByText("系统将准备：业务地图、经营驱动、证据与缺口、财务桥、三种情景、DCF/反向 DCF、反证与版本")).toBeVisible();
    for (const forbidden of ["研究任务与边界", "来源清单哈希", "资本结构市场时间", "每单位投票权", "价格市场时间", "revision boundary"]) expect(screen.queryByText(forbidden)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "开始研究" }));
    await waitFor(() => expect(screen.getByLabelText("当前路径")).toHaveTextContent(`/research/projects/${ids.project}`));
  });

  it("keeps an initialization idempotency key for retry after a lost response and locks synchronous double submits", async () => {
    const user = userEvent.setup();
    const product = server({ loseFirstInitialization: true });
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    await selectAlphabet(user);
    await screen.findByRole("heading", { name: "确认默认研究方案" });

    const start = screen.getByRole("button", { name: "开始研究" });
    fireEvent.click(start);
    fireEvent.click(start);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接投资研究服务");
    await user.click(screen.getByRole("button", { name: "重试开始研究" }));
    await waitFor(() => expect(screen.getByLabelText("当前路径")).toHaveTextContent(`/research/projects/${ids.project}`));
    const initializations = product.requests.filter((request) => request.url.endsWith("/company-research/initializations"));
    expect(initializations).toHaveLength(2);
    const keys = initializations.map((request) => new Headers(request.init?.headers).get("Idempotency-Key"));
    expect(keys).toEqual([expect.any(String), expect.any(String)]);
    expect(keys[0]).toBe(keys[1]);
  });

  it("keeps industry results browseable and keeps the company as a Security search primary action", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server().fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(results).toHaveTextContent("GOOGL");
    expect(results).toHaveTextContent("GOOG");
    expect(screen.getByRole("button", { name: "研究 Alphabet" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /研究 GOOGL|研究 GOOG/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看相关公司" })).toBeVisible();
  });

  it("invalidates an in-flight default-plan preview when a new search begins", async () => {
    const user = userEvent.setup();
    const delayedPreview = deferred<Response>();
    const requests: Request[] = [];
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes("/product/objects?")) return json({ ...dto, items: objects });
      if (url.endsWith("/company-research/preview")) return delayedPreview.promise;
      throw new Error(`unexpected request: ${url}`);
    }));
    renderPage();

    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await user.click(screen.getByRole("button", { name: "研究 Alphabet" }));
    expect(await screen.findByRole("button", { name: "正在准备默认方案" })).toBeVisible();

    const input = screen.getByLabelText("搜索公司、证券或行业");
    await user.clear(input);
    await user.type(input, "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await act(async () => {
      delayedPreview.resolve(json(preview()));
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "选择研究公司" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "确认默认研究方案" })).not.toBeInTheDocument();
    expect(requests.filter((request) => request.url.includes("/product/objects?"))).toHaveLength(2);
  });

  it("reloads a home-seeded company as its complete company group before research can start", async () => {
    const user = userEvent.setup();
    const delayedSearch = deferred<Response>();
    const requests: Request[] = [];
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes("/product/objects?")) return delayedSearch.promise;
      if (url.endsWith("/company-research/preview")) {
        const body = typeof init?.body === "string" ? JSON.parse(init.body) as { cutoff_at: string } : null;
        return json(preview(body?.cutoff_at));
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    renderPage({ pathname: "/research/new", state: { seedObject: objects[0] } });

    expect(await screen.findByRole("heading", { name: "选择研究公司" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "研究 Alphabet" })).not.toBeInTheDocument();
    delayedSearch.resolve(json({ ...dto, items: objects }));
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(results).toHaveTextContent("GOOGL");
    expect(results).toHaveTextContent("GOOG");
    expect(requests[0]?.url).toContain("query=US%3AALPHABET%3ACOMPANY");

    await user.click(screen.getByRole("button", { name: "研究 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认默认研究方案" })).toBeVisible();
  });

  it("retries a failed home-seeded full-company lookup with its stable external key", async () => {
    const user = userEvent.setup();
    const requests: Request[] = [];
    let searchAttempts = 0;
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes("/product/objects?")) {
        searchAttempts += 1;
        if (searchAttempts === 1) throw new TypeError("offline");
        return json({ ...dto, items: objects });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    renderPage({ pathname: "/research/new", state: { seedObject: objects[0] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接投资研究服务");
    await user.click(screen.getByRole("button", { name: "重试对象搜索" }));

    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(results).toHaveTextContent("GOOGL");
    expect(results).toHaveTextContent("GOOG");
    expect(requests).toHaveLength(2);
    expect(requests.map((request) => request.url)).toEqual([
      expect.stringContaining("query=US%3AALPHABET%3ACOMPANY"),
      expect.stringContaining("query=US%3AALPHABET%3ACOMPANY"),
    ]);
  });

  it("browses an industry's complete Company group through its dedicated route and starts only the chosen Company", async () => {
    const user = userEvent.setup();
    const product = server();
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    const related = await screen.findByRole("region", { name: "行业相关公司" });
    expect(related).toHaveTextContent("Alphabet Inc.");
    expect(related).toHaveTextContent("GOOGL Class A；GOOG Class C");
    const browseRequest = product.requests.find((request) => request.url.includes(`/product/industries/${ids.industry}/companies?`));
    expect(browseRequest?.url).toContain(`as_of=`);
    expect(screen.queryByRole("button", { name: /研究 互联网平台/ })).not.toBeInTheDocument();

    await user.click(within(related).getByRole("button", { name: "研究 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认默认研究方案" })).toBeVisible();
    const previewRequest = product.requests.find((request) => request.url.endsWith("/company-research/preview"));
    expect(JSON.parse(String(previewRequest?.init?.body))).toMatchObject({ company_id: ids.company });
  });

  it("shows an empty industry browse result and retries a failed industry browse", async () => {
    const user = userEvent.setup();
    const product = server({ industryItems: [], loseFirstIndustryBrowse: true });
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接投资研究服务");
    await user.click(screen.getByRole("button", { name: "重试相关公司" }));
    expect(await screen.findByText("该行业暂未配置可研究公司。")).toBeVisible();
    expect(product.requests.filter((request) => request.url.includes(`/product/industries/${ids.industry}/companies?`))).toHaveLength(2);
  });

  it("ignores a stale industry browse after a new object search starts", async () => {
    const user = userEvent.setup();
    const delayedBrowse = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (input) => {
      const url = String(input);
      if (url.includes("/product/objects?")) return json({ ...dto, items: objects });
      if (url.includes(`/product/industries/${ids.industry}/companies?`)) return delayedBrowse.promise;
      throw new Error(`unexpected request: ${url}`);
    }));
    renderPage();
    const input = await screen.findByLabelText("搜索公司、证券或行业");
    await user.type(input, "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    await user.clear(input);
    await user.type(input, "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await act(async () => { delayedBrowse.resolve(json({ ...dto, items: industryCompanyItems })); await Promise.resolve(); });

    expect(screen.queryByRole("region", { name: "行业相关公司" })).not.toBeInTheDocument();
  });

  it("announces empty search results", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ searchItems: [] }).fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司、证券或行业"), "missing");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    expect(await screen.findByText(/没有找到匹配/)).toBeVisible();
  });
});
