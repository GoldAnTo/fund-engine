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
const pythonWhitespace = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;

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

const industryCompanyWithoutSecurity = {
  ...dto,
  object_id: uid(6),
  kind: "company",
  external_key: "US:PRIVATE:COMPANY",
  canonical_name: "Private Company",
  symbol: null,
  exchange: null,
  share_class: null,
  trading_currency: null,
} as const;

const betaCompany = { ...dto, object_id: uid(30), identity_version_id: uid(31), kind: "company", external_key: "US:BETA:COMPANY", canonical_name: "Beta Inc.", symbol: null, exchange: null, share_class: null, trading_currency: null } as const;
const betaSecurity = { ...dto, object_id: uid(32), identity_version_id: uid(33), kind: "security", external_key: "NASDAQ:BETA", canonical_name: "Beta Inc. Common", symbol: "BETA", exchange: "NASDAQ", share_class: "Common", trading_currency: "USD" } as const;
const privateCompany = { ...industryCompanyWithoutSecurity, identity_version_id: uid(34) } as const;

function json(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "x-request-id": "req-new" } });
}

function canonicalFocus(value: string | null | undefined): string | null {
  if (value == null) return null;
  return value.replace(pythonWhitespace, "").normalize("NFC");
}

function preview(requestCutoff = cutoff, focusQuestion: string | null = null) {
  return {
    ...dto,
    company: { ...dto, object_id: ids.company, external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." },
    securities: [
      { ...dto, object_id: ids.googl, external_key: "NASDAQ:GOOGL", canonical_name: "Alphabet Inc. Class A", symbol: "GOOGL", exchange: "NASDAQ", share_class: "Class A", trading_currency: "USD" },
      { ...dto, object_id: ids.goog, external_key: "NASDAQ:GOOG", canonical_name: "Alphabet Inc. Class C", symbol: "GOOG", exchange: "NASDAQ", share_class: "Class C", trading_currency: "USD" },
    ],
    strategy_version: "company-research-mainline.v1",
    horizon_years: 5,
    base_currency: "CNY",
    required_return: "0.12",
    permanent_loss_limit: "0.25",
    cutoff_at: requestCutoff,
    focus_question: focusQuestion,
    agenda: ["overview", "business_map", "operating_drivers", "evidence_and_gaps", "industry_competition_regulation", "financials_cash_flow_capital_allocation", "scenarios_valuation_implied_expectations", "counterevidence_risks_next_checks", "versions_changes_memo"].map((key) => ({ ...dto, key, label: key })),
    preview_hash: hash,
  };
}

function initialized(strategyVersion: unknown = "company-research-mainline.v1") {
  return { ...dto, project_id: ids.project, company_id: ids.company, preparation: { ...dto, id: uid(20), project_id: ids.project, request_hash: hash, strategy_version: strategyVersion, status: "queued", current_step: "evidence_index", progress: 0, attempt: 1, next_attempt_at: null, last_error_code: null } };
}

type Request = { url: string; init: RequestInit | undefined };
function server({ loseFirstInitialization = false, searchItems = objects, industryItems = industryCompanyItems, loseFirstIndustryBrowse = false, initializationStrategy = "company-research-mainline.v1" }: { loseFirstInitialization?: boolean; searchItems?: readonly object[]; industryItems?: readonly object[]; loseFirstIndustryBrowse?: boolean; initializationStrategy?: unknown } = {}) {
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
      return json({ ...dto, industry_id: ids.industry, items: industryItems });
    }
    if (url.endsWith("/company-research/preview")) {
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as { cutoff_at: string; focus_question?: string | null } : null;
      return json(preview(body?.cutoff_at, canonicalFocus(body?.focus_question)));
    }
    if (url.endsWith("/company-research/initializations")) {
      initializationAttempts += 1;
      if (loseFirstInitialization && initializationAttempts === 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        throw new TypeError("connection lost after write");
      }
      return json(initialized(initializationStrategy), 201);
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
  await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
  await user.click(screen.getByRole("button", { name: "搜索对象" }));
  await screen.findByRole("region", { name: "对象搜索结果" });
  await user.click(screen.getByRole("button", { name: "选择 Alphabet" }));
}

describe("company research entry", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("takes the keyboard path from a canonical Company group and optional focus question directly into AI research", async () => {
    const user = userEvent.setup();
    const product = server();
    vi.stubGlobal("fetch", product.fetch);
    renderPage();

    expect(await screen.findByRole("heading", { name: "开始 AI 公司研究" })).toBeVisible();
    await user.type(screen.getByLabelText("可选关注点"), "云业务增长能否抵消搜索广告放缓？");
    await user.type(screen.getByLabelText("搜索公司或证券"), "Google{Enter}");
    await screen.findByRole("region", { name: "对象搜索结果" });
    const chooseCompany = screen.getByRole("button", { name: "选择 Alphabet" });
    chooseCompany.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Alphabet Inc." })).toBeVisible();
    expect(screen.getByText("GOOGL Class A；GOOG Class C")).toBeVisible();
    expect(screen.getByText("云业务增长能否抵消搜索广告放缓？")).toBeVisible();
    const settings = screen.getByText("研究设置").closest("details");
    expect(settings).not.toBeNull();
    expect(settings).not.toHaveAttribute("open");
    await user.click(screen.getByText("研究设置"));
    expect(within(settings as HTMLElement).getByText("研究期限：5 年")).toBeVisible();
    expect(within(settings as HTMLElement).getByText("基准币种：CNY")).toBeVisible();
    expect(within(settings as HTMLElement).getByText("最低要求回报：12%")).toBeVisible();
    expect(within(settings as HTMLElement).getByText("永久损失边界：25%")).toBeVisible();
    expect(within(settings as HTMLElement).getByText(/资料截止：/)).toBeVisible();
    expect(screen.queryByLabelText(/研究期限|基准币种|最低要求回报|永久损失边界/)).not.toBeInTheDocument();
    for (const forbidden of ["研究任务与边界", "来源清单哈希", "资本结构市场时间", "每单位投票权", "价格市场时间", "revision boundary"]) expect(screen.queryByText(forbidden)).not.toBeInTheDocument();

    const startResearch = screen.getByRole("button", { name: "开始 AI 研究" });
    startResearch.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByLabelText("当前路径")).toHaveTextContent(`/research/projects/${ids.project}`));
    const bodyFor = (suffix: string) => {
      const request = product.requests.find((item) => item.url.endsWith(suffix));
      return JSON.parse(String(request?.init?.body)) as Record<string, unknown>;
    };
    const previewRequest = bodyFor("/company-research/preview");
    const initializationRequest = bodyFor("/company-research/initializations");
    expect(previewRequest.focus_question).toBe("云业务增长能否抵消搜索广告放缓？");
    expect(initializationRequest.focus_question).toBe("云业务增长能否抵消搜索广告放缓？");
    expect(initializationRequest.preview_hash).toBe(hash);
  });

  it("keeps an initialization idempotency key for retry after a lost response and locks synchronous double submits", async () => {
    const user = userEvent.setup();
    const product = server({ loseFirstInitialization: true });
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    await selectAlphabet(user);
    await screen.findByRole("heading", { name: "确认研究对象" });

    const start = screen.getByRole("button", { name: "开始 AI 研究" });
    fireEvent.click(start);
    expect(screen.getByRole("button", { name: "正在启动 AI 研究" })).toBeDisabled();
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

  it("accepts the legacy initialization strategy", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ initializationStrategy: "company-research-default.v1" }).fetch);
    renderPage();
    await selectAlphabet(user);
    await user.click(await screen.findByRole("button", { name: "开始 AI 研究" }));
    await waitFor(() => expect(screen.getByLabelText("当前路径")).toHaveTextContent(`/research/projects/${ids.project}`));
  });

  it.each([
    ["unknown", "company-research-unknown.v1"],
    ["non-string", ["company-research-mainline.v1"]],
  ])("rejects an %s initialization strategy", async (_label, strategyVersion) => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ initializationStrategy: strategyVersion }).fetch);
    renderPage();
    await selectAlphabet(user);
    await user.click(await screen.findByRole("button", { name: "开始 AI 研究" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("投资研究服务返回了无法验证的数据");
  });

  it("keeps industry results browseable and keeps the company as a Security search primary action", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server().fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司或证券"), "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(results).toHaveTextContent("GOOGL");
    expect(results).toHaveTextContent("GOOG");
    expect(screen.getByRole("button", { name: "选择 Alphabet" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /研究 GOOGL|研究 GOOG/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看相关公司" })).toBeVisible();
  });

  it("keeps every search-result Security inside its canonical Company group", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({
      searchItems: [...objects.slice(0, 3), betaCompany, betaSecurity, privateCompany],
    }).fetch);
    renderPage();

    await user.type(await screen.findByLabelText("搜索公司或证券"), "platform{Enter}");
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    const alphabetCard = within(results).getByRole("heading", { name: "Alphabet Inc." }).closest("article");
    const betaCard = within(results).getByRole("heading", { name: "Beta Inc." }).closest("article");
    const privateCard = within(results).getByRole("heading", { name: "Private Company" }).closest("article");

    expect(alphabetCard).not.toBeNull();
    expect(betaCard).not.toBeNull();
    expect(privateCard).not.toBeNull();
    expect(within(alphabetCard as HTMLElement).getByText(/GOOGL Class A；GOOG Class C/)).toBeVisible();
    expect(within(alphabetCard as HTMLElement).queryByText(/BETA Common/)).not.toBeInTheDocument();
    expect(within(betaCard as HTMLElement).getByText(/BETA Common/)).toBeVisible();
    expect(within(betaCard as HTMLElement).queryByText(/GOOGL|GOOG/)).not.toBeInTheDocument();
    expect(within(privateCard as HTMLElement).getByRole("button", { name: "选择 Private Company" })).toBeDisabled();
  });

  it("matches backend Unicode focus canonicalization and counts code points", async () => {
    const user = userEvent.setup();
    const product = server();
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    const focus = await screen.findByLabelText("可选关注点");
    const fiveHundredEmoji = "😀".repeat(500);

    fireEvent.change(focus, { target: { value: `\u0085\u3000${fiveHundredEmoji}\u00a0` } });
    expect(screen.getByText("500/500，关注点只影响研究议程，不改变资料和模型边界。")).toBeVisible();
    await selectAlphabet(user);
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
    expect(screen.getByText(fiveHundredEmoji)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "返回选择公司" }));
    fireEvent.change(screen.getByLabelText("可选关注点"), { target: { value: "😀".repeat(501) } });
    expect(screen.getByText(/501\/500/)).toBeVisible();
    expect(screen.getByRole("button", { name: "选择 Alphabet" })).toBeDisabled();
  });

  it("discards a delayed preview when the focus changes and accepts a fresh preview", async () => {
    const user = userEvent.setup();
    const delayedPreview = deferred<Response>();
    let previewAttempts = 0;
    vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = String(input);
      if (url.includes("/product/objects?")) return json({ ...dto, items: objects });
      if (url.endsWith("/company-research/preview")) {
        previewAttempts += 1;
        const body = typeof init?.body === "string" ? JSON.parse(init.body) as { cutoff_at: string; focus_question: string | null } : null;
        if (previewAttempts === 1) return delayedPreview.promise;
        return json(preview(body?.cutoff_at, canonicalFocus(body?.focus_question)));
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    renderPage();
    fireEvent.change(await screen.findByLabelText("可选关注点"), { target: { value: "旧关注点" } });
    await selectAlphabet(user);
    expect(await screen.findByRole("button", { name: "正在确认研究对象" })).toBeVisible();

    fireEvent.change(screen.getByLabelText("可选关注点"), { target: { value: "新关注点" } });
    expect(screen.getByRole("button", { name: "选择 Alphabet" })).toBeEnabled();
    await act(async () => {
      delayedPreview.resolve(json(preview(cutoff, "旧关注点")));
      await Promise.resolve();
    });
    expect(screen.queryByRole("heading", { name: "确认研究对象" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "选择 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
    expect(screen.getByText("新关注点")).toBeVisible();
    expect(previewAttempts).toBe(2);
  });

  it("keeps the form and character count out of live regions and announces only the limit error", async () => {
    renderPage();
    const heading = await screen.findByRole("heading", { name: "研究对象" });
    const section = heading.closest("section");
    const form = heading.parentElement?.parentElement?.querySelector("form");
    const focus = screen.getByLabelText("可选关注点");
    const help = screen.getByText(/^0\/500/);

    expect(section).not.toHaveAttribute("aria-live");
    expect(form).not.toHaveAttribute("aria-live");
    expect(help).not.toHaveAttribute("aria-live");
    expect(help).not.toHaveAttribute("role");
    fireEvent.change(focus, { target: { value: "正常关注点" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.change(focus, { target: { value: "问".repeat(501) } });
    expect(screen.getByRole("alert")).toHaveTextContent("请删减至 500 字符以内");
    expect(focus).toHaveAttribute("aria-describedby", "focus-question-help focus-question-error");
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

    await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await user.click(screen.getByRole("button", { name: "选择 Alphabet" }));
    expect(await screen.findByRole("button", { name: "正在确认研究对象" })).toBeVisible();

    const input = screen.getByLabelText("搜索公司或证券");
    await user.clear(input);
    await user.type(input, "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await act(async () => {
      delayedPreview.resolve(json(preview()));
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "开始 AI 公司研究" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "确认研究对象" })).not.toBeInTheDocument();
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

    expect(await screen.findByRole("heading", { name: "开始 AI 公司研究" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "选择 Alphabet" })).not.toBeInTheDocument();
    delayedSearch.resolve(json({ ...dto, items: objects }));
    const results = await screen.findByRole("region", { name: "对象搜索结果" });
    expect(results).toHaveTextContent("GOOGL");
    expect(results).toHaveTextContent("GOOG");
    expect(requests[0]?.url).toContain("query=US%3AALPHABET%3ACOMPANY");

    await user.click(screen.getByRole("button", { name: "选择 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
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
    await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    const related = await screen.findByRole("region", { name: "行业相关公司" });
    expect(related).toHaveTextContent("Alphabet Inc.");
    expect(related).toHaveTextContent("GOOGL Class A；GOOG Class C");
    const browseRequest = product.requests.find((request) => request.url.includes(`/product/industries/${ids.industry}/companies?`));
    expect(browseRequest?.url).toContain(`as_of=`);
    expect(screen.queryByRole("button", { name: /研究 互联网平台/ })).not.toBeInTheDocument();

    await user.click(within(related).getByRole("button", { name: "选择 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
    const previewRequest = product.requests.find((request) => request.url.endsWith("/company-research/preview"));
    expect(JSON.parse(String(previewRequest?.init?.body))).toMatchObject({ company_id: ids.company });
  });

  it("shows an empty industry browse result and retries a failed industry browse", async () => {
    const user = userEvent.setup();
    const product = server({ industryItems: [], loseFirstIndustryBrowse: true });
    vi.stubGlobal("fetch", product.fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接投资研究服务");
    await user.click(screen.getByRole("button", { name: "重试相关公司" }));
    expect(await screen.findByText("该行业暂未配置可研究公司。")).toBeVisible();
    expect(product.requests.filter((request) => request.url.includes(`/product/industries/${ids.industry}/companies?`))).toHaveLength(2);
  });

  it("shows a browseable Company without Securities but does not allow research to start", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ industryItems: [industryCompanyItems[0]] }).fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    const related = await screen.findByRole("region", { name: "行业相关公司" });
    expect(related).toHaveTextContent("Alphabet Inc.");
    expect(related).toHaveTextContent("尚无在该时点有效的关联证券；不能建立研究。");
    expect(within(related).getByRole("button", { name: "选择 Alphabet" })).toBeDisabled();
  });

  it("keeps an empty Company group browseable before a researchable Company group", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ industryItems: [industryCompanyWithoutSecurity, ...industryCompanyItems] }).fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司或证券"), "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });

    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    const related = await screen.findByRole("region", { name: "行业相关公司" });
    expect(related).toHaveTextContent("Private Company");
    expect(related).toHaveTextContent("Alphabet Inc.");
    expect(within(related).getByRole("button", { name: "选择 Private Company" })).toBeDisabled();

    await user.click(within(related).getByRole("button", { name: "选择 Alphabet" }));
    expect(await screen.findByRole("heading", { name: "确认研究对象" })).toBeVisible();
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
    const input = await screen.findByLabelText("搜索公司或证券");
    await user.type(input, "Google");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await screen.findByRole("region", { name: "对象搜索结果" });
    await user.click(screen.getByRole("button", { name: "查看相关公司" }));
    await user.clear(input);
    await user.type(input, "GOOGL");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    await act(async () => { delayedBrowse.resolve(json({ ...dto, industry_id: ids.industry, items: industryCompanyItems })); await Promise.resolve(); });

    expect(screen.queryByRole("region", { name: "行业相关公司" })).not.toBeInTheDocument();
  });

  it("announces empty search results", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", server({ searchItems: [] }).fetch);
    renderPage();
    await user.type(await screen.findByLabelText("搜索公司或证券"), "missing");
    await user.click(screen.getByRole("button", { name: "搜索对象" }));
    expect(await screen.findByText(/没有找到匹配/)).toBeVisible();
  });
});
